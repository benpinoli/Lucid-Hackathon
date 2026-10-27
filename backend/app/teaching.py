"""Provider-independent teaching prompts, schemas, and local grading helpers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .diagrams import AUTHORED, MAX_BEATS, Diagram, DMove
from .learning import LessonPlan
from .models import describe_for_author

MAX_STEPS = MAX_BEATS  # Small steps are cheap to listen to; one crowded step is not.
MAX_PREREQUISITES = 5

DIAGRAM_RULES = """Diagram language (LUCID computes all geometry; you never give pixel coordinates):
- dimensions: 2 for planar diagrams, 3 for genuine spatial lessons. When the learner requests
  3D, use dimensions=3 with nonzero depth; never substitute a flat diagram silently.
- In 3D, curves have optional z(t), points have z, arrows have start_z and end_z, labels have z.
  surfaces: up to 3 parametric surfaces x(u,v),y(u,v),z(u,v), with u_min<u_max,v_min<v_max.
  u and v are reserved surface parameters, not sliders. Keep the mesh inside -6..6 in all axes.
  Explain what each axis means. A complex-valued function needs four real coordinates for its
  full graph: explicitly name the 3D slice/encoding; roots require BOTH output components zero.
- axes: true for anything on a coordinate plane (view is about -4.5..4.5 in x and y).
- sliders: up to 3 parameters, each one lowercase letter other than e or t, with a short label,
  min < max and a default value. Use them so the learner can explore the idea by moving it.
- curves: parametric (x(t), y(t)) for t_min..t_max, e.g. a function plot x="t", y="0.5*t^2".
  Segments are curves too: x="a*t", y="b*t" for t in 0..1.
- arrows: start and end coordinates; points: a coordinate with optional short text.
- Expressions use + - * / ^, parentheses, numbers, pi, e, the sliders' letters (and t in
  curves), and sin cos tan asin acos atan exp ln sqrt abs floor min max. Always write *.
- labels: use $...$ LaTeX for mathematical labels and equations. Keep geometry labels under
  24 characters; put longer equations in a label with id="formula" (shown below the picture).
  Labels must avoid curves, arrows and other labels. Positions within -5.5..5.5.
- graph: for discrete structures, 2-7 nodes with short labels, up to 10 edges with optional
  numeric weights, and an optional highlighted node path. LUCID lays it out.
- models: up to 3 real 3D objects, only from this catalog (model = the id):
{catalog}
  Use one when a real object makes the idea concrete: a rocket whose y is a slider for height,
  a truck whose x is distance travelled, an asteroid circling on x="3*cos(a)", y="3*sin(a)".
  x, y, z place its centre; size is its largest side (0.2-8 board units, about 1-2.5 looks
  right); rotate_x/rotate_y/rotate_z are degrees. All of these may use slider letters, so the
  object itself moves when the slider (or a beat animation) moves. clip optionally plays one of
  that model's listed clips (a walking fox). Skip models for purely abstract mathematics.
- At the default slider values everything must sit inside the view. 30 objects at most
  (a graph costs about 2 per node and 1-2 per edge).
- Element ids: lowercase letters, digits and underscores, unique.
Only state facts the diagram shows or that are standard and certain. Keep it simple.""".format(
    catalog=describe_for_author())

EXAMPLE = json.dumps(AUTHORED["derivatives"].model_dump(), ensure_ascii=False)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(_Spec):
    question: str
    expected_answer: str


class Grade(_Spec):
    verdict: Literal["correct", "partly", "wrong", "unsure"]
    feedback: str


class RetrievalGrade(_Spec):
    score: float = Field(ge=1, le=10)
    feedback: str
    demonstrated: list[str]
    missing: list[str]


class ConceptLink(_Spec):
    concept: str
    similarity: int


class ConceptSpec(_Spec):
    name: str
    description: str
    links: list[ConceptLink]


class ScriptStep(_Spec):
    say: str  # Spoken aloud, word for word.
    show: str  # What the picture shows and how it moves meanwhile: the illustrator's brief.


class LessonScript(_Spec):
    """The lesson plan: what is said, step by step. It contains no drawing."""
    title: str
    steps: list[ScriptStep]
    check: Check | None
    prerequisites: list[str] = Field(default_factory=list, max_length=MAX_PREREQUISITES)
    facts: list[ConceptSpec] = Field(default_factory=list, max_length=6)


class StepVisual(_Spec):
    focus: list[str]
    animate: list[DMove] = Field(default_factory=list)


class Visuals(_Spec):
    """The illustrator's scene for a script: one diagram, and per step what to highlight and move."""
    diagram: Diagram
    steps: list[StepVisual]


