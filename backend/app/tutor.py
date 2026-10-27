"""The conversational tutor: goal -> spoken check -> silent cheapest route -> teach -> check.

One Session per lesson. Every reply is a LessonPlan with a diagram and spoken beats. The
route is never shown or spoken; the tutor just teaches the next concept on it. Scores change
only through graded answers (diagnostic or check questions), never from conversation alone.
Confusion triggers a deeper explanation, or a detour to a prerequisite the author identifies;
concepts the graph lacks are created through the engine's proposal API with provenance.

Turns are conversational (see conversation.py): a short reaction, one idea, one hand-off
question. Questions are open first; options are a hint, never the default.
"""
from __future__ import annotations

import os
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from memory_graph import AmbiguousConcept, NotFound

from .author import UNSURE, Check, LessonScript, ScriptStep, grade_locally
from .conversation import PHRASES, Phrasing, echo, glue, joined, options_redundant
from .diagrams import AUTHORED, compile_lesson
from .goals import KNOWN, GoalError, Topic, _wrap, concept_card
from .learning import LessonPlan, TeachingBeat, plan as authored_plan
from .library import LessonLibrary
from .memory import LearnerMemory
from .reviews import Reviews
from .scene import Label, MAX_OBJECTS, Node
from .screen import describe_screen

DIAGNOSTIC_SCORES = {"correct": 7.0, "partly": 4.0, "wrong": 2.0, "unsure": 1.0}
CHECK_SCORES = {"correct": 8.0, "partly": 5.0, "wrong": 3.0, "unsure": 2.0}
MAX_QUESTIONS = 8  # Starting-check budget; the frontier search usually needs far fewer.
MAX_CLARIFY = 2  # Deeper explanations per concept before moving on.
ACTIONS = ("goal", "answer", "confused", "question", "continue", "explore", "review", "deeper", "recall", "warmup",
           "skip", "hint")
REVISIT = re.compile(r"\b(review|revisit|go deeper|learn more|tell me more|teach me more|more detail|again)\b", re.I)
# A short "yes" to "Ready for the next piece?" moves on; it is never a topic to look up.
READY = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok(ay)?|ready|go on|go ahead|next|keep going|let'?s go|continue"
                   r"|makes sense|got it|i see|i get it|right|mhm|uh huh)\b[\w\s,'!.]{0,24}$", re.I)
# ...and a "no" to "Make sense so far?" asks for help with that step.
NOT_CLEAR = re.compile(r"^\W*(no|nope|not really|not yet|not quite|huh|i'?m confused|i don'?t (?:get|understand))\b", re.I)
# Replies to "Have you worked with X before?"; anything else counts as neutral.
NEW_TO_IT = re.compile(r"\b(no|nope|never|not really|new to|haven'?t)\b", re.I)
FAMILIAR = re.compile(r"\b(yes|yeah|yep|some|a (?:bit|little)|familiar|i have|i know|seen it|used it|kind of|sort of)\b", re.I)
# First option is correct (choices are shuffled when asked). Tiny transfer/prediction tasks.
PREDICTIONS = {
    "vectors": ("If an arrow points the opposite way, which property changes?", "direction", "neither property"),
    "vector_length": ("In a right triangle, is the hypotenuse longer or shorter than either leg?", "longer", "shorter"),
    "angles": ("A vertical arrow casts how much horizontal shadow?", "zero", "its full length"),
    "dot_product": ("The arrow (3, 0) points along which axis?", "positive x", "positive y"),
    "vector_projection": ("If a vector is perpendicular to the projection axis, how long is its shadow on that axis?", "zero", "its full length"),
    "graphs": ("Can two nodes with no direct edge still be joined by a path through another node?", "yes", "no"),
    "weighted_graphs": ("A path uses edges costing 2 and 5. What is its total cost?", "7", "10"),
    "bfs": ("Does breadth-first search explore immediate neighbors or distant nodes first?", "immediate neighbors", "distant nodes"),
    "priority_queue": ("A min-priority queue holds 3 and 8. Which is returned first?", "3", "8"),
    "dijkstra": ("Can a route with more edges still have a lower total cost?", "yes", "no"),
    "functions": ("May a function assign two different outputs to the same input?", "no", "yes"),
    "slope": ("Two points have the same height. Is their connecting line horizontal or vertical?", "horizontal", "vertical"),
    "limits": ("Must a function be defined at a point for its limit there to exist?", "no", "yes"),
    "continuity": ("A graph has a jump at a point. Is it continuous there?", "no", "yes"),
    "derivatives": ("A tangent slopes downward from left to right. Is the derivative negative or positive?", "negative", "positive"),
    "mean_value_theorem": ("A graph has a jump inside the interval. Can we apply the mean value theorem?", "no", "yes"),
}


@dataclass
class Question:
    concept: str
    kind: str  # experience, diagnostic, check, prediction(_choice), synthesis, teachback, recall(_choice)
    prompt: str
    expected: str
    choices: tuple[str, ...] | None = None
    correct: int | None = None
    related: str | None = None
    event_id: str = field(default_factory=lambda: uuid4().hex)
    retries: int = 0
    offered: tuple[str, ...] | None = None  # Choices read out and shown: only as a hint.
    hints: int = 0

    def __post_init__(self):
        # "Which function is continuous?" makes no sense without its options.
        if self.choices and self.offered is None and re.match(r"\s*which\b", self.prompt, re.I):
            self.offered = self.choices

    @property
    def answer(self):
        return self.choices[self.correct] if self.choices else self.expected

    @property
    def spoken(self):
        if not self.offered or options_redundant(self.prompt, self.offered):
            return self.prompt
        return f"{self.prompt} {PHRASES['options'][0].format(options=joined(self.offered))}"


