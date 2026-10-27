import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from memory_graph import MemoryGraph
from pydantic import ValidationError

from backend.app.learning import (GRAPH_EDGES, LessonError, dijkstra, dijkstra_lesson,
                                  mean_value_lesson, mean_value_points, path_to, plan, polynomial,
                                  projection, vector_projection)
from backend.app.main import create_app
from backend.app.scene import (Arrow, Create, Event, Node, Patch, Remove, Replace,
                               SceneState, decode_event, export_schema)

TOKEN = "test-credential-which-is-at-least-24-characters"
OTHER = "other-credential-which-is-at-least-24-characters"


@pytest.fixture
def system(tmp_path):
    with MemoryGraph.open(f"sqlite:///{tmp_path / 'memory.db'}") as graph:
        a = graph.god.add_concept("Vectors").id
        b = graph.god.add_concept("Dot product").id
        graph.god.connect(a, b, similarity=8, provenance={"source": "test"})
        human = graph.create_human()
        other = graph.create_human()
        human.add_concept(a, understanding=9)
        human.add_concept(b, understanding=3)
        human.connect(a, b, familiarity=5)
        app = create_app(graph, {TOKEN: human.human_id, OTHER: other.human_id})
        with TestClient(app) as client:
            yield client, graph, human, a, b


def headers(token=TOKEN):
    return {"Authorization": "Bearer " + token}


def test_health_and_identity(system):
    client, graph, human, a, b = system
    assert client.get("/api/health").json()["epsilon"] == graph.methodology.epsilon
    assert client.post("/api/lessons").status_code == 401
    assert client.post(f"/api/users/{human.human_id}/learning-path", headers=headers(OTHER),
                       json={"sources": [a], "target": b}).status_code == 403


def test_lesson_turn_scope_and_conflict(system):
    client, *_ = system
    lesson = client.post("/api/lessons", headers=headers()).json()["lesson_id"]
    result = client.post(f"/api/lessons/{lesson}/turns", headers=headers(),
                         json={"text": "Explain dot product", "expected_epoch": 0})
    assert result.status_code == 200, result.text
    body = result.json()
    assert len(body["plan"]["beats"]) == 3
    assert body["events"][0]["seq"] == 1
    assert client.post(f"/api/lessons/{lesson}/turns", headers=headers(),
                       json={"text": "vector", "expected_epoch": 0}).status_code == 409
    assert client.get(f"/api/lessons/{lesson}/snapshot", headers=headers(OTHER)).status_code == 404
    assert client.post(f"/api/lessons/{lesson}/turns", headers=headers(),
                       json={"text": "x" * 20000, "expected_epoch": 1}).status_code == 413


def test_graph_exact_formula_history_and_invalidation(system):
    client, graph, human, a, b = system
    url = f"/api/users/{human.human_id}"
    def path():
        response = client.post(url + "/learning-path", headers=headers(),
                               json={"sources": [a], "target": b})
        assert response.status_code == 200, response.text
        return response.json()
    first = path()
    assert first["total_cost"] == pytest.approx(1 / (96 + graph.methodology.epsilon))
    # Another writer changes universal similarity: cached personal paths must invalidate too.
    graph.god.assess_similarity(a, b, 4, provenance={"source": "test"})
    assert path()["total_cost"] > first["total_cost"]
    response = client.post(url + "/assessments", headers=headers(), json={
        "concept_id": b, "score": 8.0, "expected_revision": human.revision,
        "idempotency_key": "assessment-1"})
    assert response.status_code == 200, response.text
    assert path()["total_cost"] == pytest.approx(1 / (4 * 17 + graph.methodology.epsilon))
    history = client.get(url + f"/concepts/{b}/history", headers=headers()).json()
    assert len(history) == 2


def test_websocket_interrupt_reconnect_and_bad_commands(system):
    client, *_ = system
    lesson = client.post("/api/lessons", headers=headers()).json()["lesson_id"]
    endpoint = f"/ws/scenes?lesson_id={lesson}"
    with client.websocket_connect(endpoint, headers={"origin": "http://localhost:5173"}) as ws:
        ws.send_json({"token": TOKEN})
        assert ws.receive_json()["snapshot"]["seq"] == 0
        ws.send_json({"type": "turn", "text": "dot product", "expected_epoch": 0})
        assert ws.receive_json()["turn_epoch"] == 1
        ws.send_json({"type": "interrupt", "expected_epoch": 1})
        snapshot = ws.receive_json()["snapshot"]
        assert snapshot["turn_epoch"] == 2 and len(snapshot["nodes"]) == 6
        ws.send_json({"type": "turn", "text": "dot product", "expected_epoch": 1})
        assert ws.receive_json()["type"] == "error"
        ws.send_text("[]")
        assert ws.receive_json()["type"] == "error"
    with client.websocket_connect(endpoint, headers={"origin": "http://localhost:5173"}) as ws:
        ws.send_json({"token": TOKEN})
        assert ws.receive_json()["snapshot"] == snapshot


def test_websocket_auth_and_origin_isolation(system):
    from starlette.websockets import WebSocketDisconnect
    client, *_ = system
    lesson = client.post("/api/lessons", headers=headers()).json()["lesson_id"]
    endpoint = f"/ws/scenes?lesson_id={lesson}"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(endpoint, headers={"origin": "https://evil.example"}):
            pass
    for token in [OTHER, "invalid"]:
        with client.websocket_connect(endpoint, headers={"origin": "http://localhost:5173"}) as ws:
            ws.send_json({"token": token})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


def test_assessment_retry_and_revision_conflict(system):
    client, graph, human, a, b = system
    endpoint = f"/api/users/{human.human_id}/assessments"
    request = {"concept_id": b, "score": 6.0, "expected_revision": human.revision,
               "idempotency_key": "retry-1"}
    first = client.post(endpoint, headers=headers(), json=request)
    assert first.status_code == 200
    assert client.post(endpoint, headers=headers(), json=request).status_code == 409
    request["expected_revision"] = human.revision
    retried = client.post(endpoint, headers=headers(), json=request)
    assert retried.status_code == 200
    assert retried.json()["assessment_id"] == first.json()["assessment_id"]
    assert len(graph.history(kind="understanding", human_id=human.human_id, concept=b)) == 2


def test_demo_does_not_reseed_existing_database(tmp_path):
    from backend.tools.demo import seed
    path = str(tmp_path / "demo.db")
    user = seed(path)
    with MemoryGraph.open(f"sqlite:///{path}") as graph:
        before = graph.god.revision
    assert seed(path) == user
    with MemoryGraph.open(f"sqlite:///{path}") as graph:
        assert graph.god.revision == before


def event(state, operation, **changes):
    values = dict(event_id=f"event_{state.seq + 1}", lesson_id=state.lesson_id,
                  scene_id=state.scene_id, turn_id="turn", utterance_id="utterance",
                  turn_epoch=state.epoch, seq=state.seq + 1, base_revision=state.revision,
                  operation=operation)
    return Event(**(values | changes))