@dataclass
class Written:
    plan: LessonPlan
    check: Check | None
    prerequisites: list[str] = field(default_factory=list)  # Smaller topics to learn first, most basic first.
    facts: list[ConceptSpec] = field(default_factory=list)
    script: "LessonScript | None" = None  # Kept when not drawn yet (plan is None): see explain(draw=...).


UNSURE = re.compile(r"\b(not sure|don'?t know|no idea|unsure|skip|i'?m lost|pass)\b")
WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}


def _normal(text):
    text = text.casefold()
    text = re.sub(r"[a-z]+", lambda m: WORDS.get(m.group(), m.group()), text)
    return re.sub(r"[\s(),.\[\]'\"!?]+", "", text)


def grade_locally(answer, expected, choices=None, correct=None):
    """Deterministic grading for authored multiple-choice questions and short exact answers."""
    if not answer.strip() or UNSURE.search(answer.casefold()):
        return "unsure", "No problem, that tells me where to start."
    said = _normal(answer)
    if choices:
        letter = re.fullmatch(r"\W*(?:option\s*)?([a-d])\W*", answer.casefold())
        if letter:
            picked = "abcd".index(letter.group(1))
            return ("correct", "That's right.") if picked == correct else ("wrong", f"Not quite: it's {choices[correct]}.")
        forms = [_normal(choice) for choice in choices]
        matched = [i for i, form in enumerate(forms) if form == said] or [
            i for i, form in enumerate(forms) if form and form in said]
        # "2x" contains "x" and "2": keep only the most specific choices that were said.
        matched = [i for i in matched if not any(i != j and forms[i] in forms[j] for j in matched)]
        if matched == [correct]:
            return "correct", "That's right."
        if len(matched) == 1:
            return "wrong", f"Not quite: it's {choices[correct]}."
        return "unsure", f"I didn't catch one choice; the answer is {choices[correct]}."
    return ("correct", "That's right.") if _normal(expected) in said else ("wrong", f"Not quite: it's {expected}.")


RETRY_BUDGET = 25.0  # Seconds: past this, a malformed lesson plan falls back instead of retrying.
VISUAL_RETRY_BUDGET = 50.0  # Scenes take longer to write; a fixed scene beats a title card.

SCREEN_RULES = (
    "\n\nWHAT THE LEARNER SEES: the request describes the learner's screen right now: each diagram "
    "element (positions evaluated at the learner's current slider values), the spoken steps and which one "
    "they are on, sliders they moved, any running animation, whether they are in VR, the pending question, "
    "and the last few exchanges. Ground what you write in it. When they are confused or ask about something, "
    "start from the exact element or step they are looking at and name it the way it appears (the orange "
    "tangent line, step 2). Change one thing about the current picture rather than starting over; if the same "
    "representation has already failed, switch to a different one. Slider positions they chose show what they "
    "were exploring: use them as starting values when they still fit. Never say something is on screen that "
    "the description does not list.")

