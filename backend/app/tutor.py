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
from uuid import uuid4

from memory_graph import AmbiguousConcept, NotFound

from .author import Check, grade_locally
from .diagrams import AUTHORED, compile_lesson
from .goals import KNOWN, GoalError, Topic, _wrap, concept_card
from .learning import LessonPlan, TeachingBeat
from .scene import Label, MAX_OBJECTS, Node

DIAGNOSTIC_SCORES = {"correct": 7.0, "partly": 4.0, "wrong": 2.0, "unsure": 1.0}
CHECK_SCORES = {"correct": 8.0, "partly": 5.0, "wrong": 3.0, "unsure": 2.0}
MAX_QUESTIONS = 8  # Starting-check budget; the frontier search usually needs far fewer.
MAX_CLARIFY = 2  # Deeper explanations per concept before moving on.
ACTIONS = ("goal", "answer", "confused", "question", "continue", "explore")


@dataclass
class Question:
    concept: str
    kind: str  # "diagnostic" or "check"
    prompt: str
    expected: str
    choices: tuple[str, ...] | None = None
    correct: int | None = None

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
    previous: str | None = None  # Last concept passed; its edge to the next one gets rated.
    detours: list = field(default_factory=list)
    attempts: dict = field(default_factory=dict)
    covered: set = field(default_factory=set)
    recent: deque = field(default_factory=lambda: deque(maxlen=6))
    lock: threading.Lock = field(default_factory=threading.Lock)


def card(title, lines) -> LessonPlan:
    """Text on the board for prompts and questions; explanations always get real diagrams."""
    nodes, y = [Node(id="title", content=Label(text=title[:80], position=[0.0, 3.6]))], 2.4
    for i, line in enumerate(_wrap(" ".join(lines))[:8]):
        nodes.append(Node(id=f"line_{i}", content=Label(text=line, position=[0.0, round(y, 2)])))
        y -= 0.9
    return LessonPlan(recipe_id="tutor_card_v1", nodes=nodes, beats=[])


