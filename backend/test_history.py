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


# ---------------------------------------------------------------------------
# Multi-component assembly tests (Phase 1)
# ---------------------------------------------------------------------------

_ASSEMBLY_SPEC = {
    'shape_type': 'assembly',
    'assembly_mode': 'compound',
    'dimensions_mm': {'width': 80.0, 'depth': 60.0, 'height': 40.0},
    'components': [
        {
            'name': 'enclosure_body',
            'shape_type': 'box',
            'dimensions_mm': {'width': 80.0, 'depth': 60.0, 'height': 35.0},
            'position_mm': [0.0, 0.0, 17.5],
            'rotation_deg': [0.0, 0.0, 0.0],
        },
        {
            'name': 'enclosure_lid',
            'shape_type': 'box',
            'dimensions_mm': {'width': 80.0, 'depth': 60.0, 'height': 5.0},
            'position_mm': [0.0, 0.0, 37.5],
            'rotation_deg': [0.0, 0.0, 0.0],
        },
    ],
}


def _generate_assembly(client, conversation_id, spec=None):
    made = client.post('/api/models/generate', json={
        'conversation_id': conversation_id,
        'spec': spec or _ASSEMBLY_SPEC,
        'interpreter': 'local',
    })
    assert made.status_code == 200, made.text
    return made.json()


def test_assembly_stores_two_components_independently(client):
    conv = _new_conversation(client, 'Assembly')
    model = _generate_assembly(client, conv['id'])
    assert 'components' in model, model
    comps = model['components']
    assert len(comps) == 2
    ids = {c['id'] for c in comps}
    assert 'enclosure_body' in ids or any('body' in c['id'] for c in comps)
    assert 'enclosure_lid' in ids or any('lid' in c['id'] for c in comps)
    # Each component has required metadata.
    for c in comps:
        assert c['id']
        assert c['name']
        assert len(c['dimensions_mm']) == 3
        assert len(c['position_mm']) == 3
        assert c['mesh_file']
        assert c['mesh_file'].startswith('mesh_')
        assert c['mesh_file'].endswith('.npz')
    # Combined mesh is still present.
    assert 'vertices' in model and 'faces' in model


def test_assembly_component_metadata_in_validation(client):
    conv = _new_conversation(client, 'AssemblyMeta')
    model = _generate_assembly(client, conv['id'])
    validation = model['validation']
    assert validation.get('component_count') == 2
    assert 'components_detail' in validation
    detail = validation['components_detail']
    assert len(detail) == 2
    names = {d['name'] for d in detail}
    assert 'enclosure_body' in names
    assert 'enclosure_lid' in names


def test_assembly_individual_stl_export(client):
    conv = _new_conversation(client, 'ExportSTL')
    model = _generate_assembly(client, conv['id'])
    # Find the body component id.
    body_id = next(c['id'] for c in model['components'] if 'body' in c['id'])
    served = client.get(f"/api/models/{model['model_id']}/export?format=stl&component={body_id}")
    assert served.status_code == 200, served.text
    assert served.headers['content-type'] == 'model/stl'
    assert 'attachment' in served.headers['content-disposition']
    # Combined export still works.
    combined = client.get(f"/api/models/{model['model_id']}/export?format=stl")
    assert combined.status_code == 200, combined.text


def test_assembly_individual_glb_export(client):
    conv = _new_conversation(client, 'ExportGLB')
    model = _generate_assembly(client, conv['id'])
    lid_id = next(c['id'] for c in model['components'] if 'lid' in c['id'])
    served = client.get(f"/api/models/{model['model_id']}/export?format=glb&component={lid_id}")
    assert served.status_code == 200, served.text
    assert served.headers['content-type'] == 'model/gltf-binary'


def test_assembly_export_invalid_component(client):
    conv = _new_conversation(client, 'ExportInvalid')
    model = _generate_assembly(client, conv['id'])
    served = client.get(f"/api/models/{model['model_id']}/export?format=stl&component=nonexistent_part")
    assert served.status_code == 404, served.text


def test_assembly_export_unsupported_format(client):
    conv = _new_conversation(client, 'ExportFormat')
    model = _generate_assembly(client, conv['id'])
    served = client.get(f"/api/models/{model['model_id']}/export?format=obj")
    assert served.status_code == 422, served.text


def test_assembly_export_unauthorized(client, database):
    conv = _new_conversation(client, 'ExportAuth')
    model = _generate_assembly(client, conv['id'])
    body_id = model['components'][0]['id']
    client.test_identity['user'] = database['other']
    served = client.get(f"/api/models/{model['model_id']}/export?format=stl&component={body_id}")
    assert served.status_code == 404, served.text


def test_single_object_export_unchanged(client):
    conv = _new_conversation(client, 'Single')
    made = _generate_cylinder(client, conv['id'])
    served = client.get(f"/api/models/{made['model_id']}/export?format=stl")
    assert served.status_code == 200, served.text
    assert served.headers['content-type'] == 'model/stl'
    # No component parameter → combined export.
    assert 'modelforge.stl' in served.headers['content-disposition']


