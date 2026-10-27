from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from memory_graph import MemoryGraph

from backend.app.author import Check, RetrievalGrade, Written
from backend.app.diagrams import AUTHORED, DBeat, DSurface, DiagramError, DiagramLesson, compile_lesson, diagram
from backend.app.goals import Curriculum, Goals
from backend.app.learning import LessonError, complex_quadratic_lesson, plan
from backend.app.main import create_app
from backend.app.reviews import Reviews
from backend.app.tutor import Session, Tutor


class PracticeAuthor:
    model = "test-model"
    score = 9.0
    offline = False

    def __init__(self):
        self.practices = []

    def practice(self, kind, concept, description, related=None, covered=None):
        self.practices.append((kind, concept, related, covered))
        return Check(question=f"{kind}: Explain {concept} with reasoning.",
                     expected_answer="State the idea, apply it, explain why.")

    def assess_retrieval(self, question, rubric, answer):
        return None if self.offline else RetrievalGrade(score=self.score, feedback="Here is specific feedback.",
            demonstrated=["the idea"], missing=[] if self.score >= 7 else ["reasoning"])

    def question(self, concept, description):
        return Check(question="Explain this idea.", expected_answer="yes")

    def grade(self, *args):
        return "correct", "Good reasoning."

    def explain(self, *args, **kwargs):
        return Written(compile_lesson(AUTHORED["derivatives"]), self.question("", ""), None)


@pytest.fixture
def workout(tmp_path):
    with MemoryGraph.open(f"sqlite:///{tmp_path / 'workout.db'}") as graph:
        curriculum = Curriculum().sync(graph)
        human = graph.create_human()
        author = PracticeAuthor()
        tutor = Tutor(graph, Goals(graph, curriculum, plan), plan, author)
        yield graph, curriculum, human, tutor, author


def test_small_steps_end_without_a_quick_check(workout):
    _, curriculum, human, tutor, author = workout
    target = curriculum.ids["derivatives"]
    s = Session(target=target)
    first = tutor._teach(s, human.human_id, target, [])
    # One note per turn: the step and a check-in, then wait. The question is its own turn.
    assert s.question is None and s.step_check and len(first.beats) == 1 and first.beats[0].say_text.endswith("?")
    assert not author.practices
    asked = tutor.act(s, human.human_id, "answer", "yes, got it")
    assert s.question.kind == "prediction" and [b.say_text for b in asked.beats] == [s.question.prompt]
    assert len(author.practices[-1][3]) == 1
    remaining = len(s.remaining_beats)
    tutor.act(s, human.human_id, "continue")
    assert len(s.remaining_beats) == remaining
    while s.question or s.step_check:
        tutor.act(s, human.human_id, *(("answer", "my reasoning") if s.question else ("continue",)))
    # The last step asks nothing: predictions between steps only, and no mastery is claimed.
    assert not s.remaining_beats and {kind for kind, *_ in author.practices} == {"prediction"}
    assert target not in human.snapshot().nodes
    tutor.act(s, human.human_id, "continue")
    assert target in s.covered


def test_missing_connection_synthesis_then_teachback(workout):
    graph, curriculum, human, tutor, _ = workout
    target, related = curriculum.ids["derivatives"], curriculum.ids["slope"]
    human.add_concept(related, understanding=8, method="test")
    human.add_concept(target, understanding=8, method="test")
    s = Session(target=target, current=target)
    tutor._consolidate(s, human.human_id, target, [])
    assert s.question.kind == "synthesis" and s.question.related == related
    assert not human.snapshot().edges
    tutor.act(s, human.human_id, "answer", "my reasoning")
    assert s.question.kind == "teachback"
    edge = tuple(sorted((target, related)))
    assert human.snapshot().edges[edge].familiarity == 9
    history = graph.history(kind="familiarity", human_id=human.human_id, concept=target, other=related)
    assert history[-1]["method"] == "synthesis_retrieval"
    tutor.act(s, human.human_id, "answer", "my reasoning")
    assert s.mode == "done" and human.snapshot().nodes[target].understanding == 9
    saved = tutor.reviews.suggestions(human.human_id)
    assert next(t for t in saved["topics"] if t["concept_id"] == target)["reviews"] == 1


def test_no_direct_connection_uses_teachback_and_grader_failure_does_not_score(workout):
    _, curriculum, human, tutor, author = workout
    target = curriculum.ids["derivatives"]
    human.add_concept(target, understanding=7, method="test")
    human.add_concept(curriculum.ids["vectors"], understanding=9, method="test")
    s = Session(target=target, current=target)
    tutor._consolidate(s, human.human_id, target, [])
    assert s.question.kind == "teachback" and s.question.related is None
    before = human.revision
    author.offline = True
    result = tutor.act(s, human.human_id, "answer", "a long answer")
    assert human.revision == before and s.question.kind == "teachback"
    assert "couldn't check" in result.beats[0].say_text
    assert tutor.reviews.suggestions(human.human_id)["topics"][0]["reviews"] == 0
    author.offline, author.score = False, 3
    tutor.act(s, human.human_id, "answer", "a mistaken answer")
    assert human.snapshot().nodes[target].understanding == 3
    topic = next(t for t in tutor.reviews.suggestions(human.human_id)["topics"] if t["concept_id"] == target)
    assert topic["last_review"] and topic["stability_days"] and topic["reviews"] == 1