@dataclass
class Session:
    target: str | None = None
    mode: str = "idle"  # idle | diagnosing | checking | done
    queue: deque = field(default_factory=deque)
    asked: set = field(default_factory=set)
    question: Question | None = None
    current: str | None = None
    viewed: str | None = None  # Latest explained topic, including a side question or exploration.
    previous: str | None = None  # Last concept passed; its edge to the next one gets rated.
    detours: list = field(default_factory=list)  # Topics waiting for their smaller topics to finish.
    subtopics: deque = field(default_factory=deque)  # (name, parent concept): smaller topics still to teach.
    detour_topics: dict = field(default_factory=dict)  # parent -> names of smaller topics taught for it.
    said_steps: dict = field(default_factory=dict)  # concept -> sentences already spoken about it.
    attempts: dict = field(default_factory=dict)
    covered: set = field(default_factory=set)
    recent: deque = field(default_factory=lambda: deque(maxlen=6))
    active_plan: LessonPlan | None = None
    remaining_beats: list = field(default_factory=list)
    taught_beats: list = field(default_factory=list)
    warmup_queue: deque = field(default_factory=deque)
    warmup_active: bool = False
    resume_question: Question | None = None
    # What the learner sees, for the author model: the displayed scene (from the service), the
    # client's view (moved sliders, active step, animation, VR), spoken steps and recent turns.
    screen: list = field(default_factory=list)
    view: dict | None = None
    shown_beats: list = field(default_factory=list)
    exchanges: deque = field(default_factory=lambda: deque(maxlen=6))
    said: dict = field(default_factory=dict)  # Last phrase variant per situation, so none repeats.
    bridged: bool = False
    step_check: bool = False  # The step just shown has asked "Make sense so far?"; its question comes next.  # The first lesson after the check opens from what the learner knows.
    validate_write: Callable[[], None] | None = None  # Service epoch guard, outside provider calls.
    lock: threading.Lock = field(default_factory=threading.Lock)


def card(title, lines) -> LessonPlan:
    """Text on the board for prompts and questions; explanations always get real diagrams."""
    nodes, y = [Node(id="title", content=Label(text=title[:80], position=[0.0, 3.6]))], 2.4
    for i, line in enumerate(_wrap(" ".join(lines))[:8]):
        nodes.append(Node(id=f"line_{i}", content=Label(text=line, position=[0.0, round(y, 2)])))
        y -= 0.9
    return LessonPlan(recipe_id="tutor_card_v1", nodes=nodes, beats=[])


