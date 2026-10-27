"""Single-process coordinator. Locks never span network sends or provider calls."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import fields
from threading import RLock
from time import monotonic
from uuid import uuid4

from memory_graph import Conflict, NotFound, shortest_path

from .learning import plan
from .scene import Event, Replace, SceneState
from .tutor import Session


class TutorService:
    def __init__(self, graph, planner=plan, goals=None, tutor=None):
        self.graph = graph
        self.planner = planner
        self.goals = goals
        self.tutor = tutor
        self.sessions = {}  # lesson ID -> tutor Session; process-local like lessons.
        self.lock = RLock()
        self.lessons = {}
        self.snapshots = OrderedDict()

    def create(self, user):
        with self.lock:
            self.graph.human(user)  # identity must be provisioned, never client-created
            if len(self.lessons) >= 128:
                raise Conflict("Lesson capacity reached; delete a lesson")
            lesson = uuid4().hex
            self.lessons[lesson] = (user, SceneState(lesson))
            return self.lessons[lesson][1].snapshot()

    def owned(self, user, lesson):
        item = self.lessons.get(lesson)
        if item is None or item[0] != user:
            raise NotFound("Lesson not found")
        return item[1]

    def path(self, user, sources, target, formula_id=None):
        with self.lock:
            revision = (self.graph.god.revision, self.graph.human(user).revision)
            key = (user, revision, self.graph.methodology.configuration_hash)
            snapshot = self.snapshots.get(key)
            if snapshot is None:
                snapshot = self.graph.snapshot(human_id=user, scope="human",
                                               max_nodes=100_000, max_edges=500_000)
                # Use the revision actually captured, not an earlier read.
                key = (user, (snapshot.revision.god, snapshot.revision.human),
                       snapshot.methodology.configuration_hash)
                self.snapshots[key] = snapshot
                if len(self.snapshots) > 8:
                    self.snapshots.popitem(last=False)
            if formula_id is not None and formula_id != snapshot.methodology.formula_id:
                from memory_graph import ValidationError
                raise ValidationError("Requested formula is not the active graph methodology")
            return shortest_path(snapshot, sources, target).to_dict()

    def turn(self, user, lesson, text, expected_epoch, sources=(), target=None, concept=None, route_to=None):
        started = monotonic()
        with self.lock:
            if self.owned(user, lesson).epoch != expected_epoch:
                raise Conflict("Stale turn epoch; request snapshot")
        # Planning may call a remote model, so it runs outside the lock; the epoch is rechecked
        # before commit so an interruption or competing turn during planning wins.
        if (concept or route_to) and self.goals is None:
            raise NotFound("Learning goals are not configured")
        if route_to:
            teaching = self.goals.route_lesson(user, route_to)
        elif concept:
            if self.tutor:
                self.graph.god.get_concept(concept)
                teaching, _ = self.tutor._lesson(concept, user, Session(target=concept))
                teaching.concept_id = concept
                teaching.graph_context = self.tutor._context(user, Session(target=concept), concept)
            else:
                teaching = self.goals.concept_lesson(concept)
        else:
            teaching = self.planner(text)
            if self.goals is not None:
                teaching = self.goals.tag(text, teaching)
        planned = monotonic()
        with self.lock:
            if target is not None:
                teaching.graph_context = self.path(user, sources, target)
        return self._commit(user, lesson, expected_epoch, teaching, started, planned)

    def tutor_turn(self, user, lesson, action, text, expected_epoch, view=None):
        """One conversational step; the session lock serializes a lesson, never the whole service."""
        started = monotonic()
        if self.tutor is None:
            raise NotFound("The tutor is not configured")
        with self.lock:
            if self.owned(user, lesson).epoch != expected_epoch:
                raise Conflict("Stale turn epoch; request snapshot")
            session = self.sessions.setdefault(lesson, Session())
        with session.lock:
            with self.lock:
                state = self.owned(user, lesson)
                if state.epoch != expected_epoch:
                    raise Conflict("Stale turn epoch; request snapshot")
                screen = list(state.nodes.values())  # Exactly what the learner's scene shows now.
            working = Session(**{f.name: deepcopy(getattr(session, f.name)) for f in fields(Session)
                                 if f.name not in ("lock", "validate_write")})
            working.lock = session.lock
            working.validate_write = lambda: self._validate_tutor_epoch(user, lesson, expected_epoch)
            teaching = self.tutor.act(working, user, action, text, view=view, screen=screen)
            planned = monotonic()
            with self.lock:
                self.snapshots.clear()  # Graded answers may have changed personal scores.
                result = self._commit(user, lesson, expected_epoch, teaching, started, planned)
                self.sessions[lesson] = working
                return result

    def _validate_tutor_epoch(self, user, lesson, expected_epoch):
        with self.lock:
            if self.owned(user, lesson).epoch != expected_epoch:
                raise Conflict("Lesson changed while grading; no assessment recorded")

    def _commit(self, user, lesson, expected_epoch, teaching, started, planned):
        with self.lock:
            state = self.owned(user, lesson)
            if state.epoch != expected_epoch:
                raise Conflict("Lesson changed while planning; request snapshot")
            turn_id, utterance_id = uuid4().hex, uuid4().hex
            next_epoch = state.epoch + 1
            event = Event(event_id=uuid4().hex, lesson_id=lesson, scene_id=state.scene_id,
                          turn_id=turn_id, utterance_id=utterance_id, turn_epoch=next_epoch,
                          seq=state.seq + 1, base_revision=state.revision,
                          operation=Replace(nodes=teaching.nodes))
            state.epoch = next_epoch
            try:
                state.apply(event)
            except Exception:
                state.epoch -= 1
                raise
            return {"plan": teaching.model_dump(), "events": [event.model_dump()],
                    "turn_epoch": state.epoch, "turn_id": turn_id, "utterance_id": utterance_id,
                    "timings": {"plan_ms": (planned - started) * 1000,
                                "plan_and_commit_ms": (monotonic() - started) * 1000}}

    def interrupt(self, user, lesson, expected_epoch):
        with self.lock:
            state = self.owned(user, lesson)
            if state.epoch != expected_epoch:
                raise Conflict("Stale interruption")
            state.epoch += 1
            return state.snapshot()  # retained objects survive cancellation

    def write_goal(self, method, *args):
        """Goal writes change personal scores, so cached path snapshots must go."""
        if self.goals is None:
            raise NotFound("Learning goals are not configured")
        with self.lock:
            result = getattr(self.goals, method)(*args)
            self.snapshots.clear()
            return result

    def assess(self, user, request):
        with self.lock:
            human = self.graph.human(user)
            # The memory engine owns atomic idempotency, history and revision checks.
            result = human.assess_understanding(
                request.concept_id, request.score, method="self_report",
                idempotency_key=request.idempotency_key,
                expected_revision=request.expected_revision)
            self.snapshots.clear()
            return {"assessment_id": result, "revision": human.revision}
