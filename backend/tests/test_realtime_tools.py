"""Exercise the actual authenticated bridge without spending tokens or opening a microphone."""
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from memory_graph import MemoryGraph
from backend.app.diagrams import AUTHORED
from backend.app.main import create_app
from backend.app.realtime_tools import DEFINITIONS, MODEL
from backend.app.voice import OpenAIRealtime

TOKEN = "realtime-test-only-not-a-real-secret"
OTHER = "other-realtime-test-not-a-real-secret"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def studio(tmp_path):
    graph = MemoryGraph.open(f"sqlite:///{tmp_path / 'realtime.db'}")
    human, other = graph.create_human(), graph.create_human()
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"value": "ek_fixture", "expires_at": 123})

    voice = OpenAIRealtime("test-key", transport=httpx.MockTransport(provider))
    app = create_app(graph, {TOKEN: human.human_id, OTHER: other.human_id}, voice=voice, models=None)
    with graph, TestClient(app) as client:
        lesson = client.post('/api/lessons', headers=HEADERS).json()['lesson_id']
        epoch = 0

        def tool(name, arguments, call_id=None, expected_epoch=None, headers=HEADERS):
            nonlocal epoch
            response = client.post(f'/api/lessons/{lesson}/realtime-tools', headers=headers, json={
                'name': name, 'arguments': arguments, 'call_id': call_id or uuid4().hex,
                'expected_epoch': epoch if expected_epoch is None else expected_epoch,
                'view': {'sliders': {'param.a': 1.5}, 'active_beat': 0},
            })
            if response.status_code == 200 and 'turn' in response.json():
                epoch = response.json()['turn']['turn_epoch']
            return response

        yield graph, human, app, client, lesson, tool, requests


def test_one_model_and_preloaded_brain_notes_screen(studio, monkeypatch):
    graph, human, app, client, lesson, tool, requests = studio
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'unused-key')
    assert app.state.author is None and app.state.planner is None
    health = client.get('/api/health').json()
    assert health['model'] == MODEL and health['author'] == health['planner'] == 'realtime'
    concept = app.state.curriculum.ids['derivatives']
    human.add_concept(concept, understanding=8, method='test')
    tool('learner_memory', {'command': 'create', 'file_text': '- Prefers geometric examples.'}).raise_for_status()
    context = tool('get_learning_context', {'topic': 'derivatives', 'learner_text': 'Show me a tangent.'}).json()['output']
    assert context['concept_id'] == concept
    assert context['learning_context']['nearest_concepts'][0]['understanding'] == 8
    assert 'geometric examples' in context['learner_notes']
    assert context['screen']['learner_view']['active_step'] == 1
    credential = client.post('/api/voice/sessions', headers=HEADERS, json={'lesson_id': lesson})
    assert credential.status_code == 201
    session = requests[-1]['session']
    assert session['model'] == MODEL
    assert [t['name'] for t in session['tools']] == list(DEFINITIONS)
    assert 'Jay Cummings' in session['instructions']
    assert 'geometric examples' in session['instructions']
    assert 'INITIAL LEARNER CONTEXT' in session['instructions']
    assert 'must not teach' not in session['instructions'].lower()


def test_new_topic_and_animated_steps_preserve_parameters_without_awarding_mastery(studio):
    graph, human, app, client, lesson, tool, _ = studio
    added = tool('remember_concepts', {'concepts': [{'name': 'Tangent intuition',
        'description': 'A tangent gives the local direction of a curve.',
        'links': [{'concept': 'Derivatives', 'similarity': 8}]}]})
    assert added.status_code == 200, added.text
    concept = added.json()['output']['concept_ids'][0]
    assert concept not in human.snapshot().nodes
    scene = AUTHORED['derivatives'].model_dump()
    scene['beats'] = [scene['beats'][0], scene['beats'][1]]
    parameter = scene['diagram']['sliders'][0]['name']
    scene['beats'][0]['animate'] = [{'param': parameter, 'to': 1.5, 'seconds': 2}]
    published = tool('present_lesson', {'concept_id': concept, 'lesson': scene})
    assert published.status_code == 200, published.text
    first = published.json()
    assert len(first['turn']['plan']['beats']) == 1
    assert first['turn']['plan']['planned_by'] == 'realtime'
    assert first['output']['remaining_steps'] == 1
    assert concept not in human.snapshot().nodes
    second = tool('advance_step', {}).json()
    sliders = [n['content'] for n in second['turn']['events'][0]['operation']['nodes'] if n['content']['primitive'] == 'slider']
    assert next(c for c in sliders if c['binding'] == f'param.{parameter}')['value'] == 1.5
    assert second['output']['remaining_steps'] == 0
    assert tool('advance_step', {}).json()['output']['prepared_steps_exhausted']
    saved = app.state.service.tutor.library.get(human.human_id, concept)
    assert saved and len(saved[0].beats) == 2
    assert tool('load_lesson', {'concept_id': concept}).status_code == 200


