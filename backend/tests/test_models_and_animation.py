import hashlib

import pytest
from fastapi.testclient import TestClient
from memory_graph import MemoryGraph
from pydantic import ValidationError

from backend.app.diagrams import (AUTHORED, DArrow, DBeat, DCurve, DiagramError, DiagramLesson, DModel, DMove,
                                  DSlider, compile_lesson, diagram)
from backend.app.learning import vector_projection
from backend.app.main import create_app
from backend.app.models import CATALOG, CatalogModel, ModelError, ModelLibrary
from backend.app.scene import Model3D

TOKEN = "test-credential-which-is-at-least-24-characters"


def lesson(*beats, **parts):
    parts.setdefault("axes", True)
    parts.setdefault("sliders", [DSlider(name="p", label="point", min=-2, max=2, value=0)])
    parts.setdefault("arrows", [DArrow(id="a", start_x="0", start_y="0", end_x="p", end_y="1", style="primary")])
    return DiagramLesson(title="Test", beats=list(beats), diagram=diagram(**parts))


def test_beat_animations_start_where_the_previous_beat_left_off():
    plan = compile_lesson(lesson(DBeat(say="Watch p grow.", focus=["a"], animate=[DMove(param="p", to=2)]),
                                 DBeat(say="Now nothing moves.", focus=[]),
                                 DBeat(say="Watch p shrink.", focus=["a"], animate=[DMove(param="p", to=-1, seconds=5)])))
    moves = [[(m.binding, m.start, m.end, m.seconds) for m in beat.animations] for beat in plan.beats]
    # The quiet middle beat holds p where the first beat left it.
    assert moves == [[("param.p", 0, 2, 3.0)], [("param.p", 2, 2, 0.5)], [("param.p", 2, -1, 5)]]


def test_explicit_start_resets_the_slider_for_that_beat():
    plan = compile_lesson(lesson(DBeat(say="From the left.", focus=[], animate=[DMove(param="p", start=-2, to=1)])))
    assert plan.beats[0].animations[0].start == -2


@pytest.mark.parametrize("moves,message", [
    ([DMove(param="q", to=1)], "not a slider"),
    ([DMove(param="p", to=3)], "outside its slider range"),
    ([DMove(param="p", to=0)], "must change its value"),
    ([DMove(param="p", to=1, seconds=60)], "0.5 to 12 seconds"),
    ([DMove(param="p", to=1), DMove(param="p", to=2)], "animates a slider twice"),
])
def test_animation_compiler_rejects_moves_it_cannot_play(moves, message):
    with pytest.raises(DiagramError, match=message):
        compile_lesson(lesson(DBeat(say="Watch.", focus=[], animate=moves)))


def test_animation_must_keep_the_scene_in_view():
    far = lesson(DBeat(say="Watch it fly away.", focus=[], animate=[DMove(param="p", to=40)]),
                 sliders=[DSlider(name="p", label="x", min=0, max=50, value=1)])
    with pytest.raises(DiagramError, match="moves arrow a out of view"):
        compile_lesson(far)


def test_sliders_without_any_beat_animation_are_rejected():
    with pytest.raises(DiagramError, match="Animate at least one slider"):
        compile_lesson(lesson(DBeat(say="Nothing moves.", focus=["a"])))


def test_every_authored_lesson_with_sliders_animates_what_it_says():
    for name, authored in AUTHORED.items():
        plan = compile_lesson(authored)
        if authored.diagram.sliders:
            assert any(beat.animations for beat in plan.beats), name


def test_projection_recipe_rotates_v_to_no_shadow_and_back():
    beats = vector_projection(u=(3, 0), v=(2, 3)).beats
    first, second = beats[0].animations[0], beats[1].animations[0]
    assert first.binding == "projection.angle" and first.end == 90
    assert (second.start, second.end) == (first.end, first.start)


