"""Local tools for the single Realtime teacher. No model calls or executable code here."""
from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from memory_graph import AmbiguousConcept, Conflict, NotFound

from .diagrams import DiagramLesson, compile_lesson
from .learning import TeachingBeat
from .screen import describe_screen
from .teaching import ConceptSpec, RetrievalGrade
from .tutor import Question, Session, card

MODEL = "gpt-realtime-2.1"


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Context(Args):
    topic: str = Field(default="", max_length=500)
    learner_text: str = Field(default="", max_length=2000)


class Concepts(Args):
    concepts: list[ConceptSpec] = Field(min_length=1, max_length=6)


class Present(Args):
    concept_id: str = Field(min_length=1, max_length=80)
    lesson: DiagramLesson
    facts: list[ConceptSpec] = Field(default_factory=list, max_length=6)


class Load(Args):
    concept_id: str = Field(min_length=1, max_length=80)


class Advance(Args):
    pass


class Ask(Args):
    concept_id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=600)
    rubric: str = Field(min_length=1, max_length=1500)
    kind: Literal["diagnostic", "prediction", "check", "recall", "teachback", "synthesis"] = "check"
    related_concept_id: str | None = Field(default=None, max_length=80)


class Assess(Args):
    question_id: str = Field(min_length=1, max_length=80)
    answer: str = Field(min_length=1, max_length=2000)
    grade: RetrievalGrade


class Memory(Args):
    command: Literal["view", "create", "str_replace", "insert", "delete", "rename"]
    path: str = "/memories/learner.md"
    file_text: str = Field(default="", max_length=4000)
    old_str: str = Field(default="", max_length=4000)
    new_str: str = Field(default="", max_length=4000)
    insert_line: int = Field(default=0, ge=0)
    insert_text: str = Field(default="", max_length=4000)
    old_path: str = ""
    new_path: str = ""
    view_range: tuple[int, int] | None = None


DEFINITIONS = {
    "get_learning_context": (Context, "Read the brain BEFORE teaching a topic: known concepts, mastery, "
                             "prerequisite route, learner notes, saved lessons, current screen and review suggestions. "
                             "Unknown topics return candidate concepts; create a precise new concept if needed."),
    "remember_concepts": (Concepts, "Add precise new concepts or facts and direct prerequisite links to the shared "
                          "brain. Does not change learner mastery. Returns concept IDs for presenting lessons."),
    "present_lesson": (Present, "Write animated diagram code and spoken steps. Validates and displays the first "
                       "step immediately. Prefer one small step at a time to keep latency low. Invalid code returns "
                       "a repairable error; nothing is executed. Wait for success, then speak speak_text and stop."),
    "advance_step": (Advance, "Reveal the next prepared step ONLY after the learner asks to continue. "
                     "Returns speak_text; if no steps remain, explain or generate the next small step yourself."),
    "load_lesson": (Load, "Display a saved lesson or built-in example for a concept without regenerating its code. "
                    "Returns the first step and prepares the rest for advance_step."),
    "skip_question": (Advance, "Clear a pending question ONLY when the learner explicitly asks to skip it. "
                      "Never changes mastery or the review schedule."),
    "ask_question": (Ask, "Prepare a check, prediction, recall or teachback with a PRIVATE rubric BEFORE the learner "
                     "answers. Keeps the current picture. Returns question_id. Do not grade conversational yes/no."),
    "record_assessment": (Assess, "Grade an actual learner answer against the previously stored rubric and save "
                          "evidence to the personal brain and review scheduler. Never award mastery for exposure, "
                          "silence, a skipped question, or agreeing to continue."),
    "learner_memory": (Memory, "Read or update this learner's persistent /memories notes: misconceptions, helpful "
                       "examples, pace and visual preferences. Never store personal details or raw transcripts."),
}


def tool_definitions():
    return [{"type": "function", "name": name, "description": description,
             "parameters": schema.model_json_schema()}
            for name, (schema, description) in DEFINITIONS.items()]


