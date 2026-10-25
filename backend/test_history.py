"""History regression tests: per-conversation version listing, model retrieval,
historical exports, refinement from older versions, and version bookkeeping.
The suite executes the CadQuery sandbox exactly once (module fixture) and reuses
its validated CodegenResult for the script-model persistence tests below."""

import uuid

import pytest

from . import codegen
from .db_models import GeneratedModel, Message
from .test_upgrade import CYLINDER_SCRIPT

CYLINDER_PROMPT = '20 mm diameter cylinder, 50 mm height'
METADATA_FIELDS = ('model_id', 'version', 'parent_version_id', 'parent_version', 'created_at', 'shape_type',
                   'generation_method', 'provider', 'summary', 'script_available', 'artifact_available',
                   'step_available', 'watertight', 'dimensions_mm', 'vertices', 'faces')


def _new_conversation(client, title):
    created = client.post('/api/conversations', json={'title': title})
    assert created.status_code == 200, created.text
    return created.json()


def _generate_cylinder(client, conversation_id):
    made = client.post('/api/models/generate', json={
        'conversation_id': conversation_id, 'prompt': CYLINDER_PROMPT, 'interpreter': 'local'})
    assert made.status_code == 200, made.text
    return made.json()


def _history(client, conversation_id):
    served = client.get(f"/api/conversations/{conversation_id}/models")
    assert served.status_code == 200, served.text
    return served.json()


