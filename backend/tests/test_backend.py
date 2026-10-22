import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from memory_graph import MemoryGraph
from pydantic import ValidationError

from backend.app.learning import plan, projection
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