def test_scene_validation_and_atomic_recovery():
    state = SceneState("lesson")
    state.epoch = 1
    node = Node(id="arrow", content=Arrow(end=[2.0, 3.0]))
    first = event(state, Create(node=node))
    assert state.apply(first)
    assert not state.apply(first)
    before = deepcopy(state.snapshot())
    for bad in [event(state, Create(node=node)),
                event(state, Patch(node=Node(id="missing", content=node.content))),
                event(state, Remove(object_id="arrow"), turn_epoch=2),
                event(state, Remove(object_id="arrow"), base_revision=0),
                event(state, Replace(nodes=[node, node]))]:
        with pytest.raises(ValueError):
            state.apply(bad)
        assert state.snapshot() == before
    assert state.apply(event(state, Remove(object_id="arrow")))
    assert state.apply(event(state, Remove(object_id="arrow")))


@pytest.mark.parametrize("bad", [
    {"primitive": "vector_arrow", "end": [float("nan"), 1]},
    {"primitive": "vector_arrow", "end": [True, 1]},
    {"primitive": "vector_arrow", "end": [1, 2], "code": "evil"},
    {"primitive": "text_label", "text": "<script>evil</script>"},
    {"primitive": "line_curve", "points": [[0, 0]] * 129},
    {"primitive": "shader", "url": "https://evil"},
])
def test_untrusted_scene_rejected(bad):
    with pytest.raises(ValidationError):
        Node.model_validate({"id": "test", "content": bad})


def test_schema_export_and_event_size():
    state = SceneState("lesson")
    state.epoch = 1
    sample = event(state, Replace(nodes=plan("dot product").nodes)).model_dump()
    schema = export_schema()
    Draft202012Validator(schema).validate(sample)
    checked_in = json.loads((Path(__file__).parents[2] / "shared/schemas/scene.v1.schema.json").read_text())
    assert checked_in == schema
    assert decode_event(json.dumps(sample)).seq == 1
    with pytest.raises(ValueError):
        decode_event(" " * 16385)


def test_500_scene_replacements_remain_bounded():
    state = SceneState("lesson")
    state.epoch = 1
    for _ in range(500):
        state.apply(event(state, Replace(nodes=plan("dot product").nodes)))
    assert len(state.nodes) == 6 and len(state.seen) == 256
    assert state.revision == 500


def test_projection_known_values_and_fallback():
    assert projection([2, 3], [3, 0]) == pytest.approx([2, 0])
    assert projection([1, 0], [1, 1]) == pytest.approx([0.5, 0.5])
    with pytest.raises(ValueError):
        projection([1, 1], [0, 0])
    assert plan("unknown topic").recipe_id == "whiteboard_v1"


def test_dijkstra_known_values_and_errors():
    distance, previous, order, trace = dijkstra(GRAPH_EDGES, "A")
    assert distance == {"A": 0, "B": 3, "C": 2, "D": 8, "E": 11}
    assert order == ["A", "C", "B", "D", "E"]
    assert path_to(previous, "A", "E") == ["A", "C", "B", "D", "E"]
    assert trace[1] == ("C", 2, [("B", 4, 3, 1), ("D", None, 10, 8), ("E", None, 12, 10)])
    # Cross-check against independent Bellman-Ford relaxation.
    expected = {node: float("inf") for edge in GRAPH_EDGES for node in edge[:2]} | {"A": 0}
    for _ in expected:
        for a, b, weight in GRAPH_EDGES:
            expected[a] = min(expected[a], expected[b] + weight)
            expected[b] = min(expected[b], expected[a] + weight)
    assert expected == distance
    with pytest.raises(ValueError):
        dijkstra([("A", "B", -1)], "A")
    with pytest.raises(ValueError):
        dijkstra(GRAPH_EDGES, "Z")
    _, previous, _, _ = dijkstra(GRAPH_EDGES + [("X", "Y", 1)], "A")
    with pytest.raises(ValueError):
        path_to(previous, "A", "X")


def test_mean_value_points_known_values():
    points, slope = mean_value_points([0, 0, 0, 0.125], 0.0, 3.0)
    assert points == pytest.approx([3 ** 0.5]) and slope == pytest.approx(1.125)
    points, slope = mean_value_points([0, 0, 1, 0], 1.0, 5.0)
    assert points == pytest.approx([3.0]) and slope == pytest.approx(6.0)
    points, _ = mean_value_points([0, -3, 0, 1], -2.0, 2.0)
    assert points == pytest.approx([-2 / 3 ** 0.5, 2 / 3 ** 0.5])
    assert mean_value_points([1, 2, 0, 0], 0.0, 1.0) == ([], pytest.approx(2.0))


def test_mean_value_points_property():
    import random
    rng = random.Random(7)
    for _ in range(2000):
        coefficients = [rng.uniform(-10, 10) for _ in range(4)]
        a = rng.uniform(-4.5, 4.0)
        b = rng.uniform(a + 0.5, 4.5)
        points, slope = mean_value_points(coefficients, a, b)
        assert points, "a non-linear polynomial always has a mean value point"
        for c in points:
            derivative = coefficients[1] + 2 * coefficients[2] * c + 3 * coefficients[3] * c * c
            assert a < c < b and derivative == pytest.approx(slope, rel=1e-6, abs=1e-6)
        assert slope == pytest.approx((polynomial(coefficients, b) - polynomial(coefficients, a)) / (b - a))


@pytest.mark.parametrize("text,recipe", [
    ("Explain dot product", "vector_projection_v1"),
    ("Explain Dijkstra's algorithm", "dijkstra_v1"),
    ("how does dijktra work", "dijkstra_v1"),
    ("shortest path in a graph", "dijkstra_v1"),
    ("Explain the Mean Value Theorem", "mean_value_v1"),
    ("MVT", "mean_value_v1"),
])
def test_recipes_are_valid_bounded_scenes(text, recipe):
    lesson = plan(text)
    assert lesson.recipe_id == recipe
    ids = {node.id for node in lesson.nodes}
    assert all(focus in ids for beat in lesson.beats for focus in beat.focus_ids)
    state = SceneState("lesson")
    state.epoch = 1
    sample = event(state, Replace(nodes=lesson.nodes))
    Draft202012Validator(export_schema()).validate(sample.model_dump())
    assert state.apply(sample) and len(state.nodes) == len(lesson.nodes)


def test_new_lessons_over_websocket(system):
    client, *_ = system
    lesson = client.post("/api/lessons", headers=headers()).json()["lesson_id"]
    with client.websocket_connect(f"/ws/scenes?lesson_id={lesson}",
                                  headers={"origin": "http://localhost:5173"}) as ws:
        ws.send_json({"token": TOKEN})
        ws.receive_json()
        for epoch, (text, recipe) in enumerate([("Explain Dijkstra", "dijkstra_v1"),
                                                ("mean value theorem", "mean_value_v1")]):
            ws.send_json({"type": "turn", "text": text, "expected_epoch": epoch})
            body = ws.receive_json()
            assert body["type"] == "lesson.turn" and body["plan"]["recipe_id"] == recipe
    for recipe, bindings in [("dijkstra_v1", []), ("vector_projection_v1", ["projection.angle",
                                                                             "projection.length"])]:
        manifest = client.get(f"/api/recipes/{recipe}/manifest", headers=headers()).json()
        assert manifest["local_bindings"] == bindings