@pytest.fixture(scope='module')
def cylinder_generation():
    """Real sandbox worker run: mocked Gemini response -> real script execution
    and validation. Downstream tests persist models from this validated result
    instead of re-running the worker."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('GEMINI_API_KEY', 'test-key')
        patch.setenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')
        patch.setattr(codegen, '_call_gemini', lambda message: '```python\n' + CYLINDER_SCRIPT + '\n```')
        result = codegen.build_script(codegen.ScriptRequest(prompt=CYLINDER_PROMPT))
    assert result.attempts == 1 and result.plan == [20.0, 20.0, 50.0] and result.repair_history == []
    assert result.validation['watertight'] is True
    return result


def test_conversation_version_list_metadata_only(client):
    conv = _new_conversation(client, 'History')
    v1 = _generate_cylinder(client, conv['id'])
    refined = client.post(f"/api/models/{v1['model_id']}/refine",
                          json={'instruction': 'Make it 5 mm taller', 'interpreter': 'local'})
    assert refined.status_code == 200, refined.text
    served = _history(client, conv['id'])
    assert served['total'] == 2
    assert [item['version'] for item in served['items']] == [2, 1]
    newer, older = served['items']
    assert newer['parent_version_id'] == v1['model_id'] and newer['parent_version'] == 1
    assert older['parent_version_id'] is None and older['parent_version'] is None
    assert newer['model_id'] != older['model_id']
    for item in served['items']:
        for field in METADATA_FIELDS:
            assert field in item, field
        assert isinstance(item['vertices'], int) and not isinstance(item['vertices'], list)
        assert isinstance(item['faces'], int) and not isinstance(item['faces'], list)
        assert isinstance(item['created_at'], str) and item['created_at']
        assert isinstance(item['provider'], str) and item['provider']
        assert isinstance(item['dimensions_mm'], list) and len(item['dimensions_mm']) == 3
        assert item['artifact_available'] is True
        assert 'creation_script' not in item
        assert all(len(value) <= 8 for value in item.values() if isinstance(value, list))


def test_other_user_cannot_list_or_load_versions(client, database):
    conv = _new_conversation(client, 'Private')
    made = _generate_cylinder(client, conv['id'])
    client.test_identity['user'] = database['other']
    assert client.get(f"/api/conversations/{conv['id']}/models").status_code == 404
    assert client.get(f"/api/models/{made['model_id']}").status_code == 404


def test_unrelated_parent_id_rejected(client):
    origin = _new_conversation(client, 'Origin')
    made = _generate_cylinder(client, origin['id'])
    other = _new_conversation(client, 'Unrelated')
    spec = {'shape_type': 'box', 'dimensions_mm': {'width': 10.0, 'depth': 10.0, 'height': 10.0}}
    rejected = client.post('/api/models/generate', json={
        'conversation_id': other['id'], 'spec': spec, 'parent_model_id': made['model_id']})
    assert rejected.status_code == 404, rejected.text
    assert rejected.json()['detail']['message'] == 'Parent model not found in this conversation.'


def test_script_version_list_marks_script_and_hides_code(client, monkeypatch, cylinder_generation):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(codegen, 'build_script', lambda request: cylinder_generation)
    conv = _new_conversation(client, 'Scripted')
    made = client.post('/api/models/generate', json={
        'conversation_id': conv['id'], 'prompt': CYLINDER_PROMPT, 'interpreter': 'gemini'})
    assert made.status_code == 200, made.text
    assert made.json()['geometry_generation_method'] == codegen.SCRIPT_METHOD
    item = _history(client, conv['id'])['items'][0]
    assert item['script_available'] is True
    assert item['provider'] == 'gemini'
    assert item['generation_method'] == codegen.SCRIPT_METHOD
    assert 'creation_script' not in item
    assert not any('import cadquery' in str(value) for value in item.values())


def test_refine_from_historical_version_preserves_newer(client):
    conv = _new_conversation(client, 'Branch')
    v1 = _generate_cylinder(client, conv['id'])
    v2 = client.post(f"/api/models/{v1['model_id']}/refine",
                     json={'instruction': 'Make it 5 mm taller', 'interpreter': 'local'})
    assert v2.status_code == 200, v2.text
    v2 = v2.json()
    branched = client.post(f"/api/models/{v1['model_id']}/refine",
                           json={'instruction': 'Make it 3 mm taller', 'interpreter': 'local'})
    assert branched.status_code == 200, branched.text
    branched = branched.json()
    assert branched['version'] == 3 and branched['parent_version_id'] == v1['model_id']
    served = _history(client, conv['id'])
    assert served['total'] == 3
    assert [item['version'] for item in served['items']] == [3, 2, 1]
    by_version = {item['version']: item for item in served['items']}
    assert by_version[3]['parent_version_id'] == v1['model_id'] and by_version[3]['parent_version'] == 1
    assert by_version[2]['parent_version_id'] == v1['model_id'] and by_version[2]['parent_version'] == 1
    assert by_version[2]['model_id'] == v2['model_id']
    reloaded = client.get(f"/api/models/{v2['model_id']}")
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()['version'] == 2 and reloaded.json()['parent_version_id'] == v1['model_id']


def test_historical_export_stl_glb(client):
    conv = _new_conversation(client, 'Exports')
    v1 = _generate_cylinder(client, conv['id'])
    refined = client.post(f"/api/models/{v1['model_id']}/refine",
                          json={'instruction': 'Make it 5 mm taller', 'interpreter': 'local'})
    assert refined.status_code == 200, refined.text
    for fmt in ('stl', 'glb'):
        exported = client.get(f"/api/models/{v1['model_id']}/export?format={fmt}")
        assert exported.status_code == 200, exported.text
        assert len(exported.content) > 100


def test_missing_artifact_metadata_only_model(client, database):
    conv = _new_conversation(client, 'Ghost')
    model_id = str(uuid.uuid4())
    with database['factory']() as db:
        db.add(GeneratedModel(id=model_id, owner_id=database['user'].id, conversation_id=conv['id'],
                              shape_type='box', generation_method='parametric_cad', version=1,
                              parameters_json={'shape_type': 'box',
                                               'dimensions_mm': {'width': 10.0, 'depth': 10.0, 'height': 10.0}},
                              validation_json={'watertight': True, 'vertices': 8, 'faces': 12,
                                               'dimensions_mm': [10.0, 10.0, 10.0]},
                              storage_key=str(uuid.uuid4())))
        db.commit()
    assert client.get(f'/api/models/{model_id}').status_code == 410
    assert client.get(f'/api/models/{model_id}/export?format=stl').status_code == 410
    item = _history(client, conv['id'])['items'][0]
    assert item['model_id'] == model_id and item['version'] == 1
    assert item['artifact_available'] is False and item['step_available'] is False


def test_invalid_version_reference(client):
    missing = str(uuid.uuid4())
    assert client.get(f'/api/models/{missing}').status_code == 404
    assert client.get(f'/api/conversations/{missing}/models').status_code == 404


def test_failed_refinement_leaves_no_partial_state(client, database, monkeypatch, cylinder_generation):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(codegen, 'build_script', lambda request: cylinder_generation)
    conv = _new_conversation(client, 'Rollback')
    made = client.post('/api/models/generate', json={
        'conversation_id': conv['id'], 'prompt': CYLINDER_PROMPT, 'interpreter': 'gemini'})
    assert made.status_code == 200, made.text

    def fail_when_asked(request):
        raise codegen.CodegenError('timeout', 'Gemini script generation timed out.')

    monkeypatch.setattr(codegen, 'build_script', fail_when_asked)
    failed = client.post(f"/api/models/{made.json()['model_id']}/refine",
                         json={'instruction': 'Make it 5 mm taller', 'interpreter': 'gemini'})
    assert failed.status_code == 503, failed.text
    assert failed.json()['detail']['message'] == 'Gemini script generation timed out.'
    with database['factory']() as db:
        models = db.query(GeneratedModel).filter(GeneratedModel.conversation_id == conv['id']).count()
        assistants = db.query(Message).filter(
            Message.conversation_id == conv['id'], Message.role == 'assistant').count()
    assert models == 1 and assistants == 1
    assert _history(client, conv['id'])['total'] == 1


def test_version_numbers_sequential_unique(client, monkeypatch, cylinder_generation):
    conv = _new_conversation(client, 'Sequence')
    v1 = _generate_cylinder(client, conv['id'])
    refined = client.post(f"/api/models/{v1['model_id']}/refine",
                          json={'instruction': 'Make it 5 mm taller', 'interpreter': 'local'})
    assert refined.status_code == 200, refined.text
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(codegen, 'build_script', lambda request: cylinder_generation)
    scripted = client.post('/api/models/generate', json={
        'conversation_id': conv['id'], 'prompt': CYLINDER_PROMPT, 'interpreter': 'gemini'})
    assert scripted.status_code == 200, scripted.text
    versions = [item['version'] for item in _history(client, conv['id'])['items']]
    assert versions == [3, 2, 1]
    assert len(set(versions)) == 3


def _history_page(client, conversation_id, limit=50, offset=0):
    served = client.get(f"/api/conversations/{conversation_id}/models?limit={limit}&offset={offset}")
    assert served.status_code == 200, served.text
    return served.json()


def test_version_pagination_first_page(client):
    conv = _new_conversation(client, 'Pagination')
    for _ in range(5):
        _generate_cylinder(client, conv['id'])
    page = _history_page(client, conv['id'], limit=2, offset=0)
    assert page['total'] == 5
    assert len(page['items']) == 2
    assert [item['version'] for item in page['items']] == [5, 4]


def test_version_pagination_second_page(client):
    conv = _new_conversation(client, 'Pagination2')
    for _ in range(5):
        _generate_cylinder(client, conv['id'])
    page = _history_page(client, conv['id'], limit=2, offset=2)
    assert page['total'] == 5
    assert len(page['items']) == 2
    assert [item['version'] for item in page['items']] == [3, 2]


def test_version_pagination_empty_page(client):
    conv = _new_conversation(client, 'Pagination3')
    _generate_cylinder(client, conv['id'])
    page = _history_page(client, conv['id'], limit=2, offset=10)
    assert page['total'] == 1
    assert page['items'] == []


def test_version_pagination_ownership_scoped(client, database):
    conv = _new_conversation(client, 'PrivatePagination')
    _generate_cylinder(client, conv['id'])
    client.test_identity['user'] = database['other']
    served = client.get(f"/api/conversations/{conv['id']}/models")
    assert served.status_code == 404