def test_models_compile_to_moving_catalog_objects():
    plan = compile_lesson(lesson(
        DBeat(say="Watch the rocket rise.", focus=["rocket"], animate=[DMove(param="h", to=3)]),
        sliders=[DSlider(name="h", label="height", min=-3, max=4, value=-3)], arrows=[],
        models=[DModel(id="rocket", model="saturn_v", y="h", size="2.5")]))
    rocket = next(node for node in plan.nodes if node.id == "rocket")
    assert rocket.content == Model3D(model="saturn_v", position=["0", "h", "0"], size="2.5", rotation=["0", "0", "0"])
    assert plan.beats[0].focus_ids == ["rocket"]


@pytest.mark.parametrize("model,message", [
    (DModel(id="m", model="teapot"), "unknown model"),
    (DModel(id="m", model="fox", clip="Dance"), "clips are"),
    (DModel(id="m", model="iss", clip="Walk"), "clips are none"),
    (DModel(id="m", model="fox", x="9"), "within the view"),
    (DModel(id="m", model="fox", size="20"), "size must be"),
])
def test_models_are_validated(model, message):
    with pytest.raises(DiagramError, match=message):
        compile_lesson(lesson(DBeat(say="Look.", focus=[]), sliders=[], arrows=[], models=[model]))


def test_scene_contract_accepts_only_catalog_models_and_clips():
    assert Model3D(model="fox", clip="Walk").clip == "Walk"
    with pytest.raises(ValidationError):
        Model3D(model="https://example.com/x.glb")
    with pytest.raises(ValidationError):
        Model3D(model="fox", clip="Fly")
    with pytest.raises(ValidationError):
        Model3D(model="fox", size="import(os)")


def fake_catalog(data):
    return {"thing": CatalogModel(id="thing", name="Thing", description="A thing.", url="https://example.invalid/t.glb",
                                  sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), license="CC0-1.0",
                                  credit="Tester", clips=())}


def test_library_downloads_once_and_verifies_the_hash(tmp_path):
    data, calls = b"glTF-binary-bytes", []
    library = ModelLibrary(tmp_path, fake_catalog(data), fetch=lambda url: calls.append(url) or data)
    assert library.path("thing").read_bytes() == data
    assert library.path("thing").read_bytes() == data and len(calls) == 1
    assert library.ready("thing") and not library.ready("missing")


def test_library_refuses_a_changed_upstream_file(tmp_path):
    library = ModelLibrary(tmp_path, fake_catalog(b"expected"), fetch=lambda url: b"tampered")
    with pytest.raises(ModelError, match="integrity"):
        library.path("thing")
    assert not library.ready("thing") and not list(tmp_path.iterdir())


def test_catalog_entries_are_complete():
    for model in CATALOG.values():
        assert model.url.startswith("https://") and len(model.sha256) == 64 and model.license and model.credit


def test_model_routes_serve_cached_files_to_signed_in_users(tmp_path):
    data = b"glTF-binary-bytes"
    library = ModelLibrary(tmp_path, fake_catalog(data), fetch=lambda url: data)
    with MemoryGraph.open("sqlite:///:memory:") as graph:
        human = graph.create_human()
        app = create_app(graph, {TOKEN: human.human_id}, voice=None, planner=None, author=None, models=library)
        with TestClient(app) as client:
            auth = {"Authorization": "Bearer " + TOKEN}
            assert client.get("/api/models/thing.glb").status_code == 401
            listing = client.get("/api/models", headers=auth).json()["models"]
            assert listing == [{"id": "thing", "name": "Thing", "license": "CC0-1.0", "credit": "Tester",
                                "clips": [], "bytes": len(data), "ready": False}]
            response = client.get("/api/models/thing.glb", headers=auth)
            assert response.status_code == 200 and response.content == data
            assert response.headers["content-type"] == "model/gltf-binary"
            assert "immutable" in response.headers["cache-control"]
            assert client.get("/api/models", headers=auth).json()["models"][0]["ready"] is True
            assert client.get("/api/models/other.glb", headers=auth).status_code == 404
