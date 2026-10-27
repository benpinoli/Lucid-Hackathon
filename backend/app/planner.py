"""Claude chooses which recipe to teach and with which example; recipes compute everything.

Claude never writes coordinates, numbers or narration. It returns typed parameters, a
builder in `learning.py` validates and computes the lesson, and any failure (invalid
parameters twice, refusal, timeout, outage) falls back to the keyword-routed authored lessons.
"""
from __future__ import annotations

import logging
import os
from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict, create_model

from .learning import LessonError, LessonPlan, complex_quadratic_lesson, dijkstra_lesson, mean_value_lesson, plan, vector_projection

log = logging.getLogger("lucid.planner")


def describe_failure(exc) -> str:
    """API errors carry the provider's reason (never learner text); others log only their type."""
    if isinstance(exc, anthropic.APIStatusError):
        return f"{type(exc).__name__} {exc.status_code}: {exc.message}"
    return type(exc).__name__

SYSTEM = """You choose the visual lesson for LUCID, a 3D math tutor. Given what a learner asked,
pick one recipe and its parameters. The app computes every number, label and sentence from
your parameters, so choose clean, small examples a learner can follow.

Recipes and their limits:
- vector_projection: dot product, vector projection, angle between vectors. u and v are 2D
  vectors with components between -4.5 and 4.5, each of length at least 0.5. Prefer integers.
- dijkstra: Dijkstra's algorithm or shortest paths in a weighted graph. 2-7 nodes named with
  1-3 letters or digits (for example A, B, C), 1-12 undirected edges with whole-number weights
  1-99, at most one edge per pair, 2 x nodes + 2 x edges + 1 <= 30, and target reachable from
  source. Use the learner's graph if they describe one; otherwise design one where the direct
  edge is not the shortest route, so the algorithm has something to discover.
- mean_value: the mean value theorem. f(x) = c0 + c1 x + c2 x^2 + c3 x^3, given as coefficients
  [c0, c1, c2, c3] each between -10 and 10, on an interval a < b inside [-4.5, 4.5] with
  b - a >= 0.5, and |f(x)| <= 3.8 on [a, b]. Use the learner's function if it is a polynomial
  of degree 3 or less and fits; otherwise pick a close polynomial that does.
- none: the request is not about any of these topics.
- complex_quadratic: a genuine 3D diagram of az^2+bz+c with real coefficients a,b,c,
  |coefficient| <= 10, |a| >= .01, roots within -3..3 in the complex plane.

Set recipe, fill in only that recipe's parameter object, and set the others to null.
If the learner's values break a limit, use the nearest valid example instead."""


class _Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VectorParams(_Choice):
    u: list[float]
    v: list[float]


class EdgeParams(_Choice):
    a: str
    b: str
    weight: int


class DijkstraParams(_Choice):
    nodes: list[str]
    edges: list[EdgeParams]
    source: str
    target: str


class MeanValueParams(_Choice):
    coefficients: list[float]
    a: float
    b: float


class QuadraticParams(_Choice):
    a: float
    b: float
    c: float


class LessonChoice(_Choice):
    # An enum plus one nullable parameter object per recipe: the SDK strips `const`, so a
    # union of tagged objects would leave the tag unconstrained; an enum is enforced.
    recipe: Literal["vector_projection", "dijkstra", "mean_value", "complex_quadratic", "none"]
    vector_projection: VectorParams | None
    dijkstra: DijkstraParams | None
    mean_value: MeanValueParams | None
    complex_quadratic: QuadraticParams | None = None


def build(choice: LessonChoice) -> LessonPlan | None:
    if choice.recipe == "none":
        return None
    params = getattr(choice, choice.recipe)
    if params is None:
        raise LessonError(f"Recipe {choice.recipe} needs its {choice.recipe} parameters")
    if choice.recipe == "vector_projection":
        return vector_projection(params.u, params.v)
    if choice.recipe == "dijkstra":
        return dijkstra_lesson(params.nodes, [(e.a, e.b, e.weight) for e in params.edges],
                               params.source, params.target)
    if choice.recipe == "complex_quadratic":
        return complex_quadratic_lesson(params.a, params.b, params.c)
    return mean_value_lesson(params.coefficients, params.a, params.b)


class ClaudePlanner:
    """Turns a learner request into recipe parameters. Configure by environment only."""

    def __init__(self, client, model: str = "claude-opus-5-5"):
        self.client, self.model = client, model

    @classmethod
    def from_env(cls):
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        # Bounded so a slow provider degrades to authored lessons inside the client's deadline.
        client = anthropic.Anthropic(timeout=12.0, max_retries=0)
        return cls(client, os.getenv("LUCID_PLANNER_MODEL", "claude-opus-5-5"))

    def _ask(self, messages):
        response = self.client.beta.messages.parse(
            model=self.model, max_tokens=4000, system=SYSTEM, messages=messages,
            output_format=LessonChoice,
            # Picking parameters is a short, well-specified task; low effort keeps voice turns quick.
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise LessonError(f"Planner returned no lesson (stop_reason={response.stop_reason})")
        return response

    def resolver(self, curriculum):
        """Map free-text goals to a curriculum concept when aliases do not match."""
        keys = tuple(topic.key for topic in curriculum.topics) + ("none",)
        choice = create_model("TopicChoice", __config__=ConfigDict(extra="forbid"), topic=(Literal[keys], ...))
        listing = "\n".join(f"- {topic.key}: {topic.name}" for topic in curriculum.topics)

        def resolve(text):
            try:
                response = self.client.beta.messages.parse(
                    model=self.model, max_tokens=2000, output_format=choice,
                    system="Map the learner's goal to the single closest curriculum topic key, or none "
                           "if no topic fits.\nTopics:\n" + listing,
                    messages=[{"role": "user", "content": text}], output_config={"effort": "low"},
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            except (anthropic.APIError, ValueError) as exc:
                log.warning("Claude goal resolver failed: %s", describe_failure(exc))
                return None
            if response.stop_reason == "refusal" or response.parsed_output is None:
                return None
            return curriculum.ids.get(response.parsed_output.topic)
        return resolve

    def plan(self, text: str) -> LessonPlan:
        messages = [{"role": "user", "content": text}]
        try:
            for attempt in range(2):
                response = self._ask(messages)
                try:
                    lesson = build(response.parsed_output)
                except LessonError as exc:
                    if attempt:
                        raise
                    # One corrective retry; append-only history keeps the transcript valid.
                    messages += [{"role": "assistant", "content": response.content},
                                 {"role": "user", "content": f"That example is invalid: {exc}. "
                                  "Choose parameters that satisfy every limit."}]
                    continue
                if lesson is None:
                    break
                lesson.planned_by = "claude"
                return lesson
        except (anthropic.APIError, ValueError) as exc:
            # Never log learner text: transcripts are personal data.
            log.warning("Claude planner fell back to authored lessons: %s", describe_failure(exc))
        return plan(text)