LESSON_SYSTEM = (
    "You are LUCID's lesson writer, a patient tutor. You write what is said, one small step at a time; a "
    "separate illustrator then builds an animated picture, in 3D and 2D, for every step from your notes. "
    "Honor the learner's request even if they already know the topic: review with a fresh example or extend "
    "their understanding. Use the ordered teaching sequence and nearby known concepts as bridges.\n\n"
    "TEACHING STYLE: Give the lesson in the style of Jay Cummings. Keep the step-by-step spoken format "
    "and visual teaching requirements below.\n\n"
    "STEPS: break the idea into as many small steps as it needs, usually 4 to 10 and more for a hard idea "
    f"(at most {MAX_STEPS}). Never pack two ideas into one step: if a step feels big, split it. The app pauses "
    "after each step for the learner to predict or think, so each step must stand on its own. say is spoken "
    "aloud word for word: one or two plain sentences under 30 words, no markup, no symbols that do not read "
    "aloud well. Start with a concrete problem the idea solves BEFORE naming or defining it: the first step "
    "asks a motivating question about something the learner can see. Then reveal the mathematical model, "
    "show one thing changing, and only then state the general rule. Say \"watch\" when something moves; "
    "never ask the learner to drag.\n"
    "show is the illustrator's brief for that step: the concrete objects, quantities and the motion the "
    "learner should see while it is said (\"a rocket rising from the ground; beside it, its height plotted "
    "against time as a curve that steepens\"). Real objects make ideas concrete; mathematics belongs on the "
    "2D board. Every number a step says must be one the picture can show exactly. Only state facts that "
    "are standard and certain.\n\n"
    "check: a one-line question answerable in a few words, with its expected answer.\n"
    "prerequisites: when the learner is stuck because smaller ideas are missing, list each missing idea as "
    "its own small topic, most basic first. Do not be afraid to break a topic into many small topics; each "
    "gets its own short lesson before returning here. Otherwise leave it empty.\n"
    "facts: up to six small, reusable, standard facts actually taught, each with a precise name, definition "
    "(including formulas and conditions), and direct prerequisite links by concept name from the context or "
    "other facts. For example even functions satisfy f(-x)=f(x) on a symmetric domain and connect to "
    "symmetry. Do not extract learner claims, questions, or unverified guesses as facts."
    + SCREEN_RULES + "\n\nCall submit_lesson_plan exactly once.")

VISUAL_SYSTEM = (
    "You are LUCID's illustrator. A lesson writer has written a lesson as spoken steps, each with a note on "
    "what the picture should show. Build ONE scene whose objects explain what is being said, then for each "
    "step, in the same order, say which elements it is about and what moves. Never change, add or drop steps.\n\n"
    "BUILD IN 3D: the learner stands in a 3D space (often in VR), so use dimensions=3. Draw the mathematics "
    "(curves, arrows, points and $...$ labels) on the z=0 plane like a board inside the scene, and place the "
    "concrete situation around it with real depth: catalog models, surfaces, arrows with z, objects at "
    "different z. A flat-looking scene with dimensions=3 is a missed chance: give something depth. Use "
    "dimensions=2 only for a node-edge graph. Skip models only for purely abstract ideas.\n"
    "RULES THAT ARE EASY TO SLIP ON: ids \"title\" and \"axes\" are taken; at most one move per slider in a "
    "step; positions, sizes and rotations are expression strings (\"2\", not 2); every point, arrow tip and "
    "model must stay within -5.5..5.5 throughout every animation.\n"
    "MAKE IT MOVE: animate. Give the scene sliders for the quantities that change and animate them in most "
    "steps, so every idea is seen happening (the rocket rising as its curve is drawn, a point sliding to "
    "where the slope is zero, a shadow shrinking). Bind models' positions and rotations to sliders so the "
    "objects themselves move.\n\n"
    "Per step: focus lists the element ids that step is about (graph nodes by id, graph edges as \"a-b\", "
    "\"graph\" for all of it). animate (at most 3 moves of {param, to, seconds}) slides those sliders while "
    "the step is spoken; each move starts where earlier steps left the slider (or at an explicit start). "
    "The motion must show exactly what that step says, e.g. \"watch the point slide to where the curve is "
    "flat\" animates p to the flat point. Never animate something the step does not talk about. Everything "
    "must stay in view at the end of each animated step. Leave sliders at values matching any numbers a "
    "step speaks.\n\n" + DIAGRAM_RULES
    + "\n\nExample scene (its beats show the say, focus and animate of each step):\n" + EXAMPLE
    + SCREEN_RULES + "\n\nCall submit_visuals exactly once.")

LESSON_MEMORY = (
    "\n\nLEARNER NOTES: you keep private notes on this learner with the memory tool under /memories. Their "
    "current contents are included in the request, so never call view. In the same response as "
    "submit_lesson_plan, update them only when this exchange taught you something durable about how this "
    "learner learns: a misconception and whether it was resolved, an example or representation that worked or "
    "failed, a stated preference (pace, 2D or 3D, real objects). Keep one short file, /memories/learner.md, of "
    "bullet points; prefer str_replace or insert over rewriting it, and stay under 2,000 characters by merging "
    "old points. Record learning only: no personal details, no quotes, nothing about other people.")


