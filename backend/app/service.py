"""Single-process coordinator. Locks never span network sends or provider calls."""
from collections import OrderedDict
from threading import RLock
from time import monotonic
from uuid import uuid4

from memory_graph import Conflict, NotFound, shortest_path

from .learning import plan
from .scene import Event, Replace, SceneState


class TutorService:
    def __init__(self, graph):
        self.graph = graph
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

    def turn(self, user, lesson, text, expected_epoch, sources=(), target=None):
        started = monotonic()
        with self.lock:
            state = self.owned(user, lesson)
            if state.epoch != expected_epoch:
                raise Conflict("Stale turn epoch; request snapshot")
            teaching = plan(text)
            if target is not None:
                teaching.graph_context = self.path(user, sources, target)
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
                    "timings": {"plan_and_commit_ms": (monotonic() - started) * 1000}}

    def interrupt(self, user, lesson, expected_epoch):
        with self.lock:
            state = self.owned(user, lesson)
            if state.epoch != expected_epoch:
                raise Conflict("Stale interruption")
            state.epoch += 1
            return state.snapshot()  # retained objects survive cancellation

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