def voice_app(tmp_path, voice):
    graph = MemoryGraph.open(f"sqlite:///{tmp_path / 'voice.db'}")
    human = graph.create_human()
    return graph, create_app(graph, {TOKEN: human.human_id}, voice=voice)


def test_voice_disabled_keeps_text_mode(tmp_path):
    graph, app = voice_app(tmp_path, None)
    with graph, TestClient(app) as client:
        assert client.get("/api/health").json()["voice"] == "text_only"
        assert client.post("/api/voice/sessions").status_code == 401
        assert client.post("/api/voice/sessions", headers=headers()).status_code == 503


def test_voice_credential_is_minted_server_side(tmp_path):
    import httpx
    from backend.app.voice import OpenAIRealtime
    seen = []

    def provider(request):
        seen.append(request)
        if len(seen) > 1:
            return httpx.Response(500, json={"error": {"message": "echoes sk-secret config"}})
        return httpx.Response(200, json={"value": "ek_test", "expires_at": 123, "session": {}})

    voice = OpenAIRealtime("sk-secret", transport=httpx.MockTransport(provider))
    graph, app = voice_app(tmp_path, voice)
    with graph, TestClient(app) as client:
        assert client.get("/api/health").json()["voice"] == "realtime"
        body = client.post("/api/voice/sessions", headers=headers()).json()
        assert body == {"provider": "openai", "model": "gpt-realtime-2.1", "client_secret": "ek_test",
                        "expires_at": 123, "sdp_url": "https://api.openai.com/v1/realtime/calls"}
        failed = client.post("/api/voice/sessions", headers=headers())
        assert failed.status_code == 502 and "sk-secret" not in failed.text
    request = seen[0]
    assert str(request.url) == "https://api.openai.com/v1/realtime/client_secrets"
    assert request.headers["authorization"] == "Bearer sk-secret"
    session = json.loads(request.content)["session"]
    assert session["type"] == "realtime" and session["audio"]["output"]["voice"] == "marin"
    assert [tool["name"] for tool in session["tools"]] == ["tutor"]
    assert session["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad", "eagerness": "low", "create_response": True, "interrupt_response": True}
    assert session["audio"]["input"]["noise_reduction"] == {"type": "near_field"}


def test_voice_provider_unreachable(tmp_path):
    import httpx
    from backend.app.voice import OpenAIRealtime

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    graph, app = voice_app(tmp_path, OpenAIRealtime("sk-x", transport=httpx.MockTransport(offline)))
    with graph, TestClient(app) as client:
        assert client.post("/api/voice/sessions", headers=headers()).status_code == 502


def test_voice_from_env(monkeypatch):
    from backend.app.voice import OpenAIRealtime
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert OpenAIRealtime.from_env() is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("LUCID_VOICE_MODEL", "gpt-realtime")
    assert OpenAIRealtime.from_env().model == "gpt-realtime"
    monkeypatch.setenv("LUCID_VOICE_EAGERNESS", "medium")
    monkeypatch.setenv("LUCID_VOICE_NOISE_REDUCTION", "off")
    voice = OpenAIRealtime.from_env()
    assert voice.session_config()["audio"]["input"]["noise_reduction"] is None
    assert voice.session_config()["audio"]["input"]["turn_detection"]["eagerness"] == "medium"
    monkeypatch.setenv("LUCID_VOICE_EAGERNESS", "sometimes")
    with pytest.raises(ValueError):
        OpenAIRealtime.from_env()


@pytest.mark.parametrize("build,message", [
    (lambda: vector_projection((9, 0), (1, 1)), "between"),
    (lambda: vector_projection((0, 0), (1, 1)), "length"),
    (lambda: dijkstra_lesson(["A", "B", "C"], [("A", "B", 1)], "A", "C"), "unreachable"),
    (lambda: dijkstra_lesson(["A", "B"], [("A", "B", 0)], "A", "B"), "weights"),
    (lambda: dijkstra_lesson(["A", "B"], [("A", "B", 1), ("B", "A", 2)], "A", "B"), "one edge"),
    (lambda: dijkstra_lesson(["A<", "B"], [("A<", "B", 1)], "A<", "B"), "names"),
    (lambda: dijkstra_lesson(list("ABCDEFG"), [(a, b, 1) for a, b in
                             ["AB", "BC", "CD", "DE", "EF", "FG", "GA", "AC", "BD"]], "A", "D"), "objects"),
    (lambda: mean_value_lesson((0, 0, 0, 1), 0, 3), "fit the view"),
    (lambda: mean_value_lesson((0, 0, 0, 1), 1, 1.2), "b - a"),
    (lambda: mean_value_lesson((0, 0, 11, 0), 0, 1), "Coefficients"),
])
def test_builders_reject_unteachable_parameters(build, message):
    with pytest.raises(LessonError, match=message):
        build()


def test_generated_lessons_are_consistent_scenes():
    lessons = [dijkstra_lesson(["S", "X", "Y", "Z", "T"], [("S", "X", 2), ("S", "Y", 5), ("X", "Y", 1),
                                ("Y", "Z", 2), ("Z", "T", 1), ("Y", "T", 9)], "S", "T"),
               mean_value_lesson((0, -3, 0, 1), -2, 2), mean_value_lesson((1, 0.5, 0, 0), -2, 2),
               vector_projection((2, 1), (-1, 3))]
    for lesson in lessons:
        ids = {node.id for node in lesson.nodes}
        assert all(focus in ids for beat in lesson.beats for focus in beat.focus_ids)
        state = SceneState("lesson")
        state.epoch = 1
        assert state.apply(event(state, Replace(nodes=lesson.nodes)))
    assert "S → X → Y → Z → T" in lessons[0].beats[-1].say_text
    assert "Two points work" in lessons[1].beats[-1].say_text
    assert "every c" in lessons[2].beats[-1].say_text


def test_turn_planning_runs_outside_the_lock_and_rechecks_epoch(tmp_path):
    import threading
    from backend.app.service import TutorService
    graph = MemoryGraph.open(f"sqlite:///{tmp_path / 'plan.db'}")
    human = graph.create_human()
    entered, release = threading.Event(), threading.Event()

    def slow_planner(text):
        entered.set()
        assert release.wait(5)
        return plan(text)

    service = TutorService(graph, slow_planner)
    lesson = service.create(human.human_id)["lesson_id"]
    errors = []
    worker = threading.Thread(target=lambda: errors.append(
        pytest.raises(Exception, service.turn, human.human_id, lesson, "dijkstra", 0)))
    worker.start()
    assert entered.wait(5)
    # The lock is free while planning: other lessons and interruptions proceed.
    assert service.create(human.human_id)["seq"] == 0
    service.interrupt(human.human_id, lesson, 0)
    release.set()
    worker.join(5)
    assert "changed while planning" in str(errors[0].value)
    assert service.owned(human.human_id, lesson).seq == 0
    graph.close()