def test_warmup_hides_solutions_restores_pending_question_and_skip_does_not_score(workout):
    _, curriculum, human, tutor, _ = workout
    target = curriculum.ids["derivatives"]
    human.add_concept(target, understanding=8, method="test")
    s = Session(target=target)
    pending = tutor._question(target, "check")
    s.question = pending
    before = human.revision
    warmup = tutor.act(s, human.human_id, "warmup")
    assert s.warmup_active and s.question.kind == "recall" and s.resume_question is pending
    assert all(n.content.primitive == "text_label" for n in warmup.nodes)
    assert "slope of the tangent" not in " ".join(b.say_text for b in warmup.beats)
    tutor.act(s, human.human_id, "skip")
    assert s.question is pending and not s.warmup_active and human.revision == before


def test_wrong_microanswer_gets_correction_before_advancing(workout):
    _, curriculum, human, tutor, author = workout
    s = Session(target=curriculum.ids["derivatives"])
    tutor._teach(s, human.human_id, s.target, [])
    tutor.act(s, human.human_id, "continue")
    before, question = len(s.remaining_beats), s.question
    author.score = 3
    tutor.act(s, human.human_id, "answer", "a mistake")
    assert s.question is question and len(s.remaining_beats) == before
    author.score = 9
    tutor.act(s, human.human_id, "answer", "corrected reasoning")
    assert len(s.remaining_beats) == before - 1


def test_legacy_derived_edges_are_eligible_for_real_synthesis(workout):
    _, curriculum, human, tutor, _ = workout
    a, b = curriculum.ids["derivatives"], curriculum.ids["slope"]
    for concept in (a, b):
        tutor.goals.record(human.human_id, concept, 8, "test", {})
    assert human.snapshot().edges
    s = Session(target=a, current=a)
    tutor._consolidate(s, human.human_id, a, [])
    assert s.question.kind == "synthesis" and s.question.related == b


def test_authored_offline_lessons_also_pause_and_microchecks_do_not_claim_mastery(workout):
    _, curriculum, human, tutor, _ = workout
    tutor.author = None
    s = Session(target=curriculum.ids["derivatives"])
    tutor._teach(s, human.human_id, s.target, [])
    tutor.act(s, human.human_id, "continue")
    assert s.question.kind == "prediction_choice"
    tutor.act(s, human.human_id, "answer", "A")
    assert not human.snapshot().nodes
    assert tutor.reviews.suggestions(human.human_id)["practice_by_kind"]["prediction_choice"] == 1
    assert s.question is None and s.remaining_beats and s.step_check  # step 2 checks in, then waits
    tutor.act(s, human.human_id, "continue")  # offline, step 2 has no question: on to step 3
    assert s.question is None
    while s.remaining_beats:
        tutor.act(s, human.human_id, "continue")
    assert s.question is None and not s.remaining_beats  # no quick check after the last step


def test_simultaneous_answers_cannot_grade_the_next_question_with_a_stale_epoch(workout):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from memory_graph import Conflict
    from backend.app.service import TutorService
    _, curriculum, human, tutor, author = workout
    service = TutorService(tutor.graph, plan, tutor.goals, tutor)
    lesson = service.create(human.human_id)["lesson_id"]
    target = curriculum.ids["derivatives"]
    s = Session(target=target, current=target)
    s.question = tutor._question(target, "check")
    service.sessions[lesson] = s
    started, release = Event(), Event()
    original = author.grade

    def slow_grade(*args):
        started.set()
        assert release.wait(5)
        return original(*args)

    author.grade = slow_grade
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.tutor_turn, human.human_id, lesson, "answer", "yes", 0)
        assert started.wait(5)
        second = pool.submit(service.tutor_turn, human.human_id, lesson, "answer", "yes", 0)
        release.set()
        assert first.result()["turn_epoch"] == 1
        with pytest.raises(Conflict):
            second.result()
    assert service.sessions[lesson].question.kind == "teachback"
    assert human.snapshot().nodes[target].understanding == 8


def test_interrupt_during_model_grading_preserves_question_and_mastery(workout):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from memory_graph import Conflict
    from backend.app.service import TutorService
    _, curriculum, human, tutor, author = workout
    target = curriculum.ids["derivatives"]
    human.add_concept(target, understanding=7, method="test")
    service = TutorService(tutor.graph, plan, tutor.goals, tutor)
    lesson = service.create(human.human_id)["lesson_id"]
    s = Session(target=target, current=target)
    tutor._consolidate(s, human.human_id, target, [])
    pending = s.question
    service.sessions[lesson] = s
    started, release = Event(), Event()
    original = author.assess_retrieval

    def slow_assessment(*args):
        started.set()
        assert release.wait(5)
        return original(*args)

    author.assess_retrieval = slow_assessment
    before = human.revision
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.tutor_turn, human.human_id, lesson, "answer", "reasoning", 0)
        assert started.wait(5)
        service.interrupt(human.human_id, lesson, 0)
        release.set()
        with pytest.raises(Conflict):
            future.result()
    assert human.revision == before and service.sessions[lesson].question is pending
    assert tutor.reviews.suggestions(human.human_id)["practice_attempts"] == 0


