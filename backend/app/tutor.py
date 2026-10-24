"""The conversational tutor: goal -> spoken check -> silent cheapest route -> teach -> check.

One Session per lesson. Every reply is a LessonPlan with a diagram and spoken beats. The
route is never shown or spoken; the tutor just teaches the next concept on it. Scores change
only through graded answers (diagnostic or check questions), never from conversation alone.
Confusion triggers a deeper explanation, or a detour to a prerequisite the author identifies;
concepts the graph lacks are created through the engine's proposal API with provenance.
"""
from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from memory_graph import AmbiguousConcept, NotFound

from .author import Check, grade_locally
from .diagrams import AUTHORED, compile_lesson
from .goals import KNOWN, GoalError, Topic, _wrap, concept_card
from .learning import LessonPlan, TeachingBeat
from .library import LessonLibrary
from .reviews import Reviews
from .scene import Label, MAX_OBJECTS, Node

DIAGNOSTIC_SCORES = {"correct": 7.0, "partly": 4.0, "wrong": 2.0, "unsure": 1.0}
CHECK_SCORES = {"correct": 8.0, "partly": 5.0, "wrong": 3.0, "unsure": 2.0}
MAX_QUESTIONS = 8  # Starting-check budget; the frontier search usually needs far fewer.
MAX_CLARIFY = 2  # Deeper explanations per concept before moving on.
ACTIONS = ("goal", "answer", "confused", "question", "continue", "explore", "review", "deeper", "recall", "warmup", "skip")
REVISIT = re.compile(r"\b(review|revisit|go deeper|learn more|tell me more|teach me more|more detail|again)\b", re.I)
# First option is correct. These are tiny transfer/prediction tasks, not copied explanations.
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
    kind: str  # "diagnostic" or "check"
    prompt: str
    expected: str
    choices: tuple[str, ...] | None = None
    correct: int | None = None
    related: str | None = None
    event_id: str = field(default_factory=lambda: uuid4().hex)
    retries: int = 0

    @property
    def spoken(self):
        if not self.choices:
            return self.prompt
        options = ", ".join(self.choices[:-1]) + ", or " + self.choices[-1]
        return f"{self.prompt} Is it {options}?"


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
    detours: list = field(default_factory=list)
    attempts: dict = field(default_factory=dict)
    covered: set = field(default_factory=set)
    recent: deque = field(default_factory=lambda: deque(maxlen=6))
    active_plan: LessonPlan | None = None
    remaining_beats: list = field(default_factory=list)
    taught_beats: list = field(default_factory=list)
    final_question: Question | None = None
    warmup_queue: deque = field(default_factory=deque)
    warmup_active: bool = False
    resume_question: Question | None = None
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
    def __init__(self, graph, goals, planner, author=None, active_learning=True):
        self.graph, self.goals, self.planner, self.author = graph, goals, planner, author
        self.curriculum = goals.curriculum
        self.library = LessonLibrary(graph)
        self.reviews = Reviews(graph)
        self.active_learning = active_learning

    @property
    def interactive(self):
        return self.active_learning and self.author is not None and hasattr(self.author, "practice")

    # ---- entry point -----------------------------------------------------------------------
    def act(self, session: Session, user, action, text="") -> LessonPlan:
        if action not in ACTIONS:
            raise ValueError("Unknown tutor action")
        text = text.strip()
        # A request for more teaching is never a quiz answer, even if a check is pending.
        if action in ("recall", "warmup"):
            plan = self._recall(session, user, text, warmup=action == "warmup")
        elif action == "skip":
            plan = self._skip(session, user)
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
        return plan

    # ---- goal and starting check -----------------------------------------------------------
    def _goal(self, s, user, text):
        target = self._find(text, create=True)
        if target is None:
            raise GoalError("I couldn't find that topic yet. Try: "
                            + ", ".join(t.name for t in self.curriculum.topics if t.level >= 2) + ".")
        s.target, s.queue, s.asked, s.question = target, deque([target]), set(), None
        s.current, s.previous, s.detours, s.attempts, s.covered = None, None, [], {}, set()
        s.viewed = None
        s.active_plan, s.remaining_beats, s.taught_beats = None, [], []
        s.warmup_active, s.warmup_queue, s.resume_question = False, deque(), None
        s.mode = "diagnosing"
        members = self.graph.human(user).snapshot().nodes
        if target in members and members[target].understanding >= KNOWN:
            return self._revisit(s, user, text, review=False)
        return self._next_question(s, user, [f"Let's work toward {self._name(target)}. First, a few quick "
                                             "questions so I know where to start."])

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
        return self._teach_next(s, user, intro + ["Thanks, that tells me where to start."])

    def _expand(self, s, concept):
        level = {self.curriculum.ids[t.key]: t.level for t in self.curriculum.topics}
        for neighbor in sorted(self.graph.god.neighbors(concept), key=lambda n: -level.get(n, 2)):
            if neighbor not in s.asked and neighbor not in s.queue:
                s.queue.append(neighbor)

    # ---- answers ---------------------------------------------------------------------------
    def _answer(self, s, user, text):
        q, s.question = s.question, None
        if q.kind in ("prediction", "synthesis", "teachback", "recall"):
            return self._retrieval_answer(s, user, q, text)
        verdict, feedback = self._grade(q, text)
        if s.validate_write:
            s.validate_write()
        self.reviews.log_attempt(user, q.concept, q.kind, CHECK_SCORES[verdict],
                                f"{q.event_id}:{q.retries}", {"verdict": verdict, "question": q.prompt})
        if q.kind == "prediction_choice":
            if verdict not in ("correct", "partly") and q.retries == 0:
                q.retries += 1
                s.question = q
                return self._ask_card(q, [feedback, "Try this small step once more."])
            return self._small_step(s, [feedback])
        if q.kind == "diagnostic":
            self.goals.record(user, q.concept, DIAGNOSTIC_SCORES[verdict], "voice_diagnostic",
                              {"question": q.prompt, "verdict": verdict}, link_missing=not self.interactive)
            if verdict != "correct":
                self._expand(s, q.concept)
            return self._next_question(s, user, [feedback])
        self.goals.record(user, q.concept, CHECK_SCORES[verdict], "voice_check",
                          {"question": q.prompt, "verdict": verdict}, previous=s.previous if not self.interactive else None,
                          link_missing=not self.interactive)
        if not self.interactive or q.kind == "recall_choice":
            self.reviews.record(user, q.concept, CHECK_SCORES[verdict], q.event_id,
                                {"kind": q.kind, "verdict": verdict})
        if q.kind == "recall_choice" and s.warmup_active:
            return self._next_recall(s, user, [feedback])
        if verdict in ("correct", "partly"):
            if self.interactive:
                return self._consolidate(s, user, q.concept, [feedback])
            s.covered.add(q.concept)
            s.previous = q.concept
            if s.detours:
                resume = s.detours.pop()
                return self._teach(s, user, resume, [feedback, f"Now, back to {self._name(resume)}."])
            return self._teach_next(s, user, [feedback])
        s.attempts[q.concept] = s.attempts.get(q.concept, 0) + 1
        if s.attempts[q.concept] > MAX_CLARIFY:
            s.covered.add(q.concept)
            return self._teach_next(s, user, [feedback, "Let's keep going, and come back to this later."])
        return self._clarify(s, user, q.concept, f"They answered {text!r} to the question: {q.prompt}", [feedback])

    def _grade(self, q, text):
        if self.author and not q.choices:
            graded = self.author.grade(q.prompt, q.expected, text)
            if graded:
                return graded
        if self.author and q.choices and not grade_locally(text, q.expected, q.choices, q.correct)[0] in ("correct", "wrong"):
            graded = self.author.grade(q.prompt, q.expected, text, q.choices)
            if graded:
                return graded
        return grade_locally(text, q.expected, q.choices, q.correct)

    # ---- teaching --------------------------------------------------------------------------
    def _teach_next(self, s, user, intro):
        if s.target is None:
            s.mode = "idle"
            return self._card(s, "What would you like to learn?", intro + ["Tell me what you'd like to learn."])
        route = self.goals.route(user, s.target)  # Silent: only its next step is ever used.
        if route["status"] == "known":
            if self.interactive and s.target not in s.covered:
                s.current, s.mode = s.target, "checking"
                return self._consolidate(s, user, s.target, intro)
            s.mode = "done"
            return self._finish(s, user, intro + [f"You've got {self._name(s.target)}. Nicely done."])
        step = next((step["id"] for step in route["steps"] if not step["known"] and step["id"] not in s.covered), None)
        if self.interactive and route["total_cost"] is None:
            context = self.goals.teaching_context(user, s.target)
            step = next((n["id"] for n in context["teaching_sequence"]
                         if not n["known"] and n["id"] not in s.covered), step)
        if step is None and route["status"] == "needs_diagnostic":
            step = s.target
        if step is None:
            s.mode = "done"
            return self._finish(s, user, intro + [f"We've worked through everything on the way to "
                                                  f"{self._name(s.target)}. Ask me anything, or set a new goal."])
        return self._teach(s, user, step, intro + [f"Next, let's look at {self._name(step)}."])

    def _teach(self, s, user, concept, intro):
        s.current, s.mode = concept, "checking"
        s.recent.append(self._name(concept))
        plan, check = self._lesson(concept, user, s)
        plan.concept_id = concept
        s.question = self._as_question(concept, check) or self._question(concept, "check")
        return self._activate(s, plan, intro)

    def _activate(self, s, plan, intro):
        if self.active_learning and len(plan.beats) > 1:
            s.active_plan = plan.model_copy(deep=True)
            s.remaining_beats, s.taught_beats, s.final_question = list(plan.beats), [], s.question
            return self._small_step(s, intro)
        return self._compose(intro, plan, s.question)

    def _small_step(self, s, intro):
        """Reveal one short beat, then wait for an actual learner response."""
        beat = s.remaining_beats.pop(0)
        s.taught_beats.append(beat.say_text)
        plan = s.active_plan.model_copy(deep=True)
        plan.beats = [beat]
        if not s.remaining_beats:
            s.question = s.final_question
            return self._compose(intro, plan, s.question)
        revealed = {"title", "axes"} | {node.id for node in plan.nodes if node.content.primitive == "slider"}
        revealed.update(node_id for b in s.active_plan.beats[:len(s.taught_beats)] for node_id in b.focus_ids)
        plan.nodes = [node.model_copy(update={"visible": node.id in revealed}) for node in plan.nodes]
        check = self.author.practice("prediction", self._name(s.current), self._description(s.current),
                                     covered=list(s.taught_beats)) if self.interactive else None
        if check:
            s.question = Question(s.current, "prediction", check.question, check.expected_answer)
            return self._compose(intro, plan, s.question)
        topic = self.curriculum.by_id.get(s.current)
        if topic and topic.key in PREDICTIONS:
            prompt, correct, wrong = PREDICTIONS[topic.key]
            # One authored prediction per concept; later steps pause for self-explanation.
            if len(s.taught_beats) == 1:
                s.question = Question(s.current, "prediction_choice", prompt, correct, (correct, wrong), 0)
                return self._compose(intro, plan, s.question)
        # Never turn a provider outage into a fake grade or a long autoplay lesson.
        s.question = None
        plan.beats.append(TeachingBeat(beat_id="pause", say_text="Pause and explain that step to yourself. "
                                      "Choose Continue when you're ready for the next small step.", focus_ids=[]))
        return self._compose(intro, plan, None)

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
            return self._ask_card(q, ["I couldn't evaluate that answer right now. Your mastery hasn't changed. "
                                      "Try again, or choose Skip."])
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
                plan = s.active_plan.model_copy(deep=True)
                plan.beats = []
                return self._compose([feedback, "Try explaining the reasoning once more in your own words."], plan, q)
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
            return self._card(s, "A useful gap to work on", [feedback,
                "This needs another try after a short break. Review the explanation, or choose a new goal."])
        s.covered.add(q.concept)
        s.previous = q.concept
        if s.detours:
            return self._teach(s, user, s.detours.pop(), [feedback])
        return self._teach_next(s, user, [feedback])

    def _recall(self, s, user, text, warmup=False):
        if s.warmup_active:
            return self._ask_card(s.question, ["Finish this recall attempt, or choose Skip."]) if s.question else self._next_recall(s, user, [])
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
            return self._card(s, "Choose your learning day", ["No reviews are due yet. Choose a new topic, "
                                                             "or name a topic for active recall."])
        s.resume_question, s.warmup_queue, s.warmup_active = s.question, deque(concepts), True
        return self._next_recall(s, user, ["Let's retrieve before looking at the explanation. Take your time."])

    def _next_recall(self, s, user, intro):
        if not s.warmup_queue:
            s.warmup_active = False
            s.question, s.resume_question = s.resume_question, None
            if s.question:
                return self._ask_card(s.question, intro + ["Warm-up complete. Back to where you left off."])
            s.mode = "done"
            return self._card(s, "Warm-up complete", intro + ["Choose something new to learn, or stop here for today."])
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
        if s.warmup_active:
            return self._next_recall(s, user, ["Skipped. Your mastery and review schedule are unchanged."])
        if q and q.kind == "prediction" and s.remaining_beats:
            return self._small_step(s, ["Let's try the next small step."])
        if q:
            s.covered.add(q.concept)
        return self._teach_next(s, user, ["Skipped. No mastery awarded."])

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
            return self.planner(topic.recipe), None
        if request is None and topic and topic.key in AUTHORED:
            return compile_lesson(AUTHORED[topic.key]), None
        if request is None:
            saved = self.library.get(user, concept)
            if saved:
                return saved
        if self.author:
            context = self._context(user, s, concept)
            written = self.author.explain(self._name(concept), self._description(concept), context, request)
            if written:
                written.plan.graph_context = context
                self._remember(user, concept, written)
                return written.plan, written.check
        if request is not None and topic and (topic.recipe or topic.key in AUTHORED):
            return self._lesson(concept, user, s)[0], None
        saved = self.library.get(user, concept)
        if saved:
            return saved
        return concept_card(topic or Topic(concept, self._name(concept), "", 2, self._description(concept)
                                           or "No explanation is available yet.", "", (), 0)), None

    def _clarify(self, s, user, concept, request, intro):
        written = self.author.explain(self._name(concept), self._description(concept), self._context(user, s),
                                      request) if self.author else None
        if written and written.prerequisite:
            prerequisite = self._find(written.prerequisite, create=True, link_to=concept)
            members = self.graph.human(user).snapshot().nodes
            if prerequisite and prerequisite != concept and (
                    prerequisite not in members or members[prerequisite].understanding < KNOWN):
                s.detours.append(concept)
                return self._teach(s, user, prerequisite, intro + [
                    f"I think the missing piece is {self._name(prerequisite)}. Let's look at that first."])
        s.current, s.mode = concept, "checking"
        if written:
            self._remember(user, concept, written)
            plan, check = written.plan, written.check
        else:
            plan, check = self._lesson(concept, user, s)[0], None
            intro = intro + ["Let's slow down and go through it again."]
        s.question = self._as_question(concept, check) or self._question(concept, "check")
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
        return self._clarify(s, user, s.current, text or "I don't understand this part.",
                             ["Good question. Let's dig into that."])

    def _ask(self, s, user, text, guided=False):
        """A free question: answered with a diagram; any pending check question stays pending."""
        # Resolve the actual question before using the current lesson as a fallback.
        concept = self._find(text, create=True)
        if concept is None and re.search(r"\b(this|that|it|why|again)\b", text, re.I):
            concept = s.viewed or s.current or s.target
        if concept:
            plan, check = self._lesson(concept, user, s, request=text)
            plan.concept_id = concept
            if guided:
                s.target, s.current, s.mode = concept, concept, "checking"
                s.covered, s.previous, s.detours = set(), None, []
                s.question = self._as_question(concept, check) or self._question(concept, "check")
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
        plan, check = self._lesson(concept, user, s, request=request)
        plan.concept_id = concept
        s.question = self._as_question(concept, check) or self._question(concept, "check")
        return self._activate(s, plan, [f"Let's {'review' if review else 'go further with'} {self._name(concept)}."])

    def _continue(self, s, user):
        if s.question:
            return self._ask_card(s.question, ["Here's the question again."])
        if s.remaining_beats and s.active_plan:
            return self._small_step(s, [])
        if s.mode == "checking" and s.current:
            s.covered.add(s.current)
            return self._teach_next(s, user, [])
        if s.mode == "done" and s.target:
            return self._revisit(s, user, "", review=False)
        return self._card(s, "What would you like to learn?", ["Tell me what you'd like to learn, and I'll take it from there."])

    def _finish(self, s, user, intro):
        topic = self.curriculum.by_id.get(s.target)
        plan = compile_lesson(AUTHORED[topic.key]) if topic and topic.key in AUTHORED else (
            self.planner(topic.recipe) if topic and topic.recipe else card(self._name(s.target), []))
        plan.beats = [TeachingBeat(beat_id=f"i{i}", say_text=text, focus_ids=[]) for i, text in enumerate(intro)] + [
            TeachingBeat(beat_id="next", say_text="What would you like to learn next?", focus_ids=[])]
        return plan

    # ---- plans -----------------------------------------------------------------------------
    def _compose(self, intro, plan: LessonPlan, question: Question | None):
        beats = [TeachingBeat(beat_id=f"i{i}", say_text=text, focus_ids=[]) for i, text in enumerate(intro) if text]
        beats += [beat.model_copy(update={"beat_id": f"l{i}"}) for i, beat in enumerate(plan.beats)]
        if question:
            beats.append(TeachingBeat(beat_id="check", say_text=f"Quick check: {question.spoken}", focus_ids=[]))
        plan.beats = beats
        return plan

    def _ask_card(self, question: Question, intro):
        lines = [question.prompt] + ([f"{'ABCD'[i]}. {c}" for i, c in enumerate(question.choices)] if question.choices else [])
        plan = card("Quick question", [])
        y = 2.4
        for i, line in enumerate(line for text in lines for line in _wrap(text)):
            if len(plan.nodes) < MAX_OBJECTS:
                plan.nodes.append(Node(id=f"q{i}", content=Label(text=line, position=[0.0, round(y, 2)])))
                y -= 0.9
        plan.beats = [TeachingBeat(beat_id=f"i{i}", say_text=text, focus_ids=[]) for i, text in enumerate(intro) if text]
        plan.beats.append(TeachingBeat(beat_id="question", say_text=question.spoken,
                                       focus_ids=[n.id for n in plan.nodes if n.id.startswith("q")]))
        return plan

    def _card(self, s, title, lines):
        plan = card(title, [])
        plan.beats = [TeachingBeat(beat_id=f"i{i}", say_text=line, focus_ids=[]) for i, line in enumerate(lines)]
        return plan

    def _remind(self, s, plan, intro):
        plan = self._compose(intro, plan, None)
        if s.question:
            plan.beats.append(TeachingBeat(beat_id="reminder", say_text="Whenever you're ready, here's the "
                                           f"question again: {s.question.spoken}", focus_ids=[]))
        return plan

    # ---- questions, names and concepts -----------------------------------------------------
    def _question(self, concept, kind) -> Question | None:
        topic = self.curriculum.by_id.get(concept)
        if kind == "check" and self.author:  # A fresh question, not the starting-check one again.
            check = self.author.question(self._name(concept), self._description(concept))
            if check:
                return Question(concept, kind, check.question, check.expected_answer)
        if topic and topic.question:
            return Question(concept, kind, topic.question, topic.choices[topic.answer], topic.choices, topic.answer)
        if self.author:
            check = self.author.question(self._name(concept), self._description(concept))
            if check:
                return Question(concept, kind, check.question, check.expected_answer)
        return None

    def _as_question(self, concept, check: Check | None):
        return Question(concept, "check", check.question, check.expected_answer) if check else None

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
            -len(words & set(re.findall(r"[a-z]{3,}", name.casefold()))), name.casefold()))[:200]
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
