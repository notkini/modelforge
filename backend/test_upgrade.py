from datetime import datetime, timedelta, timezone
import json

import pytest
import trimesh
import uuid
from sqlalchemy import select

from . import api, codegen, nlp
from .auth import AuthSession
from .db_models import GeneratedModel
from .schemas import GeometryInterpretation
from .script_runner import ScriptSandboxError


def test_user_identity_and_conversation_isolation(client, database):
    own = client.post('/api/conversations', json={'title': 'Mine'}).json()
    assert client.get(f"/api/conversations/{own['id']}").status_code == 200
    client.test_identity['user'] = database['other']
    assert client.get(f"/api/conversations/{own['id']}").status_code == 404
    assert client.patch(f"/api/conversations/{own['id']}", json={'title': 'stolen'}).status_code == 404
    assert client.delete(f"/api/conversations/{own['id']}").status_code == 404


def test_model_export_is_owner_scoped(client, database):
    conv = client.post('/api/conversations', json={'title': 'Mine'}).json()
    made = client.post('/api/models/generate', json={
        'conversation_id': conv['id'], 'prompt': '20 mm diameter cylinder, 50 mm height', 'interpreter': 'local'
    }).json()
    client.test_identity['user'] = database['other']
    assert client.get(f"/api/models/{made['model_id']}").status_code == 404
    assert client.get(f"/api/models/{made['model_id']}/export?format=stl").status_code == 404


def test_dimensioned_assembly_generation_persistence_and_exports(client):
    conv=client.post('/api/conversations',json={'title':'Miniature furniture'}).json()
    parts=[{'name':'tabletop','shape_type':'box','dimensions_mm':{'width':40,'depth':30,'height':3},'position_mm':[0,0,18.5]}]
    for x in (-18.5,18.5):
        for y in (-13.5,13.5):
            parts.append({'name':'leg','shape_type':'box','dimensions_mm':{'width':3,'depth':3,'height':37},'position_mm':[x,y,-1.5]})
    spec={'shape_type':'assembly','dimensions_mm':{'width':40,'depth':30,'height':40},'components':parts,'object_identity':'miniature table','intended_use':'dollhouse furniture'}
    made=client.post('/api/models/generate',json={'conversation_id':conv['id'],'spec':spec})
    assert made.status_code==200,made.text
    result=made.json()
    assert result['generation_method']=='parametric_cad'
    assert result['validation']['component_count']==5
    assert result['validation']['object_identity']=='miniature table'
    assert result['validation']['dimensions_mm']==pytest.approx([40,30,40],abs=.05)
    assert len(result['vertices'])>0 and len(result['faces'])>0
    for fmt in ('stl','glb'):
        exported=client.get(f"/api/models/{result['model_id']}/export?format={fmt}")
        assert exported.status_code==200 and len(exported.content)>100


def test_google_signin_verification_nonce_session_and_logout(unauth_client, monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'client-id.apps.googleusercontent.com')
    config = unauth_client.get('/api/auth/config').json()
    claims = {'aud': 'client-id.apps.googleusercontent.com', 'iss': 'https://accounts.google.com',
              'nonce': config['nonce'], 'sub': 'google-sub-new', 'email': 'new@example.test',
              'email_verified': True, 'name': 'New User'}
    monkeypatch.setattr(api, 'verify_google_token', lambda token, audience: claims)
    response = unauth_client.post('/api/auth/google', json={'credential': 'x' * 60},
                                  headers={'X-CSRF-Token': config['csrf_token']})
    assert response.status_code == 200, response.text
    assert response.json()['user']['email'] == 'new@example.test'
    assert response.cookies.get('modelforge_session')
    assert unauth_client.get('/api/auth/me').json()['user']['id'] == response.json()['user']['id']
    assert unauth_client.post('/api/auth/logout', json={}, headers={'X-CSRF-Token': config['csrf_token']}).status_code == 200
    assert unauth_client.get('/api/auth/me').status_code == 401


