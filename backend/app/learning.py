"""Deterministic local teaching recipes and verified projection mathematics."""
from math import fsum

from .scene import Arrow, Axes, Label, Node, Slider, StrictModel
from pydantic import Field


def projection(v: list[float], u: list[float]) -> list[float]:
    norm2 = fsum(x * x for x in u)
    if norm2 == 0:
        raise ValueError("Cannot project onto the zero vector")
    factor = fsum(a * b for a, b in zip(v, u, strict=True)) / norm2
    return [factor * x for x in u]


class TeachingBeat(StrictModel):
    beat_id: str
    say_text: str
    focus_ids: list[str]
    cue: str = "immediate"
    allow_interrupt: bool = True


class LessonPlan(StrictModel):
    schema_version: int = 1
    recipe_id: str
    beats: list[TeachingBeat]
    comprehension_prompt: str = "How well do you understand this concept, from 1 to 10?"
    graph_context: dict | None = None
    nodes: list[Node] = Field(exclude=True)


def plan(text: str) -> LessonPlan:
    if any(word in text.casefold() for word in ("dot product", "projection", "vector")):
        return LessonPlan(recipe_id="vector_projection_v1", nodes=[
            Node(id="axes", content=Axes()),
            Node(id="u", content=Arrow(end=[3.0, 0.0])),
            Node(id="v", content=Arrow(end=[2.0, 3.0]), style="secondary"),
            Node(id="projection", content=Arrow(end=projection([2.0, 3.0], [3.0, 0.0]))),
            Node(id="formula", content=Label(text="proj_u(v) = (u dot v / u dot u) u")),
            Node(id="angle", content=Slider(binding="projection.angle", minimum=-180.0,
                                            maximum=180.0, value=56.309932)),
        ], beats=[
            TeachingBeat(beat_id="vectors", say_text="The two arrows represent u and v.",
                         focus_ids=["u", "v"]),
            TeachingBeat(beat_id="projection", say_text="Project v onto u: its shadow is (2, 0).",
                         focus_ids=["projection"]),
            TeachingBeat(beat_id="dot", say_text="Here u dot v is 6, and u dot u is 9.",
                         focus_ids=["formula"]),
        ])
    return LessonPlan(recipe_id="whiteboard_v1", nodes=[
        Node(id="notice", content=Label(text="No verified animation for this topic yet."))
    ], beats=[TeachingBeat(beat_id="fallback", say_text="Try asking about vector projection or dot product.",
                           focus_ids=["notice"])])