def claude_client(*replies):
    """Real Anthropic SDK over a mock transport; each reply is a choice dict, status int or exception."""
    import anthropic
    import httpx2
    seen, queue = [], list(replies)

    def handler(request):
        seen.append(json.loads(request.content))
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, int):
            return httpx2.Response(reply, json={"type": "error", "error": {"type": "api_error", "message": "x"}})
        stop = reply.pop("stop_reason", "end_turn")
        content = [] if stop == "refusal" else [{"type": "text", "text": json.dumps(reply)}]
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = anthropic.Anthropic(api_key="sk-test", max_retries=0, http_client=anthropic.DefaultHttpxClient(
        transport=httpx2.MockTransport(handler)))
    return client, seen


def choice(recipe, **params):
    return {"recipe": recipe, "vector_projection": None, "dijkstra": None, "mean_value": None, **params}


GRAPH = {"nodes": ["S", "X", "T"], "edges": [{"a": "S", "b": "X", "weight": 1},
                                             {"a": "X", "b": "T", "weight": 2},
                                             {"a": "S", "b": "T", "weight": 5}], "source": "S", "target": "T"}


def test_claude_planner_builds_verified_lesson_from_parameters():
    from backend.app.planner import ClaudePlanner
    client, seen = claude_client(choice("dijkstra", dijkstra=GRAPH))
    lesson = ClaudePlanner(client).plan("shortest path from S to T")
    assert lesson.recipe_id == "dijkstra_v1" and lesson.planned_by == "claude"
    assert lesson.beats[-1].say_text == "T is settled at distance 3. Shortest path: S → X → T."
    request = seen[0]
    assert request["model"] == "claude-opus-5-5" and request["fallbacks"] == "default"
    assert request["output_config"]["effort"] == "low"
    assert request["output_config"]["format"]["schema"]["properties"]["recipe"]["enum"] == [
        "vector_projection", "dijkstra", "mean_value", "complex_quadratic", "none"]
    assert request["messages"] == [{"role": "user", "content": "shortest path from S to T"}]