def test_email_register_login_and_logout(unauth_client):
    csrf=unauth_client.get('/api/auth/config').json()['csrf_token']
    headers={'X-CSRF-Token':csrf}
    created=unauth_client.post('/api/auth/register',headers=headers,json={
        'name':'Ada Modeler','email':'ADA@example.test','password':'long-enough-demo-password'
    })
    assert created.status_code==200,created.text
    user=created.json()['user']
    assert user['email']=='ada@example.test' and user['name']=='Ada Modeler'
    assert unauth_client.get('/api/auth/me').json()['user']['id']==user['id']
    duplicate=unauth_client.post('/api/auth/register',headers=headers,json={
        'name':'Ada','email':'ada@example.test','password':'another-long-password'
    })
    assert duplicate.status_code==409
    assert unauth_client.post('/api/auth/logout',headers=headers,json={}).status_code==200
    bad=unauth_client.post('/api/auth/login',headers=headers,json={'email':'ada@example.test','password':'wrong'})
    assert bad.status_code==401 and bad.json()['detail']['message']=='Email or password is incorrect.'
    signed=unauth_client.post('/api/auth/login',headers=headers,json={'email':'ADA@example.test','password':'long-enough-demo-password'})
    assert signed.status_code==200 and signed.json()['user']['id']==user['id']


def test_password_hash_is_salted_and_verified():
    from .auth import hash_password, verify_password
    first=hash_password('long-enough-demo-password')
    second=hash_password('long-enough-demo-password')
    assert first!=second and verify_password('long-enough-demo-password',first)
    assert not verify_password('incorrect-password',first)


def test_google_rejects_invalid_token_audience_and_csrf(unauth_client, monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'expected-client')
    config = unauth_client.get('/api/auth/config').json()
    monkeypatch.setattr(api, 'verify_google_token', lambda token, audience: {
        'aud': 'wrong-client', 'iss': 'https://accounts.google.com', 'nonce': config['nonce'],
        'sub': 'x', 'email': 'x@example.test', 'email_verified': True
    })
    assert unauth_client.post('/api/auth/google', json={'credential': 'x' * 60},
                              headers={'X-CSRF-Token': config['csrf_token']}).status_code == 401
    assert unauth_client.post('/api/auth/google', json={'credential': 'x' * 60}).status_code == 403


def test_google_rejects_expired_or_unverifiable_token(unauth_client, monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'expected-client')
    config = unauth_client.get('/api/auth/config').json()
    monkeypatch.setattr(api, 'verify_google_token', lambda token, audience: (_ for _ in ()).throw(ValueError('expired')))
    response = unauth_client.post('/api/auth/google', json={'credential': 'x' * 60},
                                  headers={'X-CSRF-Token': config['csrf_token']})
    assert response.status_code == 401