class Tutor:
    def __init__(self, graph, goals, planner, author=None, active_learning=True, phrasing=None):
        self.graph, self.goals, self.planner, self.author = graph, goals, planner, author
        self.curriculum = goals.curriculum
        self.library = LessonLibrary(graph)
        self.memory = LearnerMemory(graph)
        self.reviews = Reviews(graph)
        self.active_learning = active_learning
        self.scenes = {}  # Built-in lessons rebuilt in 3D by the illustrator, once per topic.
        self.scenes_lock = threading.Lock()
        self.scenes_pending = {}  # topic key -> Event set when its drawing finishes
        # LUCID_TUTOR_VARIETY=0 fixes the wording (first variant, seeded shuffles) for tests and demos.
        variety = os.getenv("LUCID_TUTOR_VARIETY", "1") != "0"
        self.phrasing = phrasing or Phrasing(variety=variety, seed=None if variety else 0)

    def _say(self, s, key, **values):
        return self.phrasing.say(key, s.said, **values)

    @property
    def interactive(self):
        return self.active_learning and self.author is not None and hasattr(self.author, "practice")

    # ---- entry point -----------------------------------------------------------------------
    def act(self, session: Session, user, action, text="", view=None, screen=None) -> LessonPlan:
        if action not in ACTIONS:
            raise ValueError("Unknown tutor action")
        text = text.strip()
        session.view = view
        if screen is not None:
            session.screen = list(screen)
        # A request for more teaching is never a quiz answer, even if a check is pending.
        if action in ("recall", "warmup"):
            plan = self._recall(session, user, text, warmup=action == "warmup")
        elif action == "skip":
            plan = self._skip(session, user)
        elif action == "hint":
            plan = self._hint(session, user)
        elif action in ("answer", "question") and not session.question and READY.match(text):
            plan = self._continue(session, user)
        elif action in ("answer", "question") and session.step_check and NOT_CLEAR.match(text):
            plan = self._confused(session, user, text)
        elif action in ("review", "deeper") or (action in ("answer", "question", "continue") and REVISIT.search(text)):
            plan = self._revisit(session, user, text, action == "review")
        elif action == "goal":
            plan = self._goal(session, user, text)
        elif action == "answer" and session.question:
            plan = self._answer(session, user, text)
        elif action == "confused":
            plan = self._confused(session, user, text)
        elif action in ("question", "answer") and text:
            plan = self._ask(session, user, text)
        elif action == "explore" and text:
            plan = self._ask(session, user, text, guided=self.active_learning)
        else:
            plan = self._continue(session, user)
        plan.tutor = {"mode": session.mode, "awaiting_answer": session.question is not None,
                      "goal": self._name(session.target) if session.target else None}
        if self.active_learning:
            plan.tutor.update({"exercise": session.question.kind if session.question else None,
                               "step": len(session.taught_beats),
                               "total_steps": len(session.active_plan.beats) if session.active_plan else 0})
        context_target = plan.concept_id or session.current or session.target
        if plan.concept_id:
            session.viewed = plan.concept_id
        if context_target:
            plan.graph_context = self._context(user, session, context_target)
        session.shown_beats = [beat.say_text for beat in plan.beats]
        session.screen = list(plan.nodes)
        session.exchanges.append({"learner": text[:300], "action": action,
                                  "tutor": " ".join(session.shown_beats)[:500]})
        return plan

    def _screen(self, s):
        """The learner's screen as the author model sees it (see screen.describe_screen)."""
        return describe_screen(s.screen, s.shown_beats, s.view,
                               question=s.question.spoken if s.question else None, exchanges=list(s.exchanges))

    # ---- goal and starting check -----------------------------------------------------------
    def _goal(self, s, user, text):
        target = self._find(text, create=True)
        if target is None:
            raise GoalError("I couldn't find that topic yet. Try: "
                            + ", ".join(t.name for t in self.curriculum.topics if t.level >= 2) + ".")
        s.target, s.queue, s.asked, s.question = target, deque([target]), set(), None
        s.current, s.previous, s.detours, s.attempts, s.covered = None, None, [], {}, set()
        s.subtopics, s.detour_topics, s.said_steps = deque(), {}, {}
        s.viewed = None
        s.active_plan, s.remaining_beats, s.taught_beats = None, [], []
        s.warmup_active, s.warmup_queue, s.resume_question = False, deque(), None
        s.mode, s.bridged = "diagnosing", False
        members = self.graph.human(user).snapshot().nodes
        if target in members and members[target].understanding >= KNOWN:
            return self._revisit(s, user, text, review=False)
        # The check opens as a conversation: how familiar is this, then a few quick probes.
        name = self._name(target)
        s.question = Question(target, "experience", self._say(s, "experience", topic=name), "")
        return self._ask_card(s.question, [self._say(s, "goal", topic=name)])

    def _experience(self, s, user, q, text):
        """The learner's own account shapes the lead-in; only "never seen it" counts as evidence."""
        if NEW_TO_IT.search(text):
            if s.validate_write:
                s.validate_write()
            # Same evidence as answering "not sure" to the goal's own question, without asking it.
            self.goals.record(user, q.concept, DIAGNOSTIC_SCORES["unsure"], "voice_diagnostic",
                              {"question": q.prompt, "verdict": "unsure", "self_report": text[:200]},
                              link_missing=not self.interactive)
            s.asked.add(q.concept)
            self._expand(s, q.concept)
            return self._next_question(s, user, [self._say(s, "probe_new")])
        return self._next_question(s, user, [self._say(s, "probe_known" if FAMILIAR.search(text) else "probe")])

    def _next_question(self, s, user, intro):
        """Frontier search from the goal: a correct answer stops the search along that branch."""
        members = self.graph.human(user).snapshot().nodes
        while s.queue and len(s.asked) < MAX_QUESTIONS:
            concept = s.queue.popleft()
            if concept in s.asked:
                continue
            if concept in members:  # Earlier evidence: no need to ask again.
                s.asked.add(concept)
                if members[concept].understanding < KNOWN:
                    self._expand(s, concept)
                continue
            question = self._question(concept, "diagnostic")
            s.asked.add(concept)
            if question:
                s.question = question
                return self._ask_card(question, intro)
        s.question = None
        return self._teach_next(s, user, intro)

    def _expand(self, s, concept):
        level = {self.curriculum.ids[t.key]: t.level for t in self.curriculum.topics}
        for neighbor in sorted(self.graph.god.neighbors(concept), key=lambda n: -level.get(n, 2)):
            if neighbor in level and concept in level and level[neighbor] > level[concept]:
                continue  # Missing an idea says nothing about more advanced ones: only probe downward.
            if neighbor not in s.asked and neighbor not in s.queue:
                s.queue.append(neighbor)

    # ---- answers ---------------------------------------------------------------------------
    def _answer(self, s, user, text):
        q, s.question = s.question, None
        if q.kind == "experience":
            return self._experience(s, user, q, text)
        if q.kind in ("prediction", "synthesis", "teachback", "recall"):
            return self._retrieval_answer(s, user, q, text)
        verdict, feedback = self._grade(q, text)
        if (verdict == "unsure" and q.choices and not q.offered and text.strip()
                and not UNSURE.search(text.casefold()) and not options_redundant(q.prompt, q.choices)):
            # An answer that matched no choice isn't graded: offer the options instead.
            return self._scaffold(s, q)
        if s.validate_write:
            s.validate_write()
        self.reviews.log_attempt(user, q.concept, q.kind, CHECK_SCORES[verdict],
                                f"{q.event_id}:{q.retries}", {"verdict": verdict, "question": q.prompt})
        right = verdict in ("correct", "partly")
        if q.kind == "diagnostic":
            # The starting check reacts neutrally and never reveals: the lessons will teach it.
            self.goals.record(user, q.concept, DIAGNOSTIC_SCORES[verdict], "voice_diagnostic",
                              {"question": q.prompt, "verdict": verdict}, link_missing=not self.interactive)
            if verdict != "correct":
                self._expand(s, q.concept)
            reaction = self._say(s, "correct", echo=echo(text, q.answer)) if verdict == "correct" else self._say(s, "unsure")
            return self._next_question(s, user, [reaction])
        if not right and q.retries == 0 and self._narrow(q, text):
            # First miss: a narrower question to try again, without giving the answer away.
            q.retries += 1
            s.question = q
            return self._reask(s, q, [self._say(s, verdict), self._say(s, "narrow")])
        reaction = self._react(s, q, verdict, text, feedback)
        if q.kind == "prediction_choice":
            return self._small_step(s, [reaction])
        self.goals.record(user, q.concept, CHECK_SCORES[verdict], "voice_check",
                          {"question": q.prompt, "verdict": verdict}, previous=s.previous if not self.interactive else None,
                          link_missing=not self.interactive)
        if not self.interactive or q.kind == "recall_choice":
            self.reviews.record(user, q.concept, CHECK_SCORES[verdict], q.event_id,
                                {"kind": q.kind, "verdict": verdict})
        if q.kind == "recall_choice" and s.warmup_active:
            return self._next_recall(s, user, [reaction])
        if right:
            if self.interactive:
                return self._consolidate(s, user, q.concept, [reaction])
            s.covered.add(q.concept)
            s.previous = q.concept
            return self._after_lesson(s, user, [reaction])
        s.attempts[q.concept] = s.attempts.get(q.concept, 0) + 1
        if s.attempts[q.concept] > MAX_CLARIFY:
            s.covered.add(q.concept)
            return self._teach_next(s, user, [reaction, self._say(s, "move_on")])
        return self._clarify(s, user, q.concept, f"They answered {text!r} to the question: {q.prompt}", [reaction])

    def _react(self, s, q, verdict, text, feedback):
        """One line: a short reaction, plus the answer when it was missed (never followed by a retry)."""
        if verdict == "correct":
            return self._say(s, "correct", echo=echo(text, q.answer))
        if not q.choices and self.author and feedback:
            return feedback.strip()[:300]  # The grader's specific feedback on an open answer.
        if verdict == "partly":
            return self._say(s, "partly")
        return f"{self._say(s, verdict)} {self._say(s, 'reveal', answer=q.answer)}"

    def _scaffold(self, s, q):
        q.offered = q.choices
        s.question = q
        return self._reask(s, q, [self._say(s, "scaffold")])

    def _narrow(self, q, text=""):
        """Two options instead of many: the correct one and one the learner didn't just pick."""
        if not q.choices or len(q.offered or q.choices) <= 2:
            return False
        said = text.casefold()
        others = [c for c in (q.offered or q.choices) if c != q.answer and c.casefold() not in said]
        if not others:
            return False
        q.offered = tuple(self.phrasing.shuffle([q.answer, others[0]]))
        return True

    def _grade(self, q, text):
        # Letters ("B") refer to the options as offered; open answers match any choice.
        choices = q.offered or q.choices
        correct = choices.index(q.answer) if choices else None
        if self.author and not choices:
            graded = self.author.grade(q.prompt, q.expected, text)
            if graded:
                return graded
        if self.author and choices and not grade_locally(text, q.expected, choices, correct)[0] in ("correct", "wrong"):
            graded = self.author.grade(q.prompt, q.expected, text, choices)
            if graded:
                return graded
        return grade_locally(text, q.expected, choices, correct)

    def _hint(self, s, user):
        """The hint ladder: options, then two options, then the answer. A hint is never graded."""
        q = s.question
        if q is None:
            if s.active_plan and s.taught_beats:  # Nothing to answer: replay the step just taught.
                plan = self._shown_plan(s)
                plan.beats = [TeachingBeat(beat_id="hint", say_text=s.taught_beats[-1], focus_ids=[])]
                return self._compose([self._say(s, "again")], plan, None, ready=self._say(s, "ready"))
            return self._card(s, "Hint", [self._say(s, "no_question"), self._say(s, "idle")])
        if q.kind == "experience":
            s.question = None
            return self._experience(s, user, q, "")
        if q.choices:
            if not q.offered and not options_redundant(q.prompt, q.choices):
                s.question = None
                return self._scaffold(s, q)
            if q.kind != "diagnostic" and self._narrow(q):
                return self._reask(s, q, [self._say(s, "narrow")])
            return self._answer(s, user, "not sure")
        if q.hints == 0:
            # Open questions: point back at what was just taught (the topic's summary for recall).
            q.hints += 1
            context = s.taught_beats[-1] if q.kind == "prediction" and s.taught_beats else (
                self._description(q.concept).split(". ")[0].rstrip(".") + ".")
            if q.kind not in ("recall", "teachback", "synthesis"):
                return self._reask(s, q, [self._say(s, "hint"), context])
        if q.kind == "prediction":
            # Its expected answer is a grading rubric, not something to read out: the next step shows it.
            s.question = None
            return self._small_step(s, [self._say(s, "see_next")]) if s.remaining_beats else self._continue(s, user)
        return self._skip(s, user)

    # ---- teaching --------------------------------------------------------------------------
    def _teach_next(self, s, user, intro):
        if s.target is None:
            s.mode = "idle"
            return self._card(s, "What would you like to learn?", intro + [self._say(s, "idle")])
        route = self.goals.route(user, s.target)  # Silent: only its next step is ever used.
        if route["status"] == "known":
            s.mode = "done"
            return self._finish(s, user, intro + [self._say(s, "goal_reached", topic=self._name(s.target))])
        step = next((step["id"] for step in route["steps"] if not step["known"] and step["id"] not in s.covered), None)
        if self.interactive and route["total_cost"] is None:
            context = self.goals.teaching_context(user, s.target)
            step = next((n["id"] for n in context["teaching_sequence"]
                         if not n["known"] and n["id"] not in s.covered), step)
        if step is None and route["status"] == "needs_diagnostic":
            step = s.target
        if step is None:
            s.mode = "done"
            return self._finish(s, user, intro + [self._say(s, "route_done", topic=self._name(s.target))])
        upcoming = [st["id"] for st in route["steps"] if not st["known"] and st["id"] not in s.covered and st["id"] != step]
        if upcoming:
            self._prefetch(upcoming[0])
        return self._teach(s, user, step, intro + [self._transition(s, route, step)])

    def _transition(self, s, route, step):
        """"Now, X." The first lesson instead opens from the known idea the route starts at."""
        name = self._name(step)
        if s.bridged:
            return self._say(s, "next", topic=name)
        s.bridged = True
        ids = [st["id"] for st in route["steps"]]
        before = route["steps"][ids.index(step) - 1] if step in ids and ids.index(step) > 0 else None
        if before and before["known"]:
            return self._say(s, "bridge_known", known=before["name"])
        return self._say(s, "bridge_new", topic=name)

    def _teach(self, s, user, concept, intro, returning_after=None):
        """returning_after: smaller topics just learned. The topic is then explained again in new
        words, never with the sentences the learner already heard."""
        s.current, s.mode = concept, "checking"
        s.recent.append(self._name(concept))
        plan = None
        if returning_after is not None and self.author and s.said_steps.get(concept):
            written = self.author.explain(self._name(concept), self._description(concept), self._context(user, s, concept),
                                          screen=self._screen(s), memory=self.memory.for_learner(user),
                                          avoid=list(s.said_steps[concept]), after=returning_after)
            if written:
                self._remember(user, concept, written)
                plan = written.plan
        if plan is None:
            plan, _ = self._lesson(concept, user, s)
        plan.concept_id = concept
        return self._activate(s, plan, intro)

    def _after_lesson(self, s, user, intro):
        """A finished lesson leads to the next smaller topic, back to the topic that needed them,
        or on along the route."""
        if s.detours:
            parent = s.detours[-1]
            smaller = self._next_subtopic(s, user, parent)
            if smaller:
                s.detour_topics.setdefault(parent, []).append(self._name(smaller))
                return self._teach(s, user, smaller, intro + [self._say(s, "next", topic=self._name(smaller))])
            s.detours.pop()
            return self._teach(s, user, parent, intro + [self._say(s, "resume", topic=self._name(parent))],
                               returning_after=s.detour_topics.pop(parent, []))
        return self._teach_next(s, user, intro)

    def _next_subtopic(self, s, user, parent):
        """Smaller topics are created in the shared graph only when their turn comes; known ones are skipped."""
        members = self.graph.human(user).snapshot().nodes
        while s.subtopics and s.subtopics[0][1] == parent:
            name, _ = s.subtopics.popleft()
            try:
                concept = self._find(name, create=True, link_to=parent)
            except GoalError:
                continue
            if (concept and concept != parent and concept not in s.covered and concept not in s.detours
                    and (concept not in members or members[concept].understanding < KNOWN)):
                return concept
        return None

    def _activate(self, s, plan, intro):
        """Start a lesson. It ends without a check question: Continue moves on to the next concept."""
        s.question, s.step_check = None, False
        if self.active_learning and len(plan.beats) > 1:
            s.active_plan = plan.model_copy(deep=True)
            s.remaining_beats, s.taught_beats = list(plan.beats), []
            return self._small_step(s, intro)
        s.said_steps.setdefault(s.current, []).extend(beat.say_text for beat in plan.beats)
        return self._compose(intro, plan, None, ready=self._say(s, "ready"))

    def _shown_plan(self, s):
        """The lesson diagram as revealed so far, without spoken steps."""
        plan = s.active_plan.model_copy(deep=True)
        plan.beats = []
        if s.remaining_beats:
            revealed = {"title", "axes"} | {node.id for node in plan.nodes if node.content.primitive == "slider"}
            revealed.update(node_id for b in s.active_plan.beats[:len(s.taught_beats)] for node_id in b.focus_ids)
            plan.nodes = [node.model_copy(update={"visible": node.id in revealed}) for node in plan.nodes]
        return plan

    def _reask(self, s, q, intro):
        """Ask again: over the lesson diagram mid-lesson, otherwise on a question card."""
        if q.kind.startswith("prediction") and s.active_plan:
            return self._compose(intro, self._shown_plan(s), q)
        return self._ask_card(q, intro)

    def _small_step(self, s, intro):
        """Reveal one short beat, then wait for an actual learner response."""
        beat = s.remaining_beats.pop(0)
        s.taught_beats.append(beat.say_text)
        s.said_steps.setdefault(s.current, []).append(beat.say_text)
        if not s.remaining_beats:
            plan = s.active_plan.model_copy(deep=True)
            plan.beats = [beat]
            s.question = None
            return self._compose(intro, plan, None, ready=self._say(s, "ready"))  # The learner sets the pace.
        plan = self._shown_plan(s)
        plan.beats = [beat]
        # One thing per turn: the step, a check-in, then wait. Its question (if any) is the next
        # turn, once the learner has looked and answered the check-in.
        s.question, s.step_check = None, True
        return self._compose(intro, plan, None, ready=self._say(s, "check_in"))

    def _step_question(self, s):
        """The prediction for the step just taught: Claude's, or the authored one after step 1."""
        check = self.author.practice("prediction", self._name(s.current), self._description(s.current),
                                     covered=list(s.taught_beats)) if self.interactive else None
        if check:
            return Question(s.current, "prediction", check.question, check.expected_answer)
        topic = self.curriculum.by_id.get(s.current)
        if topic and topic.key in PREDICTIONS and len(s.taught_beats) == 1:
            prompt, correct, wrong = PREDICTIONS[topic.key]
            choices = tuple(self.phrasing.shuffle((correct, wrong)))
            return Question(s.current, "prediction_choice", prompt, correct, choices, choices.index(correct))
        return None  # Never turn a provider outage into a fake grade: just go on to the next step.

    def _consolidate(self, s, user, concept, intro):
        members = self.graph.human(user).snapshot()
        god = self.graph.god.snapshot()
        latest = {}
        for assessment in self.graph.history(kind="familiarity", human_id=user, concept=concept):
            if assessment["accepted"]:
                edge = tuple(sorted((assessment["a_id"], assessment["b_id"])))
                latest[edge] = assessment["method"]
        # Only a real, direct God edge is evidence for a synthesis task. Never fabricate a shortcut.
        candidates = [n for n in god.neighbors(concept) if concept in members.nodes and n in members.nodes
                      and members.nodes[n].understanding >= KNOWN
                      and (tuple(sorted((concept, n))) not in members.edges or
                           latest.get(tuple(sorted((concept, n)))) == "derived_min_understanding")]
        candidates.sort(key=lambda n: god.methodology.cost(
            god.edges[tuple(sorted((concept, n)))].similarity,
            members.nodes[concept].understanding, members.nodes[n].understanding))
        related = candidates[0] if candidates else None
        kind = "synthesis" if related else "teachback"
        check = self.author.practice(kind, self._name(concept), self._description(concept),
            related={"name": self._name(related), "description": self._description(related)} if related else None,
            covered=list(s.taught_beats))
        if not check:
            # Keep the task pending for retry; no rubric means no meaningful open-answer grade.
            check = Check(question=f"How would you teach {self._name(concept)} to a friend? "
                          "Include an example and explain why it works.", expected_answer=self._description(concept))
            kind, related = "teachback", None
        s.question = Question(concept, kind, check.question, check.expected_answer, related=related)
        plan = card("Connect the ideas" if related else "Teach it back", [])
        plan.concept_id = concept
        return self._compose(intro, plan, s.question)

    def _retrieval_answer(self, s, user, q, text):
        graded = self.author.assess_retrieval(q.prompt, q.expected, text) if self.author and hasattr(self.author, "assess_retrieval") else None
        if s.validate_write:
            s.validate_write()
        if graded is None:
            s.question = q
            return self._ask_card(q, [self._say(s, "grader_down")])  # Nothing is scored.
        evidence = {"kind": q.kind, "question": q.prompt, "rubric": q.expected,
                    "demonstrated": graded.demonstrated, "missing": graded.missing,
                    "model": self.author.model, "related": q.related}
        feedback = graded.feedback.strip()[:600]
        self.reviews.log_attempt(user, q.concept, q.kind, graded.score,
                                f"{q.event_id}:{q.retries}", evidence)
        if q.kind == "prediction":
            # Microchecks guide feedback, but closely spaced repetitions aren't independent SRS reviews.
            if graded.score < 5 and q.retries == 0:
                q.retries += 1
                s.question = q
                return self._reask(s, q, [feedback, self._say(s, "explain_again")])
            if s.remaining_beats:
                return self._small_step(s, [feedback])
            return self._consolidate(s, user, q.concept, [feedback])
        with self.graph.transaction():
            if q.kind == "synthesis":
                human = self.graph.human(user)
                edge = tuple(sorted((q.concept, q.related)))
                if edge in human.snapshot().edges:
                    human.assess_familiarity(q.concept, q.related, graded.score,
                                            method="synthesis_retrieval", provenance=evidence)
                else:
                    human.connect(q.concept, q.related, familiarity=graded.score,
                                  method="synthesis_retrieval", provenance=evidence)
            else:
                self.goals.record(user, q.concept, graded.score, "active_recall", evidence, link_missing=False)
                self.reviews.record(user, q.concept, graded.score, q.event_id, evidence)
        if q.kind == "synthesis":
            # Connecting two ideas is followed by independent retrieval of the current idea.
            check = self.author.practice("teachback", self._name(q.concept), self._description(q.concept),
                                         covered=list(s.taught_beats))
            check = check or Check(question=f"Teach {self._name(q.concept)} to a friend, with an example and reasoning.",
                                   expected_answer=self._description(q.concept))
            s.question = Question(q.concept, "teachback", check.question, check.expected_answer)
            return self._ask_card(s.question, [feedback])
        if s.warmup_active:
            return self._next_recall(s, user, [feedback])
        if graded.score < 5 and q.kind in ("teachback", "recall"):
            s.covered.add(q.concept)
            s.mode = "done"
            return self._card(s, "A useful gap to work on", [feedback, self._say(s, "gap")])
        s.covered.add(q.concept)
        s.previous = q.concept
        return self._after_lesson(s, user, [feedback])

    def _recall(self, s, user, text, warmup=False):
        if s.warmup_active:
            return self._ask_card(s.question, [self._say(s, "recall_wait")]) if s.question else self._next_recall(s, user, [])
        if warmup:
            topics = self.reviews.suggestions(user)["topics"]
            due = [t["concept_id"] for t in topics if t["due"]]
            concepts = []
            while due and len(concepts) < 5:
                # Interleave eligible subjects without overriding the scheduler's due filter.
                neighbors = set(self.graph.god.neighbors(concepts[-1])) if concepts else set()
                pick = next((c for c in due if c not in neighbors), due[0])
                concepts.append(pick)
                due.remove(pick)
        else:
            concept = self._find(text) if text else s.viewed or s.current or s.target
            concepts = [concept] if concept else []
        if not concepts:
            return self._card(s, "Choose your learning day", [self._say(s, "nothing_due"), self._say(s, "idle")])
        s.resume_question, s.warmup_queue, s.warmup_active = s.question, deque(concepts), True
        return self._next_recall(s, user, [self._say(s, "recall_start")])

    def _next_recall(self, s, user, intro):
        if not s.warmup_queue:
            s.warmup_active = False
            s.question, s.resume_question = s.resume_question, None
            if s.question:
                return self._ask_card(s.question, intro + [self._say(s, "back_to_it")])
            s.mode = "done"
            return self._card(s, "Warm-up complete", intro + [self._say(s, "warmup_done"), self._say(s, "stop_or_new")])
        concept = s.warmup_queue.popleft()
        check = self.author.practice("recall", self._name(concept), self._description(concept)) if self.interactive else None
        if check:
            q = Question(concept, "recall", check.question, check.expected_answer)
        else:
            q = self._question(concept, "check")
            if q:
                q.kind = "recall_choice" if q.choices else "recall"
            else:
                q = Question(concept, "recall", f"Teach {self._name(concept)} to a friend from memory, with an example.", self._description(concept))
        s.question = q
        plan = self._ask_card(q, intro)
        plan.concept_id = concept
        return plan

    def _skip(self, s, user):
        q, s.question = s.question, None
        # Skipping never scores anything or changes the review schedule.
        if s.warmup_active:
            return self._next_recall(s, user, [self._say(s, "skipped")])
        if (s.step_check or (q and q.kind.startswith("prediction"))) and s.remaining_beats:
            s.step_check = False
            return self._small_step(s, [self._say(s, "skip_step")])
        if q:
            s.covered.add(q.concept)
        return self._teach_next(s, user, [self._say(s, "skipped")])

    def _lesson(self, concept, user, s, request=None):
        query = (request or self._name(concept)).casefold()
        if "complex" in query and ("root" in query or "quadratic" in query):
            planned = self.planner(request or self._name(concept))
            if planned.recipe_id == "complex_quadratic_v1":
                check = Check(question="At a complex root, which parts of the output must be zero?",
                              expected_answer="Both the real and imaginary parts must be zero.")
                self.library.save(user, concept, planned, check)
                return planned, check
        topic = self.curriculum.by_id.get(concept)
        if request is None and topic and topic.recipe:
            # Canonical authored examples need no parameter-selection provider round trip.
            return authored_plan(topic.recipe), None
        if request is None and topic and topic.key in AUTHORED:
            return self._scene(topic), None
        if request is None:
            saved = self.library.get(user, concept)
            if saved:
                return saved
        if self.author:
            context = self._context(user, s, concept)
            written = self.author.explain(self._name(concept), self._description(concept), context, request,
                                          screen=self._screen(s), memory=self.memory.for_learner(user))
            if written:
                written.plan.graph_context = context
                self._remember(user, concept, written)
                return written.plan, written.check
        if request is not None and topic and (topic.recipe or topic.key in AUTHORED):
            return self._lesson(concept, user, s)[0], None
        saved = self.library.get(user, concept)
        if saved:
            return saved
        if self.author and topic is None:
            raise GoalError("The topic was created, but its diagram lesson couldn't be generated yet. "
                            "Try the topic again. Check the backend logs if it keeps failing.")
        return concept_card(topic or Topic(concept, self._name(concept), "", 2, self._description(concept)
                                           or "No explanation is available yet.", "", (), 0)), None

    def _scene(self, topic):
        """A built-in lesson: its checked words and numbers, drawn as an animated 3D scene.

        The hand-made (flat) diagram is the reference and the fallback. Scenes are shared by all
        learners, so each topic is illustrated once; a request already drawing it is waited for.
        """
        lesson = AUTHORED[topic.key]
        if not (self.author and hasattr(self.author, "visualize")):
            return compile_lesson(lesson)
        with self.scenes_lock:
            cached = self.scenes.get(topic.key)
            pending = None if cached else self.scenes_pending.get(topic.key)
            drawing = cached is None and pending is None
            if drawing:
                pending = self.scenes_pending[topic.key] = threading.Event()
        if cached is None and not drawing:
            pending.wait(timeout=90)
            with self.scenes_lock:
                cached = self.scenes.get(topic.key)
        if cached is not None or not drawing:
            return cached.model_copy(deep=True) if cached is not None else compile_lesson(lesson)
        try:  # Drawn outside the lock: other learners' turns never wait on it.
            script = LessonScript(title=lesson.title, check=None, steps=[
                ScriptStep(say=beat.say, show=f"What this sentence describes; the flat version points at {beat.focus}.")
                for beat in lesson.beats])
            plan = self.author.visualize(topic.name, script, reference=lesson)
            if plan.generated:  # The flat fallback is not cached: the next visit tries again.
                with self.scenes_lock:
                    self.scenes[topic.key] = plan
            return plan.model_copy(deep=True)
        finally:
            with self.scenes_lock:
                self.scenes_pending.pop(topic.key, None)
            pending.set()

    def _prefetch(self, concept):
        """Start drawing an upcoming built-in lesson while the learner works on this one."""
        topic = self.curriculum.by_id.get(concept)
        if (topic and topic.key in AUTHORED and not topic.recipe and self.author and hasattr(self.author, "visualize")
                and topic.key not in self.scenes and topic.key not in self.scenes_pending):
            threading.Thread(target=self._scene, args=(topic,), daemon=True, name=f"scene-{topic.key}").start()

    def _clarify(self, s, user, concept, request, intro):
        # Not drawn yet if it breaks the topic down: the smaller topics usually come first.
        written = self.author.explain(self._name(concept), self._description(concept), self._context(user, s),
                                      request, screen=self._screen(s), memory=self.memory.for_learner(user),
                                      draw_breakdowns=False) if self.author else None
        if written and written.prerequisites:
            # Break it down: each missing idea is its own small lesson, most basic first, before
            # (and ahead of any siblings still queued) returning to this one.
            s.subtopics.extendleft((name, concept) for name in reversed(written.prerequisites))
            first = self._next_subtopic(s, user, concept)
            if first:
                s.detours.append(concept)
                s.detour_topics[concept] = [self._name(first)]
                return self._teach(s, user, first, intro + [self._say(s, "detour", topic=self._name(first))])
        s.current, s.mode = concept, "checking"
        if written and written.plan is None:  # Nothing smaller left to learn: draw it after all.
            written.plan = self.author.visualize(self._name(concept), written.script, self._screen(s))
        if written:
            self._remember(user, concept, written)
            plan = written.plan
            intro = intro + [self._say(s, "confused")]
        else:
            plan = self._lesson(concept, user, s)[0]
            intro = intro + [self._say(s, "slow_down")]
        plan.concept_id = concept
        return self._activate(s, plan, intro)

    def _confused(self, s, user, text):
        if s.warmup_active and s.question:
            concept = s.question.concept
            return self._revisit(s, user, self._name(concept), review=True)
        if s.mode == "diagnosing" and s.question:
            return self._answer(s, user, "not sure")
        if s.current is None:
            return self._ask(s, user, text or "Can you explain that more simply?")
        # Asking for help never counts against the learner; only wrong answers use up attempts.
        return self._clarify(s, user, s.current, text or "I don't understand this part.", [])

    def _ask(self, s, user, text, guided=False):
        """A free question: answered with a diagram; any pending check question stays pending."""
        # Resolve the actual question before using the current lesson as a fallback.
        concept = self._find(text, create=True)
        if concept is None and re.search(r"\b(this|that|it|why|again)\b", text, re.I):
            concept = s.viewed or s.current or s.target
        if concept:
            topic = self.curriculum.by_id.get(concept)
            simple = re.sub(r"^(?:explain|teach me|what is|what are|learn)\s+(?:the\s+)?", "", text.casefold()).rstrip("?.! ")
            canonical = topic and simple in (topic.name.casefold(), *topic.aliases)
            plan, _ = self._lesson(concept, user, s, request=None if canonical else text)
            plan.concept_id = concept
            if guided:
                s.target, s.current, s.mode = concept, concept, "checking"
                s.covered, s.previous, s.detours, s.subtopics = set(), None, [], deque()
                return self._activate(s, plan, [])
            return self._remind(s, plan, [])
        return self._remind(s, self.goals.tag(text, self.planner(text)), [])

    def _revisit(self, s, user, text, review=False):
        if s.warmup_active:
            s.warmup_active, s.warmup_queue, s.resume_question = False, deque(), None
        # Resolve explicitly named topics; short referential requests stay on this lesson.
        named = text and (self.curriculum.match(text) or re.search(r"\b(?:about|review)\s+\w", text, re.I))
        try:
            exact = self.graph.god.get_concept(text.strip()).id if text else None
        except (NotFound, AmbiguousConcept, ValueError):
            exact = None
        concept = exact or (self._find(text, create=True) if named else None) or s.viewed or s.current or s.target
        if concept is None:
            return self._goal(s, user, text) if text else self._continue(s, user)
        s.current, s.mode = concept, "checking"
        s.recent.append(self._name(concept))
        request = text or ("Review this with a fresh example." if review else
                           "Go deeper, building on what I already know with a new example and diagram.")
        plan, _ = self._lesson(concept, user, s, request=request)
        plan.concept_id = concept
        return self._activate(s, plan, [self._say(s, "review" if review else "deeper", topic=self._name(concept))])

    def _continue(self, s, user):
        if s.question:
            return self._reask(s, s.question, [self._say(s, "again")])
        if s.step_check and s.active_plan:
            s.step_check = False
            question = self._step_question(s)
            if question:
                s.question = question
                return self._reask(s, question, [])
        if s.remaining_beats and s.active_plan:
            return self._small_step(s, [])
        if s.mode == "checking" and s.current:
            s.covered.add(s.current)
            return self._after_lesson(s, user, [])
        if s.mode == "done" and s.target:
            return self._revisit(s, user, "", review=False)
        return self._card(s, "What would you like to learn?", [self._say(s, "idle")])

    def _finish(self, s, user, intro):
        topic = self.curriculum.by_id.get(s.target)
        plan = compile_lesson(AUTHORED[topic.key]) if topic and topic.key in AUTHORED else (
            self.planner(topic.recipe) if topic and topic.recipe else card(self._name(s.target), []))
        plan.beats = self._joined([], before=" ".join(glue(intro)), after=self._say(s, "what_next"), beat_id="next")
        return plan

    # ---- plans -----------------------------------------------------------------------------
    # Every turn: a short reaction and transition (at most two "glue" lines, which the voice may
    # rephrase), the lesson step, then one hand-off spoken last, then silence.
    def _compose(self, intro, plan: LessonPlan, question: Question | None, ready: str | None = None):
        beats = [beat.model_copy(update={"beat_id": f"l{i}"}) for i, beat in enumerate(plan.beats)]
        if question:
            beats.append(TeachingBeat(beat_id="check", say_text=question.spoken, focus_ids=[]))
        elif ready:
            beats = self._joined(beats, after=ready, beat_id="ready")
        plan.beats = self._joined(beats, before=" ".join(glue(intro)))
        return plan

    @staticmethod
    def _joined(beats, before="", after="", beat_id="i0"):
        """One note per turn: the lead-in and hand-off are part of the step, not notes of their own."""
        if not (before or after):
            return beats
        if not beats:
            return [TeachingBeat(beat_id=beat_id, say_text=" ".join(t for t in (before, after) if t), focus_ids=[])]
        beats = list(beats)
        if before:
            beats[0] = beats[0].model_copy(update={"say_text": f"{before} {beats[0].say_text}"})
        if after:
            beats[-1] = beats[-1].model_copy(update={"say_text": f"{beats[-1].say_text} {after}"})
        return beats

    def _ask_card(self, question: Question, intro):
        offered = question.offered or ()
        lines = [question.prompt] + [f"{'ABCD'[i]}. {c}" for i, c in enumerate(offered)]
        plan = card("Quick question", [])
        y = 2.4
        for i, line in enumerate(line for text in lines for line in _wrap(text)):
            if len(plan.nodes) < MAX_OBJECTS:
                plan.nodes.append(Node(id=f"q{i}", content=Label(text=line, position=[0.0, round(y, 2)])))
                y -= 0.9
        plan.beats = self._joined([TeachingBeat(beat_id="question", say_text=question.spoken,
                                                focus_ids=[n.id for n in plan.nodes if n.id.startswith("q")])],
                                  before=" ".join(glue(intro)))
        return plan

    def _card(self, s, title, lines):
        plan = card(title, [])
        plan.beats = self._joined([], before=" ".join(glue(lines)))
        return plan

    def _remind(self, s, plan, intro):
        plan = self._compose(intro, plan, None)
        if s.question:
            plan.beats = self._joined(plan.beats, after=f"{self._say(s, 'again')} {s.question.spoken}")
        return plan

    # ---- questions, names and concepts -----------------------------------------------------
    def _question(self, concept, kind) -> Question | None:
        topic = self.curriculum.by_id.get(concept)
        if kind == "check" and self.author:  # A fresh question, not the starting-check one again.
            check = self.author.question(self._name(concept), self._description(concept))
            if check:
                return Question(concept, kind, check.question, check.expected_answer)
        if topic and topic.question:
            answer = topic.choices[topic.answer]
            choices = tuple(self.phrasing.shuffle(topic.choices))  # The answer isn't always first.
            return Question(concept, kind, topic.question, answer, choices, choices.index(answer))
        if self.author:
            check = self.author.question(self._name(concept), self._description(concept))
            if check:
                return Question(concept, kind, check.question, check.expected_answer)
        return None

    def _name(self, concept):
        topic = self.curriculum.by_id.get(concept)
        return topic.name if topic else self.graph.god.get_concept(concept).canonical_name

    def _description(self, concept):
        topic = self.curriculum.by_id.get(concept)
        return topic.description if topic else self.graph.god.get_concept(concept).description

    def _context(self, user, s, concept=None):
        target = concept or s.current or s.target
        context = self.goals.teaching_context(user, target) if target else {}
        if s.target and s.target != target:
            context["goal_teaching_sequence"] = self.goals.teaching_context(user, s.target)["teaching_sequence"]
        known = [n["name"] for n in context.get("nearest_concepts", []) if n["known"]]
        return {**context, "goal": self._name(s.target) if s.target else None,
                "current_concept": self._name(target) if target else None,
                "knows_well": known, "recently_covered": list(s.recent)}

    def _remember(self, user, concept, written):
        """Persist validated teaching, then its atomic facts through audited proposals.

        Exposure is not mastery: only graded answers add personal understanding.
        """
        written.plan.concept_id = concept
        with self.graph.transaction():
            self.library.save(user, concept, written.plan, written.check)
            facts = getattr(written, "facts", [])[:6]
            # A single proposal permits links between facts introduced by the same explanation.
            if facts:
                self._store_specs(facts, evidence=f"Facts taught in {self._name(concept)}")

    def _find(self, text, create=False, link_to=None):
        """Curriculum alias, exact God concept name, the resolver, then (optionally) a new concept."""
        query = re.sub(r"^(?:(?:i (?:want|would like) to |can you |please )?(?:learn(?: more about)?|understand|teach me|explain|review|what (?:is|are))\s+)(?:the\s+)?", "", text.strip(), flags=re.I).rstrip("?.! ")
        concept = None
        try:
            concept = self.graph.god.get_concept(query).id
        except (NotFound, AmbiguousConcept, ValueError):
            pass
        if concept is None:
            concept = next((self.curriculum.ids[t.key] for t in self.curriculum.topics
                            if query.casefold() in (t.name.casefold(), *t.aliases)), None)
        # With an author available, preserve specific new topics such as "even functions"
        # instead of swallowing them with the broad substring alias "function".
        if concept is None and create and self.author:
            concept = self._create(text, link_to)
            if concept is None:
                raise GoalError("The topic writer couldn't create that lesson. Check the backend's "
                                "Anthropic configuration and try again. Existing lessons still work.")
        if (concept is None and create and not self.author and self.curriculum.match(text) is None
                and authored_plan(text).recipe_id == "whiteboard_v1"):
            raise GoalError("New topics need ANTHROPIC_API_KEY on the backend. Restart the backend "
                            "after setting it. You can explore the built-in topics now.")
        if concept is None:
            concept = self.curriculum.match(text)
        if concept is None and self.goals.resolver is not None:
            concept = self.goals.resolver(text)
        return concept

    def _create(self, request, link_to=None):
        """New shared-graph concept through the engine's proposal API (validated, audited)."""
        snapshot = self.graph.snapshot()
        names = {node.label: node.id for node in snapshot.nodes.values()}
        words = set(re.findall(r"[a-z]{3,}", request.casefold())) - {"the", "and", "learn", "about", "teach", "want"}
        candidates = sorted(names, key=lambda name: (
            -len(words & set(re.findall(r"[a-z]{3,}", name.casefold()))), name.casefold()))[:60]
        spec = self.author.concept(request, candidates)
        if spec is None or not spec.name.strip():
            return None
        ids = self._store_specs([spec], evidence="Learner requested a concept", link_to=link_to)
        return ids[0] if ids else None

    def _store_specs(self, specs, evidence, link_to=None):
        # The transaction resolves concurrent duplicates before building revision-bound proposals.
        with self.graph.transaction():
            return self._store_specs_locked(specs, evidence, link_to)

    def _store_specs_locked(self, specs, evidence, link_to):
        snapshot = self.graph.snapshot()
        names = {node.label.casefold(): node.id for node in snapshot.nodes.values()}
        provenance = {"source": "claude", "model": self.author.model}
        commands, ids = [], []
        for spec in specs:
            name = re.sub(r"\s+", " ", spec.name.strip())[:80]
            if not name or not spec.description.strip():
                continue
            if name.casefold() not in names:
                names[name.casefold()] = str(uuid4())
                commands.append({"operation": "add_concept", "arguments": {
                    "concept_id": names[name.casefold()], "name": name,
                    "description": spec.description.strip()[:600]}})
            ids.append((spec, names[name.casefold()]))
        edges = set(snapshot.edges)
        for spec, concept_id in ids:
            linked = {names[link.concept.casefold()]: max(1, min(10, link.similarity))
                      for link in spec.links if link.concept.casefold() in names}
            if link_to:
                linked.setdefault(link_to, 7)
            for other, similarity in linked.items():
                edge = tuple(sorted((concept_id, other)))
                if other == concept_id or edge in edges:
                    continue
                edges.add(edge)
                commands.append({"operation": "connect_god", "arguments": {
                    "a": concept_id, "b": other, "similarity": similarity, "provenance": provenance}})
        if not commands:
            return [c for _, c in ids]
        proposal = self.graph.proposals.submit({
            "source": f"claude:{self.author.model}", "evidence": [evidence],
            "confidence": 0.5, "expected_revisions": {"god": self.graph.god.revision}, "commands": commands})
        self.graph.proposals.accept(proposal)
        return [c for _, c in ids]