class Tutor:
    def __init__(self, graph, goals, planner, author=None):
        self.graph, self.goals, self.planner, self.author = graph, goals, planner, author
        self.curriculum = goals.curriculum

    # ---- entry point -----------------------------------------------------------------------
    def act(self, session: Session, user, action, text="") -> LessonPlan:
        if action not in ACTIONS:
            raise ValueError("Unknown tutor action")
        text = text.strip()
        if action == "goal":
            plan = self._goal(session, user, text)
        elif action == "answer" and session.question:
            plan = self._answer(session, user, text)
        elif action == "confused":
            plan = self._confused(session, user, text)
        elif action in ("question", "answer") and text:
            plan = self._ask(session, user, text)
        elif action == "explore" and text:
            plan = self._remind(session, self.goals.tag(text, self.planner(text)), [])
        else:
            plan = self._continue(session)
        plan.tutor = {"mode": session.mode, "awaiting_answer": session.question is not None,
                      "goal": self._name(session.target) if session.target else None}
        return plan

    # ---- goal and starting check -----------------------------------------------------------
    def _goal(self, s, user, text):
        target = self._find(text, create=True)
        if target is None:
            raise GoalError("I couldn't find that topic yet. Try: "
                            + ", ".join(t.name for t in self.curriculum.topics if t.level >= 2) + ".")
        s.target, s.queue, s.asked, s.question = target, deque([target]), set(), None
        s.current, s.previous, s.detours, s.attempts, s.covered = None, None, [], {}, set()
        s.mode = "diagnosing"
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
        verdict, feedback = self._grade(q, text)
        if q.kind == "diagnostic":
            self.goals.record(user, q.concept, DIAGNOSTIC_SCORES[verdict], "voice_diagnostic",
                              {"question": q.prompt, "verdict": verdict})
            if verdict != "correct":
                self._expand(s, q.concept)
            return self._next_question(s, user, [feedback])
        self.goals.record(user, q.concept, CHECK_SCORES[verdict], "voice_check",
                          {"question": q.prompt, "verdict": verdict}, previous=s.previous)
        if verdict in ("correct", "partly"):
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
            s.mode = "done"
            return self._finish(s, user, intro + [f"You've got {self._name(s.target)}. Nicely done."])
        step = next((step["id"] for step in route["steps"] if not step["known"] and step["id"] not in s.covered), None)
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
        s.question = self._as_question(concept, check) or self._question(concept, "check")
        return self._compose(intro, plan, s.question)

    def _lesson(self, concept, user, s, request=None):
        topic = self.curriculum.by_id.get(concept)
        if request is None and topic and topic.recipe:
            return self.planner(topic.recipe), None
        if request is None and topic and topic.key in AUTHORED:
            return compile_lesson(AUTHORED[topic.key]), None
        if self.author:
            written = self.author.explain(self._name(concept), self._description(concept), self._context(user, s), request)
            if written:
                return written.plan, written.check
        if request is not None and topic and (topic.recipe or topic.key in AUTHORED):
            return self._lesson(concept, user, s)[0], None
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
            plan, check = written.plan, written.check
        else:
            plan, check = self._lesson(concept, user, s)[0], None
            intro = intro + ["Let's slow down and go through it again."]
        s.question = self._as_question(concept, check) or self._question(concept, "check")
        return self._compose(intro, plan, s.question)

    def _confused(self, s, user, text):
        if s.mode == "diagnosing" and s.question:
            return self._answer(s, user, "not sure")
        if s.current is None:
            return self._ask(s, user, text or "Can you explain that more simply?")
        # Asking for help never counts against the learner; only wrong answers use up attempts.
        return self._clarify(s, user, s.current, text or "I don't understand this part.",
                             ["Good question. Let's dig into that."])

    def _ask(self, s, user, text):
        """A free question: answered with a diagram; any pending check question stays pending."""
        concept = s.current or self.curriculum.match(text)
        if self.author:
            written = self.author.explain(self._name(concept) if concept else text,
                                          self._description(concept) if concept else "", self._context(user, s), text)
            if written:
                return self._remind(s, written.plan, [])
        return self._remind(s, self.goals.tag(text, self.planner(text)), [])

    def _continue(self, s):
        if s.question:
            return self._ask_card(s.question, ["Here's the question again."])
        if s.mode == "done" and s.target:
            return self._card(s, f"Goal reached: {self._name(s.target)}", ["Set a new goal, or ask me anything."])
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

    def _context(self, user, s):
        members = self.graph.human(user).snapshot().nodes
        known = sorted(self._name(c) for c, node in members.items() if node.understanding >= KNOWN)[:12]
        return {"goal": self._name(s.target) if s.target else None, "knows_well": known,
                "recently_covered": list(s.recent)}

    def _find(self, text, create=False, link_to=None):
        """Curriculum alias, exact God concept name, the resolver, then (optionally) a new concept."""
        concept = self.curriculum.match(text)
        if concept is None:
            try:
                concept = self.graph.god.get_concept(text.strip()).id
            except (NotFound, AmbiguousConcept, ValueError):
                concept = None
        if concept is None and self.goals.resolver is not None:
            concept = self.goals.resolver(text)
        if concept is None and create and self.author:
            concept = self._create(text, link_to)
        return concept

    def _create(self, request, link_to=None):
        """New shared-graph concept through the engine's proposal API (validated, audited)."""
        snapshot = self.graph.snapshot()
        names = {node.label: node.id for node in snapshot.nodes.values()}
        spec = self.author.concept(request, sorted(names)[:200])
        if spec is None or not spec.name.strip():
            return None
        name = re.sub(r"\s+", " ", spec.name.strip())[:80]
        if name in names:
            return names[name]
        concept_id = str(uuid4())
        provenance = {"source": "claude", "model": self.author.model}
        commands = [{"operation": "add_concept", "arguments": {"concept_id": concept_id, "name": name,
                                                               "description": spec.description.strip()[:600]}}]
        linked = {link.concept: max(1, min(10, link.similarity)) for link in spec.links if link.concept in names}
        if link_to and self._name(link_to) not in linked:
            linked[self._name(link_to)] = 7
        for other, similarity in linked.items():
            commands.append({"operation": "connect_god", "arguments": {
                "a": concept_id, "b": names[other], "similarity": similarity, "provenance": provenance}})
        proposal = self.graph.proposals.submit({
            "source": f"claude:{self.author.model}", "evidence": [f"Learner asked about: {name}"],
            "confidence": 0.5, "expected_revisions": {"god": self.graph.god.revision}, "commands": commands})
        self.graph.proposals.accept(proposal)
        return concept_id