def test_expired_session_rejected(unauth_client, database):
    raw = 'expired-raw-token'
    from .auth import _digest
    with database['factory']() as db:
        db.add(AuthSession(id=str(uuid.uuid4()), user_id=database['user'].id, token_hash=_digest(raw), csrf_hash=_digest('csrf'),
                           expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()
    unauth_client.cookies.set('modelforge_session', raw)
    assert unauth_client.get('/api/auth/me').status_code == 401


CYLINDER_SCRIPT = '''# plan-dimensions: 20,20,50
# explanation: A 20 mm diameter cylinder, 50 mm tall.
import cadquery as cq
import math

result = cq.Workplane("XY").circle(10.0).extrude(50.0).translate((0.0, 0.0, -25.0))
'''


PHONE_STAND_SCRIPT = '''# plan-dimensions: 80,80,100
# explanation: Compact phone stand with a fused base, upright support, and front lip.
import cadquery as cq
import math

base = cq.Workplane("XY").box(80.0, 80.0, 10.0).translate((0.0, 0.0, -45.0))
back = cq.Workplane("XY").box(80.0, 10.0, 90.0).translate((0.0, 35.0, 5.0))
lip = cq.Workplane("XY").box(80.0, 15.0, 15.0).translate((0.0, -32.5, -35.0))
result = base.union(back).union(lip)
'''


TALLER_STAND_SCRIPT = '''# plan-dimensions: 80,80,120
# explanation: Taller phone stand with the back support raised to 110 mm.
import cadquery as cq
import math

base = cq.Workplane("XY").box(80.0, 80.0, 10.0).translate((0.0, 0.0, -55.0))
back = cq.Workplane("XY").box(80.0, 10.0, 110.0).translate((0.0, 35.0, 5.0))
lip = cq.Workplane("XY").box(80.0, 15.0, 15.0).translate((0.0, -32.5, -45.0))
result = base.union(back).union(lip)
'''


LIP_STAND_SCRIPT = '''# plan-dimensions: 80,80,100
# explanation: Raised retaining lip at 20 mm height for a firmer phone rest.
import cadquery as cq
import math

base = cq.Workplane("XY").box(80.0, 80.0, 10.0).translate((0.0, 0.0, -45.0))
back = cq.Workplane("XY").box(80.0, 10.0, 90.0).translate((0.0, 35.0, 5.0))
lip = cq.Workplane("XY").box(80.0, 20.0, 20.0).translate((0.0, -30.0, -35.0))
result = base.union(back).union(lip)
'''


def _fake_generation(code, plan):
    mesh = trimesh.creation.box(extents=[8.0, 8.0, 8.0])
    return codegen.CodegenResult(
        code=code, explanation='A cylinder.', attempts=1, plan=list(plan), repair_history=[],
        mesh=mesh, cad_shape=None,
        validation={'cad_solid_valid': True, 'solid_count': 1, 'volume_mm3': 512.0,
                    'dimensions_mm': list(plan), 'requested_dimensions_mm': list(plan),
                    'dimension_error_mm': [0.0, 0.0, 0.0], 'dimension_tolerance_mm': 0.5,
                    'dimensions_within_tolerance': True, 'watertight': True,
                    'winding_consistent': True, 'vertices': 8, 'faces': 12,
                    'printability': 'validated watertight CAD tessellation'})


def test_build_script_executes_and_validates_generated_code(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')
    monkeypatch.setattr(codegen, '_call_gemini', lambda message: '```python\n' + CYLINDER_SCRIPT + '\n```')
    result = codegen.build_script(codegen.ScriptRequest(
        prompt='Create a cylinder with a 20 mm diameter and 50 mm height.'))
    assert result.attempts == 1 and result.plan == [20.0, 20.0, 50.0] and result.repair_history == []
    assert 'import cadquery as cq' in result.code and result.explanation.startswith('A 20 mm')
    validation = result.validation
    assert validation['cad_solid_valid'] and validation['watertight'] and validation['solid_count'] == 1
    assert validation['dimensions_mm'] == pytest.approx([20.0, 20.0, 50.0], abs=0.5)
    assert validation['dimensions_within_tolerance'] is True
    assert validation['requested_dimensions_mm'] == pytest.approx([20.0, 20.0, 50.0])
    assert isinstance(result.mesh, trimesh.Trimesh) and len(result.mesh.faces) > 0


def test_gemini_timeout_fails_without_fallback(monkeypatch, caplog):
    caplog.set_level('INFO', logger='backend.codegen')
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    calls = []
    def timeout(message):
        calls.append(1)
        raise TimeoutError('timed out')
    monkeypatch.setattr(codegen, '_call_gemini', timeout)
    with pytest.raises(codegen.CodegenError, match='Gemini timed out before completing the request.') as caught:
        codegen.build_script(codegen.ScriptRequest(prompt='Create a cylinder 20 mm diameter and 50 mm tall.'))
    assert len(calls) == codegen.MAX_ATTEMPTS
    hits = [item for item in caught.value.history if item.get('code') == 'timeout']
    assert len(hits) == codegen.MAX_ATTEMPTS - 1 and all(item['stage'] == 'response' for item in hits)
    assert 'TimeoutError' not in json.dumps(caught.value.history)
    assert 'codegen attempt 1' in caplog.text


def test_gemini_invalid_request_sanitized_without_fallback(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    class InvalidArgument(Exception):
        code = 400
        status = 'INVALID_ARGUMENT'
    monkeypatch.setattr(codegen, '_call_gemini',
                        lambda message: (_ for _ in ()).throw(InvalidArgument('secret-bearing exception text')))
    with pytest.raises(codegen.CodegenError, match='Gemini rejected the structured interpretation request as invalid.') as caught:
        codegen.build_script(codegen.ScriptRequest(prompt='Create a cylinder 20 mm diameter and 50 mm tall.'))
    text = caught.value.message + json.dumps(caught.value.history)
    assert 'secret-bearing exception text' not in text
    assert all(item['code'] == 'invalid_request' for item in caught.value.history)


def test_codegen_rejects_scripts_missing_contract_parts(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    responses = iter([
        'plain prose without any fence',
        '```plain\nresult = 1\n```',
        '```python\n# plan-dimensions: 20,20,600\n# explanation: too tall\nimport cadquery as cq\n'
        'box = cq.Workplane("XY").box(20, 20, 600)\nresult = box\n```',
    ])
    monkeypatch.setattr(codegen, '_call_gemini', lambda message: next(responses))
    with pytest.raises(codegen.CodegenError) as caught:
        codegen.build_script(codegen.ScriptRequest(prompt='Create a cube.'))
    codes = [item['code'] for item in caught.value.history]
    assert caught.value.code == 'invalid_plan'
    assert codes == ['no_code_block', 'missing_plan_header']


def test_gemini_sdk_script_success_and_invalid_response(monkeypatch):
    from types import SimpleNamespace
    from google import genai
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')
    calls = []
    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text='```python\n' + CYLINDER_SCRIPT + '\n```')
    class FakeClient:
        def __init__(self, **kwargs): self.models = FakeModels()
    monkeypatch.setattr(genai, 'Client', FakeClient)
    text = codegen._call_gemini('Create a cylinder with a 20 mm diameter and 50 mm height.')
    assert text.startswith('```python') and 'plan-dimensions: 20,20,50' in text
    assert calls[0]['model'] == 'gemini-3.5-flash-lite'
    assert calls[0]['config'].temperature == 0
    assert calls[0]['config'].max_output_tokens == codegen.MAX_OUTPUT_TOKENS
    class EmptyModels:
        def generate_content(self, **kwargs): return SimpleNamespace(text='')
    class EmptyClient:
        def __init__(self, **kwargs): self.models = EmptyModels()
    monkeypatch.setattr(genai, 'Client', EmptyClient)
    with pytest.raises(codegen.CodegenError, match='Gemini returned no script text.'):
        codegen._call_gemini('Make an unsupported object.')


def test_missing_gemini_key_defaults_to_local(monkeypatch, caplog):
    caplog.set_level('INFO', logger='backend.nlp')
    monkeypatch.setenv('GEMINI_API_KEY', '')
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')
    result = nlp.interpret('Create a cylinder 20 mm diameter and 50 mm tall')
    assert result.provider == 'local-deterministic'
    assert result.fallback_reason == 'Gemini API key is not configured; interpreted with the local deterministic parser.'
    assert 'failure_category=missing_api_key' in caplog.text


def test_structured_interpreter_rejects_gemini_selection_even_with_key(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    for kwargs in ({'interpreter': 'gemini'}, {}):
        with pytest.raises(nlp.ParseError, match='Gemini now generates CadQuery code directly'):
            nlp.interpret('Create a cylinder 20 mm diameter and 50 mm tall', **kwargs)


def test_generate_api_reports_gemini_script_provider(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(codegen, 'build_script', lambda request: _fake_generation(CYLINDER_SCRIPT, [20.0, 20.0, 50.0]))
    conv = client.post('/api/conversations', json={'title': 'Provider check'}).json()
    prompt = 'Create a cylinder with a 20 mm diameter and 50 mm height.'
    client.post(f"/api/conversations/{conv['id']}/messages", json={'role': 'user', 'text': prompt})
    made = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'prompt': prompt})
    assert made.status_code == 200, made.text
    result = made.json()
    assert result['provider'] == 'gemini' and result['interpretation_provider'] == 'gemini'
    assert result['geometry_generation_method'] == 'cadquery_script'
    assert result['generation_method'] == 'cadquery_script'
    assert result['fallback_reason'] is None and result['script_attempts'] == 1
    assert result['spec']['shape_type'] == 'cadquery_script'
    assert 'import cadquery as cq' in result['spec']['creation_script']
    assert result['spec']['plan_dimensions_mm'] == pytest.approx([20.0, 20.0, 50.0])
    assert result['validation']['requested_dimensions_mm'] == pytest.approx([20.0, 20.0, 50.0])
    assert result['validation']['script_attempts'] == 1
    assert 'Gemini-written CadQuery model script' in result['message']
    fetched = client.get(f"/api/models/{result['model_id']}").json()
    assert fetched['spec']['creation_script'] == CYLINDER_SCRIPT
    assert fetched['spec']['plan_dimensions_mm'] == pytest.approx([20.0, 20.0, 50.0])
    for fmt in ('stl', 'glb'):
        exported = client.get(f"/api/models/{result['model_id']}/export?format={fmt}")
        assert exported.status_code == 200 and len(exported.content) > 100


def test_generate_api_surfaces_gemini_failure_without_fallback(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    conv = client.post('/api/conversations', json={'title': 'Provider check'}).json()
    monkeypatch.setattr(codegen, 'build_script', lambda request: (_ for _ in ()).throw(
        codegen.CodegenError('timeout', 'Gemini timed out before completing the request.')))
    prompt = 'Create a cylinder 20 mm diameter and 50 mm tall.'
    client.post(f"/api/conversations/{conv['id']}/messages", json={'role': 'user', 'text': prompt})
    failed = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'prompt': prompt})
    assert failed.status_code == 503, failed.text
    assert failed.json()['detail']['message'] == 'Gemini timed out before completing the request.'
    conversation = client.get(f"/api/conversations/{conv['id']}").json()
    assert any(m['role'] == 'user' and m['text'] == prompt for m in conversation['messages'])
    assert all(not m.get('model') for m in conversation['messages'])


def test_generate_api_surfaces_gemini_contract_error_without_fallback(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    conv = client.post('/api/conversations', json={'title': 'Provider check'}).json()
    monkeypatch.setattr(codegen, 'build_script', lambda request: (_ for _ in ()).throw(
        codegen.CodegenError('structured_response_invalid', 'Gemini returned no script text.')))
    failed = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'prompt': 'make anything'})
    assert failed.status_code == 422, failed.text
    assert failed.json()['detail']['message'] == 'Gemini returned no script text.'


def test_generate_api_maps_sandbox_kill_to_503(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    conv = client.post('/api/conversations', json={'title': 'Sandbox check'}).json()
    monkeypatch.setattr(codegen, '_call_gemini', lambda message: '```python\n' + CYLINDER_SCRIPT + '\n```')
    monkeypatch.setattr(codegen, 'run_script', lambda script: (_ for _ in ()).throw(
        ScriptSandboxError('cpu_limit', 'The generated script exceeded its CPU time budget and was terminated.')))
    failed = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'prompt': 'make anything'})
    assert failed.status_code == 503, failed.text
    assert failed.json()['detail']['message'] == 'The generated script exceeded its CPU time budget and was terminated.'


def test_explicit_local_selection_bypasses_gemini(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    def gemini_faceplants(request): raise AssertionError('explicit local selection must not call Gemini')
    monkeypatch.setattr(codegen, 'build_script', gemini_faceplants)
    conv = client.post('/api/conversations', json={'title': 'Local choice'}).json()
    made = client.post('/api/models/generate', json={'conversation_id': conv['id'],
        'prompt': '20 mm diameter cylinder, 50 mm height', 'interpreter': 'local'})
    assert made.status_code == 200, made.text
    assert made.json()['provider'] == 'local-deterministic'
    assert made.json()['interpretation_provider'] == 'local-deterministic'
    assert not made.json()['fallback_reason']
    conversation = client.get(f"/api/conversations/{conv['id']}").json()
    assistant = [m for m in conversation['messages'] if m['role'] == 'assistant']
    assert assistant and assistant[-1]['metadata'].get('interpretation_provider') == 'local-deterministic'


def test_missing_key_auto_resolves_to_local_generation(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', '')
    conv = client.post('/api/conversations', json={'title': 'Auto local'}).json()
    made = client.post('/api/models/generate', json={'conversation_id': conv['id'],
        'prompt': '20 mm diameter cylinder, 50 mm height'})
    assert made.status_code == 200, made.text
    assert made.json()['provider'] == 'local-deterministic'
    assert 'local deterministic parser' in made.json()['message']


def test_gemini_selected_without_key_is_rejected(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', '')
    conv = client.post('/api/conversations', json={'title': 'No key'}).json()
    made = client.post('/api/models/generate', json={'conversation_id': conv['id'],
        'prompt': '20 mm diameter cylinder, 50 mm height', 'interpreter': 'gemini'})
    assert made.status_code == 422, made.text
    assert made.json()['detail']['message'] == 'Gemini interpretation is selected but GEMINI_API_KEY is not configured on the server.'
    refine_target = client.post('/api/models/generate', json={'conversation_id': conv['id'],
        'prompt': '20 mm diameter cylinder, 50 mm height', 'interpreter': 'local'}).json()
    refined = client.post(f"/api/models/{refine_target['model_id']}/refine",
                          json={'instruction': 'Make it 5 mm taller', 'interpreter': 'gemini'})
    assert refined.status_code == 422, refined.text
    assert refined.json()['detail']['message'] == 'Gemini interpretation is selected but GEMINI_API_KEY is not configured on the server.'


def test_unknown_interpreter_value_is_rejected(client):
    conv=client.post('/api/conversations',json={'title':'Bad choice'}).json()
    made=client.post('/api/models/generate',json={'conversation_id':conv['id'],
        'prompt':'20 mm diameter cylinder, 50 mm height','interpreter':'claude'})
    assert made.status_code==422,made.text
    assert 'interpreter' in made.text


def test_parse_prompt_honors_interpreter_selection(client,monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','')
    blocked=client.post('/api/parse-prompt',json={'prompt':'20 mm cylinder, 50 mm tall','interpreter':'gemini'})
    assert blocked.status_code==422,blocked.text
    assert blocked.json()['detail']['message']=='Gemini interpretation is selected but GEMINI_API_KEY is not configured on the server.'
    exact=client.post('/api/parse-prompt',json={'prompt':'Create a cylinder with a 20 mm diameter and 50 mm height','interpreter':'local'})
    assert exact.status_code==200,exact.text
    assert exact.json()['provider']=='local-deterministic'
    assert exact.json()['spec']['shape_type']=='cylinder'


def test_invalid_model_schema_and_unsupported_features_rejected():
    with pytest.raises(Exception):
        GeometryInterpretation.model_validate({'shape_type': 'spaceship'})
    with pytest.raises(Exception):
        GeometryInterpretation(shape_type='organic', generation_method='gaussian-implicit',
            dimensions_mm={'width': float('nan'), 'depth': 20, 'height': 20})
    with pytest.raises(nlp.UnsupportedGeometry):
        nlp._local('4 mm plate with four mounting holes')


def test_model_versions_persist_and_link(client, database):
    conv = client.post('/api/conversations', json={'title': 'Versions'}).json()
    first = client.post('/api/models/generate', json={'conversation_id': conv['id'],
        'prompt': '20 mm diameter cylinder, 50 mm height', 'interpreter': 'local'}).json()
    second = client.post(f"/api/models/{first['model_id']}/refine", json={'instruction': 'Make it 10 mm taller', 'interpreter': 'local'}).json()
    with database['factory']() as db:
        row = db.scalar(select(GeneratedModel).where(GeneratedModel.id == second['model_id']))
        assert row.parent_version_id == first['model_id'] and row.version == 2


def test_phone_stand_script_generation_end_to_end(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(codegen, '_call_gemini', lambda message: '```python\n' + PHONE_STAND_SCRIPT + '\n```')
    conv = client.post('/api/conversations', json={'title': 'Phone stand'}).json()
    prompt = 'Create a compact phone stand with an 80 x 80 mm base, a 100 mm overall height and a front retaining lip.'
    client.post(f"/api/conversations/{conv['id']}/messages", json={'role': 'user', 'text': prompt})
    made = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'prompt': prompt, 'interpreter': 'gemini'})
    assert made.status_code == 200, made.text
    result = made.json()
    assert result['interpretation_provider'] == 'gemini' and result['generation_method'] == 'cadquery_script'
    validation = result['validation']
    assert validation['dimensions_mm'] == pytest.approx([80.0, 80.0, 100.0], abs=1.0)
    assert validation['dimensions_within_tolerance'] is True
    assert validation['watertight'] is True and validation['cad_solid_valid'] is True
    assert validation['solid_count'] == 1
    assert result['spec']['plan_dimensions_mm'] == pytest.approx([80.0, 80.0, 100.0])
    assert 'union(' in result['spec']['creation_script'] and 'import math' in result['spec']['creation_script']
    for fmt in ('stl', 'glb', ):
        exported = client.get(f"/api/models/{result['model_id']}/export?format={fmt}")
        assert exported.status_code == 200 and len(exported.content) > 100


def test_irreparable_assembly_spec_still_rejected_by_api(client):
    conv = client.post('/api/conversations', json={'title': 'Too small'}).json()
    spec = {'shape_type': 'assembly', 'dimensions_mm': {'width': 80, 'depth': 80, 'height': 220},
            'assembly_mode': 'union',
            'components': [{'name': 'cube', 'shape_type': 'box', 'dimensions_mm': {'width': 40, 'depth': 40, 'height': 40}, 'position_mm': [0, 0, 0]}]}
    made = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'spec': spec})
    assert made.status_code == 422, made.text
    assert 'differ from the request' in made.json()['detail']['message']


def _upright_stand_spec():
    return {'shape_type': 'assembly', 'dimensions_mm': {'width': 80, 'depth': 80, 'height': 100}, 'assembly_mode': 'union',
            'components': [
                {'name': 'base', 'shape_type': 'box', 'dimensions_mm': {'width': 80, 'depth': 80, 'height': 10}, 'position_mm': [0, 0, -45]},
                {'name': 'back_support', 'shape_type': 'box', 'dimensions_mm': {'width': 80, 'depth': 10, 'height': 90}, 'position_mm': [0, 35, 5]},
                {'name': 'retaining_lip', 'shape_type': 'box', 'dimensions_mm': {'width': 80, 'depth': 15, 'height': 15}, 'position_mm': [0, -32.5, -32.5]}]}


def test_refine_script_model_revises_previous_code(client, monkeypatch, database):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    seeds = iter(['```python\n' + PHONE_STAND_SCRIPT + '\n```', '```python\n' + TALLER_STAND_SCRIPT + '\n```'])
    seen = []
    def fake_gemini(message):
        seen.append(message)
        return next(seeds)
    monkeypatch.setattr(codegen, '_call_gemini', fake_gemini)
    conv = client.post('/api/conversations', json={'title': 'Bigger stand'}).json()
    made = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'prompt': 'phone stand', 'interpreter': 'gemini'})
    assert made.status_code == 200, made.text
    first = made.json()
    instruction = 'Make the whole stand 120 mm tall and keep the retaining lip.'
    client.post(f"/api/conversations/{conv['id']}/messages", json={'role': 'user', 'text': instruction})
    refined = client.post(f"/api/models/{first['model_id']}/refine", json={'instruction': instruction})
    assert refined.status_code == 200, refined.text
    result = refined.json()
    assert result['version'] == 2 and result['parent_version_id'] == first['model_id']
    assert result['interpretation_provider'] == 'gemini' and result['geometry_generation_method'] == 'cadquery_script'
    assert result['validation']['dimensions_mm'] == pytest.approx([80.0, 80.0, 120.0], abs=1.2)
    assert result['validation']['dimensions_within_tolerance'] and result['validation']['watertight']
    assert 'box(80.0, 10.0, 110.0' in result['spec']['creation_script']
    assert '80.0, 80.0, 10.0' in seen[-1] and 'plan-dimensions: 80,80,100' in seen[-1]
    assert 'Make the whole stand 120 mm tall' in seen[-1]
    with database['factory']() as db:
        row = db.scalar(select(GeneratedModel).where(GeneratedModel.id == result['model_id']))
        assert row.version == 2 and row.parent_version_id == first['model_id']