def test_claude_planner_retries_once_with_the_validation_error():
    from backend.app.planner import ClaudePlanner
    bad = choice("mean_value", mean_value={"coefficients": [0, 0, 0, 1], "a": 0, "b": 3})
    good = choice("mean_value", mean_value={"coefficients": [0, -3, 0, 1], "a": -2, "b": 2})
    client, seen = claude_client(bad, good)
    lesson = ClaudePlanner(client).plan("mean value theorem for x cubed")
    assert lesson.planned_by == "claude" and "Two points work" in lesson.beats[-1].say_text
    retry = seen[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert "fit the view" in retry[2]["content"]


@pytest.mark.parametrize("replies", [
    (choice("mean_value", mean_value={"coefficients": [0, 0, 0, 9], "a": 0, "b": 3}),) * 2,
    (choice("dijkstra"),) * 2,                     # recipe without its parameters
    ({"stop_reason": "refusal"},),
    (500,),
    ({"recipe": "shader", "vector_projection": None, "dijkstra": None, "mean_value": None},),
])
def test_claude_planner_falls_back_to_authored_lessons(replies):
    from backend.app.planner import ClaudePlanner
    client, _ = claude_client(*[dict(r) if isinstance(r, dict) else r for r in replies])
    lesson = ClaudePlanner(client).plan("explain the mean value theorem")
    assert lesson.recipe_id == "mean_value_v1" and lesson.planned_by == "authored"


def test_claude_planner_none_uses_keyword_router():
    from backend.app.planner import ClaudePlanner
    client, _ = claude_client(choice("none"), choice("none"))
    assert ClaudePlanner(client).plan("tell me about cats").recipe_id == "whiteboard_v1"
    assert ClaudePlanner(client).plan("vector please").recipe_id == "vector_projection_v1"


def test_planner_wired_into_app(tmp_path):
    from backend.app.planner import ClaudePlanner
    client, _ = claude_client(choice("vector_projection", vector_projection={"u": [2, 1], "v": [-1, 3]}))
    graph = MemoryGraph.open(f"sqlite:///{tmp_path / 'planner.db'}")
    human = graph.create_human()
    app = create_app(graph, {TOKEN: human.human_id}, voice=None, planner=ClaudePlanner(client))
    with graph, TestClient(app) as http:
        assert http.get("/api/health").json()["planner"] == "claude"
        lesson = http.post("/api/lessons", headers=headers()).json()["lesson_id"]
        body = http.post(f"/api/lessons/{lesson}/turns", headers=headers(),
                         json={"text": "project (-1, 3) onto (2, 1)", "expected_epoch": 0}).json()
        assert body["plan"]["planned_by"] == "claude"
        assert body["plan"]["beats"][1]["say_text"] == "Project v onto u: its shadow is (0.4, 0.2)."


def test_planner_from_env(monkeypatch):
    from backend.app.planner import ClaudePlanner
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ClaudePlanner.from_env() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert ClaudePlanner.from_env().model == "claude-opus-5-5"


# ---- learning goals -----------------------------------------------------------------------
from backend.app.goals import KNOWN, LINKS, TOPICS, Curriculum, GoalError, Goals  # noqa: E402


@pytest.fixture
def goals(tmp_path):
    graph = MemoryGraph.open(f"sqlite:///{tmp_path / 'goals.db'}")
    curriculum = Curriculum().sync(graph)
    yield graph, curriculum, Goals(graph, curriculum, plan)
    graph.close()


def diagnose(goals_, human, text, answers_by_name):
    start = goals_.start(human.human_id, text)
    answers = [(q["concept_id"], answers_by_name.get(q["name"])) for q in start["questions"]]
    return start, goals_.diagnose(human.human_id, start["target"]["id"], answers, start["revision"], "key1")


def test_curriculum_sync_is_idempotent_and_reuses_existing_concepts(tmp_path):
    graph = MemoryGraph.open(f"sqlite:///{tmp_path / 'sync.db'}")
    existing = graph.god.add_concept("Derivatives").id
    first = Curriculum().sync(graph)
    revision = graph.god.revision
    second = Curriculum().sync(graph)
    assert graph.god.revision == revision and first.ids == second.ids
    assert first.ids["derivatives"] == existing
    assert first.ids["vectors"] == "00000000-0000-0000-0000-000000000001"  # demo seed ID
    assert len(first.ids) == len(TOPICS)
    for a, b, _ in LINKS:
        assert first.ids[b] in graph.god.neighbors(first.ids[a])
    graph.close()


def test_novice_route_starts_from_foundations_and_uses_engine_costs(goals):
    graph, curriculum, goals_ = goals
    human = graph.create_human()
    start, route = diagnose(goals_, human, "I want to learn the mean value theorem", {})
    assert [q["name"] for q in start["questions"]][0] == "Functions"  # foundations first
    assert len(start["questions"]) == 6 and "choices" in start["questions"][0]
    assert "answer" not in json.dumps(start)  # the key never leaves the server
    assert [s["name"] for s in route["steps"]] == ["Functions", "Slope and secant lines", "Derivatives",
                                                   "Mean value theorem"]
    assert route["next"] == curriculum.ids["functions"] and route["formula_id"] == "baseline_v1"
    # Baseline cost: 1 / (s * (u_a + u_b) + eps), every unsure answer scored 1.
    eps = graph.methodology.epsilon
    assert route["total_cost"] == pytest.approx(1 / (8 * 2 + eps) + 1 / (9 * 2 + eps) + 1 / (9 * 2 + eps))


def test_route_starts_from_known_concepts_and_skips_known_target(goals):
    graph, curriculum, goals_ = goals
    human = graph.create_human()
    _, route = diagnose(goals_, human, "mvt", {"Functions": 0, "Derivatives": 0, "Limits": 1})
    assert [s["name"] for s in route["steps"]] == ["Derivatives", "Mean value theorem"]
    assert route["steps"][0]["known"] and route["steps"][0]["understanding"] == KNOWN
    expert = graph.create_human()
    _, route = diagnose(goals_, expert, "mvt", {"Mean value theorem": 0})
    assert route["status"] == "known" and route["next"] is None


def test_progress_expands_the_graph_and_moves_the_route(goals):
    graph, curriculum, goals_ = goals
    human = graph.create_human()
    _, route = diagnose(goals_, human, "mean value theorem", {})
    target = route["target"]["id"]
    previous, learned = None, []
    while route["next"] and len(learned) < 10:
        step = route["next"]
        goals_.progress(human.human_id, step, 9, previous, route["revision"], f"p{len(learned)}")
        previous, route = step, goals_.route(human.human_id, target)
        learned.append(curriculum.by_id[step].name)
    # Mastering a step never lets the route skip a prerequisite.
    assert learned == ["Functions", "Slope and secant lines", "Derivatives", "Mean value theorem"]
    assert route["status"] == "known"
    history = graph.history(kind="understanding", human_id=human.human_id, concept=target)
    assert [h["method"] for h in history][-1] == "lesson_self_report"
    # A rating on a concept outside the diagnosed region adds it and links it to known neighbours.
    vectors = curriculum.ids["vectors"]
    goals_.progress(human.human_id, vectors, 8, None, graph.human(human.human_id).revision, "v1")
    assert graph.human(human.human_id).snapshot().nodes[vectors].understanding == 8
    # A returning learner is only asked about concepts not yet in their graph.
    assert [q["name"] for q in goals_.start(human.human_id, "dot product")["questions"]] == [
        "Vector length", "Angles and cosine", "Dot product", "Vector projection"]


def test_goal_errors_and_stale_revision(goals):
    from memory_graph import Conflict
    graph, curriculum, goals_ = goals
    human = graph.create_human()
    with pytest.raises(GoalError, match="Try:"):
        goals_.start(human.human_id, "quantum tunnelling")
    start = goals_.start(human.human_id, "dijkstra")
    assert goals_.route(human.human_id, start["target"]["id"])["status"] == "needs_diagnostic"
    with pytest.raises(GoalError):
        goals_.route_lesson(human.human_id, start["target"]["id"])
    answers = [(q["concept_id"], None) for q in start["questions"]]
    with pytest.raises(Conflict):
        goals_.diagnose(human.human_id, start["target"]["id"], answers, start["revision"] + 5, "k")
    assert graph.human(human.human_id).snapshot().nodes == {}  # atomic: nothing partially written


def test_every_concept_lesson_and_route_map_is_a_valid_bounded_scene(goals):
    graph, curriculum, goals_ = goals
    for topic in TOPICS:
        concept_id = curriculum.ids[topic.key]
        lesson = goals_.concept_lesson(concept_id)
        assert lesson.concept_id == concept_id
        assert lesson.recipe_id == ("concept_card_v1" if topic.recipe is None else lesson.recipe_id)
        human = graph.create_human()
        _, route = diagnose(goals_, human, topic.name, {})
        for built in (lesson, goals_.route_lesson(human.human_id, concept_id)):
            ids = {node.id for node in built.nodes}
            assert all(focus in ids for beat in built.beats for focus in beat.focus_ids)
            state = SceneState("lesson")
            state.epoch = 1
            assert state.apply(event(state, Replace(nodes=built.nodes)))


def test_goal_api_flow(tmp_path):
    graph = MemoryGraph.open(f"sqlite:///{tmp_path / 'api.db'}")
    human, other = graph.create_human(), graph.create_human()
    app = create_app(graph, {TOKEN: human.human_id, OTHER: other.human_id}, voice=None, planner=None)
    with graph, TestClient(app) as client:
        me = client.get("/api/me", headers=headers()).json()
        url = f"/api/users/{me['user_id']}"
        assert len(client.get("/api/curriculum", headers=headers()).json()) == len(TOPICS)
        assert client.post(url + "/goals", headers=headers(OTHER), json={"text": "mvt"}).status_code == 403
        missing = client.post(url + "/goals", headers=headers(), json={"text": "quantum"})
        assert missing.status_code == 404 and "Try:" in missing.json()["detail"]
        start = client.post(url + "/goals", headers=headers(), json={"text": "mean value theorem"}).json()
        target = start["target"]["id"]
        body = {"answers": [{"concept_id": q["concept_id"], "choice": 0 if q["name"] == "Functions" else None}
                            for q in start["questions"]],
                "expected_revision": start["revision"] + 1, "idempotency_key": "diag-1"}
        assert client.post(f"{url}/goals/{target}/diagnostic", headers=headers(), json=body).status_code == 409
        body["expected_revision"] = start["revision"]
        route = client.post(f"{url}/goals/{target}/diagnostic", headers=headers(), json=body).json()
        assert route["steps"][0]["name"] == "Functions" and route["steps"][0]["known"]
        assert client.get(f"{url}/goals/{target}/route", headers=headers()).json() == route

        lesson = client.post("/api/lessons", headers=headers()).json()["lesson_id"]
        with client.websocket_connect(f"/ws/scenes?lesson_id={lesson}", headers={"origin": "http://localhost:5173"}) as ws:
            ws.send_json({"token": TOKEN})
            ws.receive_json()
            ws.send_json({"type": "turn", "text": "Show my route", "expected_epoch": 0, "route_to": target})
            mapped = ws.receive_json()
            assert mapped["plan"]["recipe_id"] == "learning_route_v1"
            assert "Functions → Slope and secant lines" in mapped["plan"]["beats"][1]["say_text"]
            ws.send_json({"type": "turn", "text": "Learn next", "expected_epoch": 1, "concept": route["next"]})
            taught = ws.receive_json()
            assert taught["plan"]["concept_id"] == route["next"] and taught["plan"]["recipe_id"] == "diagram_v1"
            ws.send_json({"type": "turn", "text": "explain dot product", "expected_epoch": 2})
            assert ws.receive_json()["plan"]["concept_id"] is not None  # free exploration counts too

        progress = {"concept_id": route["next"], "score": 9, "previous_concept_id": route["steps"][0]["id"],
                    "expected_revision": route["revision"], "idempotency_key": "step-1"}
        assert client.post(url + "/progress", headers=headers(), json=progress).status_code == 200
        after = client.get(f"{url}/goals/{target}/route", headers=headers()).json()
        assert after["next"] != route["next"]


def test_claude_goal_resolver():
    from backend.app.planner import ClaudePlanner
    curriculum = Curriculum()
    curriculum.ids = {t.key: f"id-{t.key}" for t in TOPICS}
    client, seen = claude_client({"topic": "derivatives"}, {"topic": "none"}, 500)
    resolve = ClaudePlanner(client).resolver(curriculum)
    assert resolve("how fast is a curve changing at a point?") == "id-derivatives"
    assert resolve("cats") is None and resolve("anything") is None
    assert "derivatives" in seen[0]["output_config"]["format"]["schema"]["properties"]["topic"]["enum"]


@pytest.mark.parametrize("key", [t.key for t in TOPICS if t.level == 3])
def test_mastery_never_skips_prerequisites(goals, key):
    """Guards curriculum edits: a learner rating each step 10 still climbs level by level."""
    graph, curriculum, goals_ = goals
    human = graph.create_human()
    _, route = diagnose(goals_, human, curriculum.by_id[curriculum.ids[key]].name, {})
    previous, levels = None, []
    while route["next"] and len(levels) < 10:
        step = route["next"]
        goals_.progress(human.human_id, step, 10, previous, route["revision"], f"m{len(levels)}")
        levels.append(curriculum.by_id[step].level)
        previous, route = step, goals_.route(human.human_id, curriculum.ids[key])
    assert levels == sorted(levels) and levels[0] == 0 and levels[-1] == 3


# ---- conversational tutor ---------------------------------------------------------------------
from backend.app.author import Check, ConceptLink, ConceptSpec, Written, grade_locally  # noqa: E402
from backend.app.diagrams import (AUTHORED, DArrow, DCurve, DiagramError, DiagramLesson, DLabel,  # noqa: E402
                                  DSlider, compile_lesson, diagram, DBeat)
from backend.app.tutor import Session, Tutor  # noqa: E402


class FakeAuthor:
    """Stands in for Claude: deterministic explanations, questions, grading and concepts."""
    model = "fake-author"

    def __init__(self, prerequisite=None):
        self.prerequisite, self.calls = prerequisite, []

    def explain(self, concept, description, context, request=None):
        self.calls.append(("explain", concept, request))
        plan = compile_lesson(AUTHORED["functions"], recipe_id="generated_v1", generated=True)
        return Written(plan, Check(question=f"Say yes about {concept}", expected_answer="yes"),
                       self.prerequisite if request else None)

    def question(self, concept, description):
        return Check(question=f"Say yes about {concept}", expected_answer="yes")

    def grade(self, question, expected, answer, choices=None):
        if choices:
            return grade_locally(answer, expected, choices, choices.index(expected))
        return ("correct", "Great.") if "yes" in answer else ("wrong", "Not quite.")

    def concept(self, request, existing):
        return ConceptSpec(name="Chain rule", description="Differentiates a function of a function.",
                           links=[ConceptLink(concept="Derivatives", similarity=8),
                                  ConceptLink(concept="Not a real concept", similarity=9)])


@pytest.fixture
def tutor_env(goals):
    graph, curriculum, goals_ = goals
    human = graph.create_human()

    def make(author=None):
        tutor = Tutor(graph, goals_, plan, author, active_learning=False)
        session = Session()
        return tutor, session, lambda action, text="": tutor.act(session, human.human_id, action, text)
    return graph, curriculum, human, make


def run_check(act, session, correct_for):
    """Answer the starting check: correct only for the named prompts."""
    plans = []
    while session.mode == "diagnosing":
        question = session.question
        plans.append(act("answer", question.expected if any(k in question.prompt for k in correct_for) else "not sure"))
    return plans


def test_tutor_runs_the_whole_loop_without_a_provider(tutor_env):
    graph, curriculum, human, make = tutor_env
    tutor, session, act = make()
    first = act("goal", "I want to learn the mean value theorem")
    assert first.tutor == {"mode": "diagnosing", "awaiting_answer": True, "goal": "Mean value theorem"}
    assert "Is it parallel to the secant" in first.beats[-1].say_text
    plans = run_check(act, session, ["2x + 1"])
    lesson = plans[-1]
    # Taught silently along the cheapest route: no route, cost or map in what the learner hears.
    spoken = " ".join(b.say_text for b in lesson.beats)
    assert "Next, let's look at Slope and secant lines" in spoken and "route" not in spoken.lower()
    assert lesson.tutor["mode"] == "checking" and lesson.beats[-1].say_text.startswith("Quick check:")
    assert any(n.content.primitive == "slider" for n in lesson.nodes)  # every explanation has a diagram
    while session.mode == "checking":
        act("answer", session.question.expected)
    assert session.mode == "done"
    members = graph.human(human.human_id).snapshot().nodes
    target = curriculum.ids["mean_value_theorem"]
    assert members[target].understanding == 8.0
    methods = {h["method"] for h in graph.history(kind="understanding", human_id=human.human_id, concept=target)}
    assert methods == {"voice_diagnostic", "voice_check"}


def test_correct_goal_answer_skips_teaching_and_confusion_is_free(tutor_env):
    graph, curriculum, human, make = tutor_env
    tutor, session, act = make()
    act("goal", "derivatives")
    done = act("answer", "2x")
    assert done.tutor["mode"] == "done" and "You've got Derivatives" in " ".join(b.say_text for b in done.beats)
    tutor, session, act = make()
    act("goal", "mean value theorem")
    run_check(act, session, ["derivative of x²"])  # knows derivatives: go straight to the theorem
    assert session.current == curriculum.ids["mean_value_theorem"]
    for _ in range(5):
        assert act("confused", "why is it parallel?").tutor["awaiting_answer"]
    assert session.attempts == {} and session.mode == "checking"
    for _ in range(3):
        act("answer", "vertical")
    assert session.mode == "done"  # three wrong answers: covered, move on, never loop forever


def test_tutor_detours_to_a_prerequisite_and_creates_missing_concepts(tutor_env):
    graph, curriculum, human, make = tutor_env
    author = FakeAuthor(prerequisite="Chain rule")
    tutor, session, act = make(author)
    act("goal", "derivatives")
    run_check(act, session, [])
    while session.current != curriculum.ids["derivatives"]:
        act("answer", "yes")
    clarified = act("confused", "I don't get how to differentiate nested functions")
    assert "the missing piece is Chain rule" in " ".join(b.say_text for b in clarified.beats)
    chain = graph.god.get_concept("Chain rule")
    assert curriculum.ids["derivatives"] in graph.god.neighbors(chain.id)  # in the shared graph
    assert session.current == chain.id and session.detours == [curriculum.ids["derivatives"]]
    assert clarified.generated and clarified.planned_by == "claude"
    resumed = act("answer", "yes")
    assert "Now, back to Derivatives" in " ".join(b.say_text for b in resumed.beats)
    assert session.current == curriculum.ids["derivatives"]
    proposals = graph.history(kind="similarity", concept=chain.id)
    assert proposals and graph.human(human.human_id).snapshot().nodes[chain.id].understanding == 8.0


def test_tutor_can_build_a_new_goal_concept(tutor_env):
    graph, curriculum, human, make = tutor_env
    tutor, session, act = make(FakeAuthor())
    first = act("goal", "teach me the chain rule")
    assert first.tutor["goal"] == "Chain rule" and "Say yes about Chain rule" in first.beats[-1].say_text
    tutor, session, act = make()
    with pytest.raises(GoalError):
        act("goal", "quantum tunnelling")  # without an author nothing new can be built


def test_free_questions_keep_the_pending_check(tutor_env):
    graph, curriculum, human, make = tutor_env
    tutor, session, act = make()
    act("goal", "vector projection")
    run_check(act, session, ["(1, 2) + (3, 4)"])
    pending = session.question
    answer = act("question", "what is a dot product?")
    assert session.question is pending and answer.beats[-1].say_text.startswith("Whenever you're ready")
    assert act("continue").beats[-1].say_text == pending.spoken


def test_known_goal_and_requests_for_more_keep_teaching_without_regrading(tutor_env):
    graph, curriculum, human, make = tutor_env
    target = curriculum.ids["derivatives"]
    human.add_concept(target, understanding=9, method="test")
    tutor, session, act = make(FakeAuthor())
    before = human.revision
    first = act("goal", "derivatives")
    assert first.tutor["mode"] == "checking" and first.concept_id == target
    for action, text in [("answer", "I want to learn more"), ("review", ""), ("deeper", "")]:
        result = act(action, text)
        assert result.tutor["awaiting_answer"] and result.concept_id == target
        assert human.revision == before
    assert human.snapshot().nodes[target].understanding == 9


def test_continue_after_completion_reopens_teaching(tutor_env):
    _, curriculum, _, make = tutor_env
    _, session, act = make()
    act("goal", "derivatives")
    act("answer", "2x")
    assert session.mode == "done"
    result = act("continue")
    assert session.mode == "checking" and result.concept_id == curriculum.ids["derivatives"]
    assert any(n.content.primitive == "expr_curve" for n in result.nodes)


def test_new_generated_goal_can_be_assessed_and_routed(tutor_env):
    graph, _, human, make = tutor_env
    _, session, act = make(FakeAuthor())
    act("goal", "teach me the chain rule")
    taught = act("answer", "not sure")
    while session.mode == "diagnosing":
        taught = act("answer", "yes")
    chain = graph.god.get_concept("Chain rule").id
    for _ in range(16):
        if session.current == chain:
            break
        taught = act("answer", session.question.expected)
    assert taught.concept_id == chain and taught.generated
    finished = act("answer", "yes")
    assert finished.tutor["mode"] == "done"
    assert human.snapshot().nodes[chain].understanding == 8


class FactAuthor(FakeAuthor):
    def concept(self, request, existing):
        return ConceptSpec(name="Even functions", description="On a symmetric domain, f(-x) = f(x).",
                           links=[ConceptLink(concept="Functions", similarity=8)])

    def explain(self, concept, description, context, request=None):
        self.context = context
        written = super().explain(concept, description, context, request)
        written.facts = [
            ConceptSpec(name="Symmetry", description="A transformation preserves a figure or function.", links=[]),
            ConceptSpec(name="Even functions", description="On a symmetric domain, f(-x) = f(x).",
                        links=[ConceptLink(concept="Symmetry", similarity=8)]),
            ConceptSpec(name="Odd functions", description="On a symmetric domain, f(-x) = -f(x).",
                        links=[ConceptLink(concept="Symmetry", similarity=8)])]
        return written


def test_free_topic_and_atomic_facts_are_saved_deduplicated_and_reusable(tutor_env):
    graph, curriculum, human, make = tutor_env
    author = FactAuthor()
    tutor, session, act = make(author)
    act("goal", "derivatives")
    pending = session.question
    first = act("question", "Explain even functions")
    even = graph.god.get_concept("Even functions").id
    odd = graph.god.get_concept("Odd functions").id
    symmetry = graph.god.get_concept("Symmetry").id
    assert even != curriculum.ids["functions"] and first.concept_id == even
    assert symmetry in graph.god.neighbors(even) and symmetry in graph.god.neighbors(odd)
    assert session.question is pending  # a side question does not consume the check
    assert author.context["current_concept"] == "Even functions"
    assert even not in human.snapshot().nodes  # exposure cannot fabricate mastery
    revision = graph.god.revision
    act("question", "Explain EVEN FUNCTIONS")
    assert graph.god.revision == revision  # no duplicate nodes or reassessed shared edges
    assert tutor.library.listing(human.human_id) == [{"concept_id": even}]
    other = graph.create_human()
    assert tutor.library.get(other.human_id, even) is None
    # A new tutor instance can reuse its saved geometry with the author unavailable.
    offline, _, _ = make()
    saved, check = offline._lesson(even, human.human_id, Session(target=even))
    assert saved.nodes == first.nodes and check.expected_answer == "yes"


def test_lesson_library_survives_database_reopen(tmp_path):
    from backend.app.library import LessonLibrary
    url = f"sqlite:///{tmp_path / 'lessons.db'}"
    with MemoryGraph.open(url) as graph:
        human = graph.create_human().human_id
        concept = graph.god.add_concept("A topic").id
        lesson = compile_lesson(AUTHORED["functions"])
        LessonLibrary(graph).save(human, concept, lesson, Check(question="Why?", expected_answer="Because"))
    with MemoryGraph.open(url) as graph:
        saved, check = LessonLibrary(graph).get(human, concept)
        assert saved.nodes == lesson.nodes and check.expected_answer == "Because"


def test_exploration_can_go_deeper_and_review_with_provider_unavailable(tutor_env):
    graph, _, human, make = tutor_env
    tutor, session, act = make(FactAuthor())
    explored = act("explore", "even functions")
    assert session.current is None and session.viewed == explored.concept_id
    deeper = act("deeper")
    assert deeper.concept_id == explored.concept_id and deeper.tutor["awaiting_answer"]
    tutor.author = None
    reviewed = act("review")
    assert reviewed.generated and reviewed.nodes == explored.nodes
    assert graph.human(human.human_id).snapshot().nodes == {}


def test_teaching_context_uses_traversal_cost_and_can_bridge_unassessed_nodes(goals):
    graph, curriculum, goals_ = goals
    human = graph.create_human()
    for name in ("Relevant anchor", "Weak anchor", "Unrelated anchor", "Bridge", "Target"):
        graph.god.add_concept(name)
    ids = {name: graph.god.get_concept(name).id for name in
           ("Relevant anchor", "Weak anchor", "Unrelated anchor", "Bridge", "Target")}
    graph.god.connect(ids["Relevant anchor"], ids["Bridge"], similarity=10, provenance={"source": "test"})
    graph.god.connect(ids["Bridge"], ids["Target"], similarity=10, provenance={"source": "test"})
    graph.god.connect(ids["Weak anchor"], ids["Target"], similarity=1, provenance={"source": "test"})
    for name in ("Relevant anchor", "Weak anchor", "Unrelated anchor"):
        human.add_concept(ids[name], understanding=8, method="test")
    before = human.revision
    context = goals_.teaching_context(human.human_id, ids["Target"])
    assert [n["name"] for n in context["teaching_sequence"]] == ["Relevant anchor", "Bridge", "Target"]
    assert "Unrelated anchor" not in [n["name"] for n in context["nearest_concepts"]]
    assert context["teaching_sequence"][-1]["understanding"] is None
    assert human.revision == before
    # Similarity and understanding are inputs; Dijkstra uses the derived traversal cost.
    expected = graph.methodology.cost(10, 1, 1) + graph.methodology.cost(10, 1, 8)
    assert context["teaching_sequence"][0]["distance_to_goal"] == pytest.approx(expected)


def test_library_and_review_api_are_user_scoped(tmp_path):
    graph = MemoryGraph.open(f"sqlite:///{tmp_path / 'review.db'}")
    human, other = graph.create_human(), graph.create_human()
    app = create_app(graph, {TOKEN: human.human_id, OTHER: other.human_id},
                     voice=None, planner=None, author=FactAuthor())
    with graph, TestClient(app) as client:
        lesson = client.post("/api/lessons", headers=headers()).json()["lesson_id"]
        path = f"/api/lessons/{lesson}/tutor"
        first = client.post(path, headers=headers(), json={
            "action": "explore", "text": "Explain even functions", "expected_epoch": 0})
        assert first.status_code == 200
        even = first.json()["plan"]["concept_id"]
        listing = f"/api/users/{human.human_id}/lessons"
        assert client.get(listing, headers=headers()).json() == [{"concept_id": even}]
        assert client.get(listing, headers=headers(OTHER)).status_code == 403
        reviewed = client.post(path, headers=headers(), json={
            "action": "review", "text": "even functions", "expected_epoch": 1})
        assert reviewed.status_code == 200 and reviewed.json()["plan"]["concept_id"] == even


@pytest.mark.parametrize("change,message", [
    (dict(curves=[DCurve(id="f", x="t", y="k*t", t_min=-1, t_max=1, style="primary")]), "undefined parameter"),
    (dict(curves=[DCurve(id="f", x="t", y="50 + t", t_min=-1, t_max=1, style="primary")]), "outside the view"),
    (dict(arrows=[DArrow(id="a", start_x="0", start_y="0", end_x="sqrt(-1)", end_y="1", style="primary")]), "within the view"),
    (dict(sliders=[DSlider(name="t", label="x", min=0, max=1, value=0)]), "other than e or t"),
    (dict(sliders=[DSlider(name="a", label="x", min=0, max=1, value=3)]), "min <= value"),
    (dict(labels=[DLabel(id="x", text="a", x=0, y=0), DLabel(id="x", text="b", x=1, y=1)]), "Duplicate"),
    (dict(labels=[DLabel(id="Bad Id", text="a", x=0, y=0)]), "Element id"),
    (dict(labels=[DLabel(id=f"l{i}", text="a", x=0, y=0) for i in range(30)]), "Too many objects"),
    (dict(curves=[DCurve(id="f", x="t", y="import(t)", t_min=-1, t_max=1, style="primary")]), "Unknown name"),
])
def test_diagram_compiler_rejects_what_it_cannot_draw_faithfully(change, message):
    with pytest.raises(DiagramError, match=message):
        compile_lesson(DiagramLesson(title="x", beats=[DBeat(say="hi", focus=[])], diagram=diagram(**change)))


def test_claude_author_compiles_retries_and_grades():
    from backend.app.author import ClaudeAuthor
    good = {"lesson": AUTHORED["slope"].model_dump(), "check": {"question": "Slope from (0,0) to (2,4)?",
            "expected_answer": "2"}, "prerequisite": None}
    bad = json.loads(json.dumps(good))
    bad["lesson"]["diagram"]["curves"][0]["y"] = "w*t"
    client, seen = claude_client(bad, good, {"verdict": "partly", "feedback": "Close: it is 2."})
    author = ClaudeAuthor(client)
    written = author.explain("Slope", "rise over run", {"goal": "Derivatives"}, request="why rise over run?")
    assert written.plan.generated and written.check.expected_answer == "2"
    assert "undefined parameter" in seen[1]["messages"][2]["content"]
    assert author.grade("Slope?", "2", "about two") == ("partly", "Close: it is 2.")
    assert '"w*t"' not in json.dumps(written.plan.model_dump())


def test_tutor_over_websocket_and_http(tmp_path):
    graph = MemoryGraph.open(f"sqlite:///{tmp_path / 'tutor.db'}")
    human = graph.create_human()
    app = create_app(graph, {TOKEN: human.human_id}, voice=None, planner=None, author=None)
    with graph, TestClient(app) as client:
        assert client.get("/api/health").json()["author"] == "authored"
        lesson = client.post("/api/lessons", headers=headers()).json()["lesson_id"]
        with client.websocket_connect(f"/ws/scenes?lesson_id={lesson}", headers={"origin": "http://localhost:5173"}) as ws:
            ws.send_json({"token": TOKEN})
            ws.receive_json()
            ws.send_json({"type": "tutor", "action": "goal", "text": "derivatives", "expected_epoch": 0})
            asked = ws.receive_json()
            assert asked["plan"]["tutor"]["awaiting_answer"] and asked["plan"]["recipe_id"] == "tutor_card_v1"
            ws.send_json({"type": "tutor", "action": "dance", "text": "", "expected_epoch": 1})
            assert ws.receive_json()["type"] == "error"
        done = client.post(f"/api/lessons/{lesson}/tutor", headers=headers(),
                           json={"action": "answer", "text": "2x", "expected_epoch": 1}).json()
        assert done["plan"]["tutor"]["mode"] == "done"
        missing = client.post(f"/api/lessons/{lesson}/tutor", headers=headers(),
                              json={"action": "goal", "text": "quantum tunnelling", "expected_epoch": 2})
        assert missing.status_code == 404 and "ANTHROPIC_API_KEY" in missing.json()["detail"]
        assert client.delete(f"/api/lessons/{lesson}", headers=headers()).status_code == 204
        assert lesson not in app.state.service.sessions