class RealtimeTools:
    def __init__(self, service):
        self.service = service
        self.calls: dict[str, OrderedDict] = {}

    def context(self, user, lesson, topic="", view=None):
        service, tutor = self.service, self.service.tutor
        state = service.owned(user, lesson)
        session = service.sessions.setdefault(lesson, Session())
        target = session.current or session.target
        if topic:
            try:
                target = tutor.graph.god.get_concept(topic).id
            except (NotFound, AmbiguousConcept, ValueError):
                # Resolve aliases exactly so a new topic is never swallowed by a broad substring.
                target = next((tutor.curriculum.ids[t.key] for t in tutor.curriculum.topics
                               if topic.casefold() in (t.name.casefold(), *t.aliases)), None)
        graph = tutor._context(user, session, target) if target else {}
        words = set(re.findall(r"[a-z]{3,}", topic.casefold()))
        candidates = sorted(tutor.graph.snapshot().nodes.values(), key=lambda n: (
            -len(words & set(re.findall(r"[a-z]{3,}", n.label.casefold()))), n.label.casefold()))[:60]
        return {"model": MODEL, "concept_id": target, "requested_topic": topic,
                "learning_context": graph,
                "candidate_concepts": [{"id": n.id, "name": n.label, "description": n.description}
                                       for n in candidates],
                "learner_notes": tutor.memory.render(user),
                "screen": describe_screen(list(state.nodes.values()), session.shown_beats,
                    view if view is not None else session.view,
                    question=session.question.spoken if session.question else None,
                    exchanges=list(session.exchanges)),
                "pending_question": ({"id": session.question.event_id, "question": session.question.prompt,
                    "rubric": session.question.expected, "kind": session.question.kind} if session.question else None),
                "reviews": tutor.reviews.suggestions(user),
                "saved_lessons": tutor.library.listing(user),
                "remaining_steps": len(session.remaining_beats), "epoch": state.epoch}

    def execute(self, user, lesson, call_id, name, arguments, expected_epoch, view=None):
        if name not in DEFINITIONS:
            raise ValueError("Unknown realtime tool")
        parsed = DEFINITIONS[name][0].model_validate(arguments)
        signature = hashlib.sha256(json.dumps([name, arguments], sort_keys=True).encode()).hexdigest()
        service = self.service
        # Local-only tools: one atomic ownership/epoch/write boundary, never spanning a model call.
        with service.lock:
            state = service.owned(user, lesson)
            ledger = self.calls.setdefault(lesson, OrderedDict())
            if call_id in ledger:
                old_signature, result = ledger[call_id]
                if signature != old_signature:
                    raise Conflict("Tool call ID already used with different arguments")
                # A replay must not apply the same scene event twice.
                return {"output": result["output"], "replayed": True}
            if state.epoch != expected_epoch:
                raise Conflict("Stale realtime tool call; refresh context")
            session = service.sessions.setdefault(lesson, Session())
            session.view = view
            result = self._run(user, lesson, session, name, parsed)
            ledger[call_id] = (signature, result)
            if len(ledger) > 128:
                ledger.popitem(last=False)
            return result

    def _commit(self, user, lesson, session, plan):
        now = monotonic()
        plan.planned_by = "realtime"
        plan.graph_context = self.service.tutor._context(user, session, plan.concept_id) if plan.concept_id else None
        plan.tutor = {"mode": "checking", "awaiting_answer": session.question is not None,
                      "goal": self.service.tutor._name(session.target) if session.target else None,
                      "step": len(session.taught_beats),
                      "total_steps": len(session.active_plan.beats) if session.active_plan else 1}
        turn = self.service._commit(user, lesson, self.service.owned(user, lesson).epoch, plan, now, now)
        session.screen = list(self.service.owned(user, lesson).nodes.values())
        session.shown_beats = [b.say_text for b in plan.beats]
        session.exchanges.append({"role": "teacher", "text": " ".join(session.shown_beats)})
        return {"turn": turn, "output": {"speak_text": " ".join(session.shown_beats),
                "remaining_steps": len(session.remaining_beats), "awaiting_answer": session.question is not None,
                "question_id": session.question.event_id if session.question else None,
                "note": "The visual is on screen. Speak this one step, then listen. Do not repeat the tool call."}}

    def _run(self, user, lesson, s, name, args):
        tutor = self.service.tutor
        if name == "get_learning_context":
            if args.learner_text.strip():
                s.exchanges.append({"role": "learner", "text": args.learner_text})
            return {"output": self.context(user, lesson, args.topic, s.view)}
        if name == "remember_concepts":
            ids = tutor._store_specs(args.concepts, "Realtime teacher defined a requested concept or taught fact",
                                    source="openai", model=MODEL)
            return {"output": {"concept_ids": ids, "concepts": [tutor._name(c) for c in ids]}}
        if name == "learner_memory":
            return {"output": {"result": tutor.memory.for_learner(user).call(args.model_dump())}}
        if name == "skip_question":
            s.question = None
            return {"output": {"skipped": True, "note": "No assessment recorded; continue only if asked."}}
        if name == "load_lesson":
            tutor.graph.god.get_concept(args.concept_id)
            saved = tutor.library.get(user, args.concept_id)
            if saved:
                plan = saved[0]
            else:
                topic = tutor.curriculum.by_id.get(args.concept_id)
                if not topic:
                    raise ValueError("No saved lesson: generate a diagram with present_lesson")
                plan = tutor._lesson(args.concept_id, user, s)[0]
            s.current = s.target = s.viewed = args.concept_id
            s.question = None
            s.active_plan = plan.model_copy(deep=True)
            s.remaining_beats, s.taught_beats = list(plan.beats), []
            return self._next(user, lesson, s)
        if name == "present_lesson":
            tutor.graph.god.get_concept(args.concept_id)
            if not 1 <= len(args.lesson.beats) <= 32:
                raise ValueError("Present between 1 and 32 small steps")
            plan = compile_lesson(args.lesson, recipe_id="generated_v1", generated=True)
            plan.concept_id = args.concept_id
            # Validate the event budget BEFORE any graph/session writes.
            if len(json.dumps([n.model_dump() for n in plan.nodes]).encode()) > 14000:
                raise ValueError("Scene too large: simplify the diagram or use fewer elements")
            if args.facts:
                tutor._store_specs(args.facts, "Facts taught by the realtime teacher", source="openai", model=MODEL)
            tutor.library.save(user, args.concept_id, plan, None)
            s.target = args.concept_id
            s.current, s.viewed, s.question = args.concept_id, args.concept_id, None
            s.active_plan = plan.model_copy(deep=True)
            s.remaining_beats, s.taught_beats = list(plan.beats), []
            return self._next(user, lesson, s)
        if name == "advance_step":
            if s.question:
                raise ValueError("A question is pending. Answer it or explicitly skip it before continuing")
            if not s.active_plan or not s.remaining_beats:
                return {"output": {"prepared_steps_exhausted": True, "note": "No prepared steps remain. "
                                  "If the learner asked to continue, generate the next small step of this concept "
                                  "with present_lesson. The concept is not necessarily complete. Never invent mastery."}}
            return self._next(user, lesson, s)
        if name == "ask_question":
            tutor.graph.god.get_concept(args.concept_id)
            if s.question:
                raise ValueError("A question is already pending; assess or skip it first")
            if args.kind == "synthesis":
                members = tutor.graph.human(user).snapshot().nodes
                if (not args.related_concept_id or args.related_concept_id == args.concept_id
                        or args.related_concept_id not in tutor.graph.god.neighbors(args.concept_id)
                        or args.concept_id not in members or args.related_concept_id not in members):
                    raise ValueError("Synthesis requires two assessed concepts joined by a direct shared-brain edge")
            s.question = Question(args.concept_id, args.kind, args.question, args.rubric,
                                  related=args.related_concept_id)
            # Keep the picture; put the question in the accessible explanation area.
            plan = s.active_plan.model_copy(deep=True) if s.active_plan else card("Think it through", [])
            plan.concept_id = args.concept_id
            plan.nodes = list(self.service.owned(user, lesson).nodes.values())
            plan.beats = [TeachingBeat(beat_id="check", say_text=args.question, focus_ids=[])]
            return self._commit(user, lesson, s, plan)
        if name == "record_assessment":
            q = s.question
            if not q or q.event_id != args.question_id:
                raise ValueError("Assessment must match a previously prepared question")
            if not args.answer.strip():
                raise ValueError("An actual learner answer is required")
            evidence = {"model": MODEL, "question": q.prompt, "rubric": q.expected,
                        "demonstrated": args.grade.demonstrated, "missing": args.grade.missing,
                        "kind": q.kind}
            with tutor.graph.transaction():
                tutor.reviews.log_attempt(user, q.concept, q.kind, args.grade.score, q.event_id, evidence)
                # A micro-prediction guides feedback; independent retrieval updates mastery.
                if q.kind == "synthesis":
                    human = tutor.graph.human(user)
                    edge = tuple(sorted((q.concept, q.related)))
                    if edge in human.snapshot().edges:
                        human.assess_familiarity(q.concept, q.related, args.grade.score,
                                                method="realtime_synthesis", provenance=evidence)
                    else:
                        human.connect(q.concept, q.related, familiarity=args.grade.score,
                                      method="realtime_synthesis", provenance=evidence)
                elif q.kind != "prediction":
                    tutor.goals.record(user, q.concept, args.grade.score, "realtime_retrieval", evidence,
                                       link_missing=False)
                    tutor.reviews.record(user, q.concept, args.grade.score, q.event_id, evidence)
            s.question = None
            self.service.snapshots.clear()
            return {"output": {"feedback": args.grade.feedback, "recorded": q.kind != "prediction"}}
        raise ValueError("Unknown realtime tool")

    def _next(self, user, lesson, s):
        beat = s.remaining_beats.pop(0)
        s.taught_beats.append(beat.say_text)
        plan = s.active_plan.model_copy(deep=True)
        plan.beats = [beat]
        # Preserve where earlier animations left the parameters between spoken steps.
        previous_values = {a.binding: a.end for b in s.active_plan.beats[:len(s.taught_beats) - 1]
                           for a in b.animations}
        plan.nodes = [n.model_copy(update={"content": n.content.model_copy(update={"value": previous_values[n.content.binding]})})
                      if n.content.primitive == "slider" and n.content.binding in previous_values else n
                      for n in plan.nodes]
        revealed = {"title", "axes"} | {n.id for n in plan.nodes if n.content.primitive == "slider"}
        revealed.update(i for b in s.active_plan.beats[:len(s.taught_beats)] for i in b.focus_ids)
        if s.remaining_beats:
            plan.nodes = [n.model_copy(update={"visible": n.id in revealed}) for n in plan.nodes]
        return self._commit(user, lesson, s, plan)