def test_validation_ownership_stale_calls_and_idempotency(studio):
    graph, human, app, client, lesson, tool, _ = studio
    target = app.state.curriculum.ids['derivatives']
    revision = graph.god.revision
    assert tool('get_learning_context', {}, headers={'Authorization': f'Bearer {OTHER}'}).status_code == 404
    assert client.post('/api/voice/sessions', headers={'Authorization': f'Bearer {OTHER}'},
                       json={'lesson_id': lesson}).status_code == 404
    assert tool('exec_python', {'code': 'print(1)'}).status_code == 422
    invalid = AUTHORED['derivatives'].model_dump()
    invalid['diagram']['curves'][0]['x'] = '__import__(os)'
    assert tool('present_lesson', {'concept_id': target, 'lesson': invalid}).status_code == 422
    assert graph.god.revision == revision
    assert app.state.service.owned(human.human_id, lesson).epoch == 0
    args = {'concept_id': target, 'lesson': AUTHORED['derivatives'].model_dump()}
    first = tool('present_lesson', args, call_id='same_call').json()
    replay = tool('present_lesson', args, call_id='same_call').json()
    assert replay['replayed'] and 'turn' not in replay
    assert app.state.service.owned(human.human_id, lesson).epoch == first['turn']['turn_epoch']
    assert tool('advance_step', {}, call_id='same_call').status_code == 409
    canceled = client.post(f'/api/lessons/{lesson}/realtime-interrupt', headers=HEADERS).json()
    assert canceled['turn_epoch'] == first['turn']['turn_epoch'] + 1
    assert tool('advance_step', {}, expected_epoch=first['turn']['turn_epoch']).status_code == 409


def test_assessment_requires_prepared_rubric_is_scoped_and_skip_does_not_score(studio):
    _, human, app, _, _, tool, _ = studio
    target = app.state.curriculum.ids['derivatives']
    grade = {'score': 8, 'feedback': 'You connected slope to rate of change.',
             'demonstrated': ['local rate of change'], 'missing': []}
    assert tool('record_assessment', {'question_id': 'made_up', 'answer': 'slope', 'grade': grade}).status_code == 422
    question = tool('ask_question', {'concept_id': target, 'question': 'What does a derivative measure?',
                                   'rubric': 'Must describe local rate of change.', 'kind': 'recall'}).json()
    assert target not in human.snapshot().nodes
    assert tool('advance_step', {}).status_code == 422
    args = {'question_id': question['output']['question_id'], 'answer': 'Local rate of change.', 'grade': grade}
    result = tool('record_assessment', args, call_id='grade_once')
    assert result.status_code == 200, result.text
    assert human.snapshot().nodes[target].understanding == 8
    revision = human.revision
    assert tool('record_assessment', args, call_id='grade_once').json()['replayed']
    assert human.revision == revision
    tool('ask_question', {'concept_id': target, 'question': 'Try another?', 'rubric': 'A factual criterion.'})
    assert tool('skip_question', {}).status_code == 200
    assert human.revision == revision
    assert tool('learner_memory', {'command': 'create', 'path': '/../other.md', 'file_text': 'bad'}).status_code == 422


def test_synthesis_updates_only_a_real_direct_relationship(studio):
    graph, human, app, _, _, tool, _ = studio
    target, related = app.state.curriculum.ids['derivatives'], app.state.curriculum.ids['slope']
    human.add_concept(target, understanding=8, method='test')
    human.add_concept(related, understanding=7, method='test')
    assert related in graph.god.neighbors(target)
    task = {'concept_id': target, 'related_concept_id': related, 'kind': 'synthesis',
            'question': 'How does slope describe a derivative?', 'rubric': 'Connect local rate with tangent slope.'}
    invalid = {**task, 'related_concept_id': app.state.curriculum.ids['vectors']}
    assert tool('ask_question', invalid).status_code == 422
    result = tool('ask_question', task).json()
    graded = tool('record_assessment', {'question_id': result['output']['question_id'],
        'answer': 'A derivative gives tangent slope.', 'grade': {'score': 9,
        'feedback': 'That connects the two ideas.', 'demonstrated': ['tangent slope'], 'missing': []}})
    assert graded.status_code == 200, graded.text
    snapshot = human.snapshot()
    assert snapshot.nodes[target].understanding == 8 and snapshot.nodes[related].understanding == 7
    assert snapshot.edges[tuple(sorted((target, related)))].familiarity == 9