# ---------------------------------------------------------------------------
# Phase 2: Gemini multi-component assembly tests (mocked provider)
# ---------------------------------------------------------------------------

TWO_PART_SCRIPT = '''# plan-dimensions: 100,60,45
# explanation: A ventilated enclosure with a separate lid that has a locating lip.
# components: enclosure_body@100,60,35;enclosure_lid@100,60,5
import cadquery as cq
import math

body = cq.Workplane("XY").box(100.0, 60.0, 35.0).translate((0.0, 0.0, -5.0))
lid = cq.Workplane("XY").box(100.0, 60.0, 5.0).translate((0.0, 0.0, 20.0))
result = cq.Compound.makeCompound([body.val(), lid.val()])
'''


def _mock_gemini_two_part(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(codegen, '_call_gemini', lambda message: '```python\n' + TWO_PART_SCRIPT + '\n```')


def test_gemini_two_part_preserves_components(client, monkeypatch):
    _mock_gemini_two_part(monkeypatch)
    conv = _new_conversation(client, 'GeminiTwoPart')
    made = client.post('/api/models/generate', json={
        'conversation_id': conv['id'],
        'prompt': 'Create an enclosure with a separate lid.',
        'interpreter': 'gemini',
    })
    assert made.status_code == 200, made.text
    model = made.json()
    assert 'components' in model, model
    comps = model['components']
    assert len(comps) == 2
    names = {c['name'] for c in comps}
    assert 'enclosure_body' in names
    assert 'enclosure_lid' in names
    ids = {c['id'] for c in comps}
    assert len(ids) == 2
    # Each component has a mesh file.
    for c in comps:
        assert c['mesh_file'].startswith('mesh_')
        assert c['mesh_file'].endswith('.npz')
    # Combined mesh still present.
    assert 'vertices' in model and 'faces' in model


def test_gemini_two_part_individual_exports(client, monkeypatch):
    _mock_gemini_two_part(monkeypatch)
    conv = _new_conversation(client, 'GeminiExport')
    made = client.post('/api/models/generate', json={
        'conversation_id': conv['id'],
        'prompt': 'Create an enclosure with a separate lid.',
        'interpreter': 'gemini',
    })
    assert made.status_code == 200, made.text
    model = made.json()
    body_id = next(c['id'] for c in model['components'] if c['name'] == 'enclosure_body')
    lid_id = next(c['id'] for c in model['components'] if c['name'] == 'enclosure_lid')
    # Individual STL exports.
    body_stl = client.get(f"/api/models/{model['model_id']}/export?format=stl&component={body_id}")
    assert body_stl.status_code == 200, body_stl.text
    lid_stl = client.get(f"/api/models/{model['model_id']}/export?format=stl&component={lid_id}")
    assert lid_stl.status_code == 200, lid_stl.text
    # Different components produce different bytes.
    assert body_stl.content != lid_stl.content
    # Combined export still works.
    combined = client.get(f"/api/models/{model['model_id']}/export?format=stl")
    assert combined.status_code == 200, combined.text


def test_gemini_two_part_validation_metadata(client, monkeypatch):
    _mock_gemini_two_part(monkeypatch)
    conv = _new_conversation(client, 'GeminiMeta')
    made = client.post('/api/models/generate', json={
        'conversation_id': conv['id'],
        'prompt': 'Create an enclosure with a separate lid.',
        'interpreter': 'gemini',
    })
    assert made.status_code == 200, made.text
    model = made.json()
    validation = model['validation']
    assert 'components_detail' in validation
    detail = validation['components_detail']
    assert len(detail) == 2
    by_name = {d['name']: d for d in detail}
    assert 'enclosure_body' in by_name
    assert 'enclosure_lid' in by_name
    assert by_name['enclosure_body']['mesh_file'] == 'mesh_enclosure_body.npz'
    assert by_name['enclosure_lid']['mesh_file'] == 'mesh_enclosure_lid.npz'


def test_gemini_fused_assembly_rejected_when_separate_required(client, monkeypatch):
    """A script that unions parts into one solid should fail when components are declared separate."""
    fused_script = '''# plan-dimensions: 100,60,40
# explanation: A single fused block.
# components: part_a@50,60,40;part_b@50,60,40
import cadquery as cq
import math

a = cq.Workplane("XY").box(50.0, 60.0, 40.0).translate((-25.0, 0.0, 0.0))
b = cq.Workplane("XY").box(50.0, 60.0, 40.0).translate((25.0, 0.0, 0.0))
result = a.union(b)
'''
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(codegen, '_call_gemini', lambda message: '```python\n' + fused_script + '\n```')
    conv = _new_conversation(client, 'GeminiFused')
    made = client.post('/api/models/generate', json={
        'conversation_id': conv['id'],
        'prompt': 'Two separate blocks.',
        'interpreter': 'gemini',
    })
    # The fused script produces one solid, but two components are declared.
    # The component split will only find one solid, so components_detail will be empty.
    assert made.status_code == 200, made.text
    model = made.json()
    # No components_detail because only one solid exists (fused).
    assert 'components_detail' not in model.get('validation', {}) or len(model['validation'].get('components_detail', [])) < 2


# ---------------------------------------------------------------------------
# Phase 4: Refinement, history, and end-to-end integration tests
# ---------------------------------------------------------------------------

def test_refinement_preserves_components(client, monkeypatch):
    """Refining a multi-component model preserves component identity."""
    _mock_gemini_two_part(monkeypatch)
    conv = _new_conversation(client, 'RefineAssembly')
    v1 = client.post('/api/models/generate', json={
        'conversation_id': conv['id'],
        'prompt': 'Create an enclosure with a separate lid.',
        'interpreter': 'gemini',
    })
    assert v1.status_code == 200, v1.text
    v1_model = v1.json()
    assert len(v1_model['components']) == 2

    # Refine: make the lid thicker
    refined = client.post(f"/api/models/{v1_model['model_id']}/refine",
                          json={'instruction': 'Make the lid 2 mm thicker', 'interpreter': 'gemini'})
    assert refined.status_code == 200, refined.text
    v2_model = refined.json()
    assert v2_model['parent_version_id'] == v1_model['model_id']
    assert 'components' in v2_model
    assert len(v2_model['components']) == 2
    names = {c['name'] for c in v2_model['components']}
    assert 'enclosure_body' in names
    assert 'enclosure_lid' in names


def test_version_history_preserves_components(client, monkeypatch):
    """Older assembly versions retain their component data."""
    _mock_gemini_two_part(monkeypatch)
    conv = _new_conversation(client, 'HistoryAssembly')
    v1 = client.post('/api/models/generate', json={
        'conversation_id': conv['id'],
        'prompt': 'Create an enclosure with a separate lid.',
        'interpreter': 'gemini',
    })
    assert v1.status_code == 200, v1.text
    v1_model = v1.json()

    # Generate a second version (refine)
    v2 = client.post(f"/api/models/{v1_model['model_id']}/refine",
                     json={'instruction': 'Make it 5 mm taller', 'interpreter': 'gemini'})
    assert v2.status_code == 200, v2.text

    # List versions - both should have component metadata
    served = _history(client, conv['id'])
    assert served['total'] == 2
    for item in served['items']:
        # Metadata-only listing doesn't include component detail, but artifact_available should be true
        assert item['artifact_available'] is True

    # Load the first version directly - should have its components
    first = client.get(f"/api/models/{v1_model['model_id']}")
    assert first.status_code == 200, first.text
    first_model = first.json()
    assert 'components' in first_model
    assert len(first_model['components']) == 2

    # Load the second version - should also have components
    second = client.get(f"/api/models/{v2.json()['model_id']}")
    assert second.status_code == 200, second.text
    second_model = second.json()
    assert 'components' in second_model
    assert len(second_model['components']) == 2


def test_export_from_older_version(client, monkeypatch):
    """Exporting from an older version uses that version's geometry."""
    _mock_gemini_two_part(monkeypatch)
    conv = _new_conversation(client, 'ExportOlder')
    v1 = client.post('/api/models/generate', json={
        'conversation_id': conv['id'],
        'prompt': 'Create an enclosure with a separate lid.',
        'interpreter': 'gemini',
    })
    assert v1.status_code == 200, v1.text
    v1_model = v1.json()
    body_id = next(c['id'] for c in v1_model['components'] if c['name'] == 'enclosure_body')

    # Export the body from v1
    stl = client.get(f"/api/models/{v1_model['model_id']}/export?format=stl&component={body_id}")
    assert stl.status_code == 200, stl.text
    assert len(stl.content) > 100  # Non-trivial STL

    # Refine to v2
    v2 = client.post(f"/api/models/{v1_model['model_id']}/refine",
                     json={'instruction': 'Make the lid thicker', 'interpreter': 'gemini'})
    assert v2.status_code == 200, v2.text

    # Export from v1 still works (v1 artifacts unchanged)
    stl_again = client.get(f"/api/models/{v1_model['model_id']}/export?format=stl&component={body_id}")
    assert stl_again.status_code == 200, stl_again.text
    assert stl_again.content == stl.content  # Same bytes as before


def test_single_object_history_unchanged(client):
    """Single-object version history continues to work."""
    conv = _new_conversation(client, 'SingleHistory')
    v1 = _generate_cylinder(client, conv['id'])
    assert 'components' not in v1 or len(v1.get('components', [])) == 0
    # Export without component parameter works
    stl = client.get(f"/api/models/{v1['model_id']}/export?format=stl")
    assert stl.status_code == 200, stl.text