def test_refine_script_model_repairs_after_invalid_first_response(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(codegen, '_call_gemini', lambda message: '```python\n' + PHONE_STAND_SCRIPT + '\n```')
    conv = client.post('/api/conversations', json={'title': 'Repair path'}).json()
    made = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'prompt': 'phone stand', 'interpreter': 'gemini'})
    assert made.status_code == 200, made.text
    responses = iter(['missing fence prose only', '```python\n' + LIP_STAND_SCRIPT + '\n```'])
    monkeypatch.setattr(codegen, '_call_gemini', lambda message: next(responses))
    instruction = 'Raise the retaining lip to 20 mm height.'
    client.post(f"/api/conversations/{conv['id']}/messages", json={'role': 'user', 'text': instruction})
    refined = client.post(f"/api/models/{made.json()['model_id']}/refine", json={'instruction': instruction})
    assert refined.status_code == 200, refined.text
    result = refined.json()
    assert result['version'] == 2 and result['script_attempts'] == 2
    assert result['validation']['script_attempts'] == 2
    assert result['validation']['dimensions_mm'] == pytest.approx([80.0, 80.0, 100.0], abs=1.0)
    assert 'after 2 repair attempts' in result['message']


def test_spec_parent_refine_routes_to_gemini_script(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    def fake_gemini(message):
        seen.append(message)
        return '```python\n' + TALLER_STAND_SCRIPT + '\n```'
    seen = []
    monkeypatch.setattr(codegen, '_call_gemini', fake_gemini)
    conv = client.post('/api/conversations', json={'title': 'Spec to script'}).json()
    first = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'spec': _upright_stand_spec()})
    assert first.status_code == 200, first.text
    instruction = 'Convert this stand into a single fused CadQuery script and raise it to 120 mm.'
    client.post(f"/api/conversations/{conv['id']}/messages", json={'role': 'user', 'text': instruction})
    refined = client.post(f"/api/models/{first.json()['model_id']}/refine", json={'instruction': instruction})
    assert refined.status_code == 200, refined.text
    result = refined.json()
    assert result['geometry_generation_method'] == 'cadquery_script'
    assert result['validation']['dimensions_mm'] == pytest.approx([80.0, 80.0, 120.0], abs=1.2)
    # the previous parametric spec was included as structured context
    assert 'back_support' in seen[-1] and 'retaining_lip' in seen[-1]
    for axis in (80.0, 100.0):
        assert '%.2f' % axis in seen[-1]


def test_digest_raises_without_session_secret(monkeypatch):
    monkeypatch.setenv('SESSION_SECRET', '')
    from fastapi import HTTPException
    from .auth import _digest
    with pytest.raises(HTTPException) as exc:
        _digest('test-value')
    assert exc.value.status_code == 503


def test_digest_raises_with_weak_session_secret(monkeypatch):
    monkeypatch.setenv('SESSION_SECRET', 'replace-with-a-long-random-secret')
    from fastapi import HTTPException
    from .auth import _digest
    with pytest.raises(HTTPException) as exc:
        _digest('test-value')
    assert exc.value.status_code == 503


def test_digest_works_with_valid_session_secret(monkeypatch):
    monkeypatch.setenv('SESSION_SECRET', 'a-valid-secret-that-is-long-enough-for-testing-12345')
    from .auth import _digest
    result = _digest('test-value')
    assert isinstance(result, str) and len(result) == 64