def test_fsrs_forgetting_due_dates_failure_idempotency_and_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'srs.db'}"
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    with MemoryGraph.open(url) as graph:
        human = graph.create_human()
        concept = graph.god.add_concept("A fact").id
        human.add_concept(concept, understanding=8, method="test")
        user, reviews = human.human_id, Reviews(graph)
        reviews.record(user, concept, 8, "first", {"kind": "recall"}, now=now)
        initial = reviews.suggestions(user, now=now)["topics"][0]
        later = reviews.suggestions(user, now=now + timedelta(days=3))["topics"][0]
        assert initial["recall_probability"] > later["recall_probability"]
        assert later["forgetting_score"] > initial["forgetting_score"] and later["due"]
        reviews.record(user, concept, 8, "first", {}, now=now)
        assert reviews.suggestions(user, now=now)["topics"][0]["reviews"] == 1
        reviews.record(user, concept, 2, "failed", {"kind": "recall"}, now=now + timedelta(days=3))
        failed = reviews.suggestions(user, now=now + timedelta(days=3))["topics"][0]
        assert datetime.fromisoformat(failed["next_review"]) <= now + timedelta(days=3, minutes=10)
        assert reviews.suggestions(graph.create_human().human_id, now=now)["topics"] == []
    with MemoryGraph.open(url) as graph:
        assert Reviews(graph).suggestions(user, now=now + timedelta(days=4))["topics"][0]["reviews"] == 2


def test_review_api_is_authenticated_and_scoped(workout):
    graph, _, human, _, author = workout
    token = "workout-test-token-more-than-24-characters"
    app = create_app(graph, {token: human.human_id}, planner=None, author=author, voice=None)
    with TestClient(app) as client:
        assert client.get("/api/me/reviews").status_code == 401
        response = client.get("/api/me/reviews", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200 and response.json()["topics"] == []


@pytest.mark.parametrize("coefficients", [(1, 0, 1), (1, -2, 5), (1, -3, 2), (1, 2, 1)])
def test_true_3d_complex_quadratic_roots(coefficients):
    a, b, c = coefficients
    result = complex_quadratic_lesson(a, b, c)
    assert all(n.coordinate_system == "lesson_xyz" for n in result.nodes)
    assert any(n.content.primitive == "expr_surface" for n in result.nodes)
    for root in [n.content for n in result.nodes if n.id.startswith("root_")]:
        z = complex(float(root.at[0]), float(root.at[1]))
        assert abs(a*z*z + b*z + c) < 1e-9


def test_3d_surface_validation_and_degenerate_quadratics():
    bad = DSurface(id="bad", x="u", y="v", z="sqrt(-1)", u_min=-1, u_max=1, v_min=-1, v_max=1)
    with pytest.raises(DiagramError, match="outside the view or undefined"):
        compile_lesson(DiagramLesson(title="bad", beats=[DBeat(say="hi", focus=[])],
                                    diagram=diagram(dimensions=3, surfaces=[bad])))
    with pytest.raises(LessonError):
        complex_quadratic_lesson(0, 1, 1)


def test_hints_point_back_to_the_step_then_move_on_without_scoring(workout):
    from backend.app.conversation import Phrasing
    _, curriculum, human, tutor, _ = workout
    tutor.phrasing = Phrasing(variety=False, seed=0)
    s = Session(target=curriculum.ids["derivatives"])
    tutor._teach(s, human.human_id, s.target, [])
    tutor.act(s, human.human_id, "continue")
    q, before = s.question, len(s.remaining_beats)
    hinted = tutor.act(s, human.human_id, "hint")
    assert s.question is q and [b.say_text for b in hinted.beats] == [f"Here's a hint. {s.taught_beats[-1]} {q.prompt}"]
    revealed = tutor.act(s, human.human_id, "hint")
    # The expected answer is a rubric, so it is never read out: the next step shows the idea.
    assert revealed.beats[0].say_text.startswith("Let's see it in the next step. ") and len(s.remaining_beats) == before - 1
    assert not human.snapshot().nodes and tutor.reviews.suggestions(human.human_id)["practice_attempts"] == 0


def test_a_no_to_the_check_in_explains_that_step_again(workout):
    _, curriculum, human, tutor, _ = workout
    s = Session(target=curriculum.ids["derivatives"])
    tutor._teach(s, human.human_id, s.target, [])
    assert s.step_check
    again = tutor.act(s, human.human_id, "answer", "not really")
    # Explained afresh from its first step (not graded), which checks in again.
    assert s.question is None and s.step_check and len(s.taught_beats) == 1 and len(again.beats) == 1
    assert s.current == curriculum.ids["derivatives"] and not human.snapshot().nodes
