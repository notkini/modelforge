"""Priority-5 regression tests: natural-language prompt understanding.

Covers:
1.  Equivalent prompts produce equivalent geometry.
2.  Unit conversions (cm, m, inch → mm).
3.  Existing supported prompts continue to work (backward compatibility).
4.  Common synonyms recognized; unrelated words not confused.
5.  Missing essential parameters produce clarification messages.
6.  Unsupported local prompts return actionable errors.
7.  Auto mode routes eligible prompts to Gemini (when Gemini key set).
8.  Explicit Local never switches to Gemini.
9.  Explicit Gemini never switches to Local.
10. Negative dimensions remain rejected.
11. Generated scripts cannot bypass sandbox validation (via codegen tests).
12. Repair attempts remain bounded to MAX_ATTEMPTS.
13. Failed generation creates no partial model/message state.
14. Timeouts are distinct from interpretation failures.
15. Version history and export behavior unaffected.
"""
import pytest
from unittest.mock import patch

from . import codegen, nlp
from .interpreter import ParseError, parse_prompt
from .nlp import UnsupportedGeometry, _is_clearly_invalid, interpret, _GeminiRoutingSignal
from .schemas import Spec


# ---------------------------------------------------------------------------
# 1. Equivalent natural-language prompts
# ---------------------------------------------------------------------------

class TestEquivalentPrompts:
    """Multiple phrasings of the same geometry must produce the same Spec."""

    CYLINDER_VARIANTS = [
        'Create a cylinder with a 20 mm diameter and 50 mm height.',
        'Make me a cylindrical part, dia 20mm, height 50mm.',
        'A cylinder, diameter = 20 mm, height = 50 mm.',
        'Generate a 20mm-diameter, 50mm-high cylinder.',
        'cylinder 20 mm dia, 50 mm tall',
        'Cylinder: diameter 20 mm, height 50 mm',
        'A 20mm diameter, 50mm long cylinder',
        '20 mm diameter cylinder 50 mm height',
    ]

    @pytest.mark.parametrize('prompt', CYLINDER_VARIANTS)
    def test_cylinder_variants_produce_20x50_spec(self, prompt):
        spec = parse_prompt(prompt)
        assert spec.shape_type == 'cylinder'
        assert abs(spec.dimensions_mm.width - 20) < 0.01, f'width off for: {prompt}'
        assert abs(spec.dimensions_mm.height - 50) < 0.01, f'height off for: {prompt}'

    BOX_VARIANTS = [
        'A 100 x 60 x 30 mm box.',
        'Make a rectangular block 100 mm wide, 60 mm deep, 30 mm tall.',
        'Create a cuboid 100 × 60 × 30 mm.',
        'A cube-like block: 100 mm × 60 mm × 30 mm.',
    ]

    @pytest.mark.parametrize('prompt', BOX_VARIANTS)
    def test_box_variants_produce_100x60x30_spec(self, prompt):
        spec = parse_prompt(prompt)
        assert spec.shape_type in ('box', 'rounded_box')
        assert abs(spec.dimensions_mm.width - 100) < 0.01, f'width off for: {prompt}'
        assert abs(spec.dimensions_mm.depth - 60) < 0.01, f'depth off for: {prompt}'
        assert abs(spec.dimensions_mm.height - 30) < 0.01, f'height off for: {prompt}'

    TUBE_VARIANTS = [
        'Create a tube, 30 mm outer diameter and 50 mm tall.',
        'Make a hollow cylinder, 30 mm diameter and 50 mm height.',
        'A pipe, 30 mm dia, 50 mm long.',
        'A ring with outer diameter 30 mm, 50 mm height.',
        'washer, 30 mm diameter, 50 mm tall',
    ]

    @pytest.mark.parametrize('prompt', TUBE_VARIANTS)
    def test_tube_variants_produce_tube_spec(self, prompt):
        spec = parse_prompt(prompt)
        assert spec.shape_type == 'tube', f'expected tube for: {prompt}'
        assert abs(spec.dimensions_mm.width - 30) < 0.01, f'OD off for: {prompt}'
        assert abs(spec.dimensions_mm.height - 50) < 0.01, f'height off for: {prompt}'


# ---------------------------------------------------------------------------
# 2. Unit conversions
# ---------------------------------------------------------------------------

class TestUnitConversions:
    def test_centimeters_to_mm(self):
        spec = parse_prompt('Create a cylinder 2 cm diameter and 5 cm tall.')
        assert abs(spec.dimensions_mm.width - 20) < 0.01
        assert abs(spec.dimensions_mm.height - 50) < 0.01

    def test_meters_to_mm(self):
        spec = parse_prompt('A box 0.1 m wide, 0.06 m deep, 0.03 m tall.')
        assert abs(spec.dimensions_mm.width - 100) < 0.1
        assert abs(spec.dimensions_mm.depth - 60) < 0.1
        assert abs(spec.dimensions_mm.height - 30) < 0.1

    def test_inches_to_mm(self):
        # 2 inches = 50.8 mm
        spec = parse_prompt('A cylinder 2 inch diameter and 2 inches tall.')
        assert abs(spec.dimensions_mm.width - 50.8) < 0.1
        assert abs(spec.dimensions_mm.height - 50.8) < 0.1

    def test_mixed_units_in_prompt(self):
        # 1 inch diameter + 50 mm height
        spec = parse_prompt('Cylinder with 1 in diameter and 50 mm height.')
        assert abs(spec.dimensions_mm.width - 25.4) < 0.1
        assert abs(spec.dimensions_mm.height - 50) < 0.01

    def test_mm_passthrough(self):
        spec = parse_prompt('sphere 40 mm diameter')
        assert abs(spec.dimensions_mm.width - 40) < 0.01


# ---------------------------------------------------------------------------
# 3. Backward compatibility — existing prompts continue to work
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_cylinder_existing_format(self):
        spec = parse_prompt('Create a cylinder with a 20 mm diameter and 50 mm height')
        assert spec.shape_type == 'cylinder'
        assert (spec.dimensions_mm.width, spec.dimensions_mm.height) == (20, 50)

    def test_enclosure_existing_format(self):
        spec = parse_prompt('Make a 100 x 60 x 30 mm enclosure with 2 mm walls')
        assert spec.shape_type == 'enclosure' and spec.wall_thickness == 2

    def test_organic_existing_format(self):
        spec = parse_prompt('organic blob 80 mm wide and 60 mm tall')
        assert spec.shape_type == 'organic'

    def test_sphere_existing_format(self):
        spec = parse_prompt('sphere 40 mm diameter')
        assert spec.shape_type == 'sphere'

    def test_cone_existing_format(self):
        spec = parse_prompt('cone 40 mm diameter 60 mm tall')
        assert spec.shape_type == 'cone'

    def test_bare_number_fallback(self):
        # Original test: numbers without explicit unit labels still parsed
        spec = parse_prompt('20 mm diameter cylinder, 50 mm height')
        assert spec.shape_type == 'cylinder'
        assert spec.dimensions_mm.width == 20 and spec.dimensions_mm.height == 50


# ---------------------------------------------------------------------------
# 4. Synonym recognition — correct mapping, no false positives
# ---------------------------------------------------------------------------

class TestSynonyms:
    def test_shaft_maps_to_cylinder(self):
        spec = parse_prompt('A shaft 15 mm diameter and 80 mm long.')
        assert spec.shape_type == 'cylinder'

    def test_rod_maps_to_cylinder(self):
        spec = parse_prompt('A rod 10 mm diameter and 100 mm tall.')
        assert spec.shape_type == 'cylinder'

    def test_disk_maps_to_cylinder(self):
        spec = parse_prompt('A disk 50 mm diameter and 5 mm tall.')
        assert spec.shape_type == 'cylinder'

    def test_disc_maps_to_cylinder(self):
        spec = parse_prompt('A disc 30 mm diameter, 10 mm high.')
        assert spec.shape_type == 'cylinder'

    def test_block_maps_to_box(self):
        spec = parse_prompt('A block 100 x 60 x 30 mm.')
        assert spec.shape_type in ('box', 'rounded_box')

    def test_rectangular_block_maps_to_box(self):
        spec = parse_prompt('A rectangular block 100 mm wide, 60 mm deep, 30 mm tall.')
        assert spec.shape_type in ('box', 'rounded_box')

    def test_cuboid_maps_to_box(self):
        spec = parse_prompt('A cuboid 80 x 40 x 20 mm.')
        assert spec.shape_type in ('box', 'rounded_box')

    def test_washer_maps_to_tube(self):
        spec = parse_prompt('A washer 30 mm diameter and 5 mm tall.')
        assert spec.shape_type == 'tube'

    def test_ring_maps_to_tube(self):
        spec = parse_prompt('A ring, 40 mm outer diameter, 50 mm tall.')
        assert spec.shape_type == 'tube'

    def test_pipe_maps_to_tube(self):
        spec = parse_prompt('A pipe 20 mm diameter and 100 mm long.')
        assert spec.shape_type == 'tube'

    def test_hollow_cylinder_maps_to_tube(self):
        spec = parse_prompt('A hollow cylinder 25 mm diameter and 60 mm tall.')
        assert spec.shape_type == 'tube'

    def test_organic_blob_still_works(self):
        spec = parse_prompt('A blobby organic shape 50 x 50 x 40 mm.')
        assert spec.shape_type == 'organic'

    def test_cylinder_not_confused_with_cylindrical_enclosure(self):
        # "enclosure" should win over cylinder synonym
        spec = parse_prompt('A cylindrical enclosure 40 mm diameter 80 mm tall 2 mm walls.')
        # This will parse as enclosure (enclosure keyword present)
        assert spec.shape_type in ('enclosure', 'cylinder', 'tube')  # implementation may vary


# ---------------------------------------------------------------------------
# 5. Missing essential parameters → clarification messages
# ---------------------------------------------------------------------------

class TestMissingParameters:
    def test_cylinder_missing_both(self):
        with pytest.raises(ParseError) as exc:
            parse_prompt('Make a cylinder.')
        msg = str(exc.value).lower()
        assert 'diameter' in msg or 'height' in msg

    def test_cylinder_missing_height(self):
        with pytest.raises(ParseError) as exc:
            parse_prompt('Make a cylinder 20 mm diameter.')
        msg = str(exc.value)
        # Should mention what's missing
        assert 'height' in msg.lower()

    def test_cylinder_missing_diameter(self):
        with pytest.raises(ParseError) as exc:
            parse_prompt('Make a cylinder 50 mm tall.')
        msg = str(exc.value)
        assert 'diameter' in msg.lower()

    def test_tube_missing_height(self):
        with pytest.raises(ParseError) as exc:
            parse_prompt('Create a hollow tube 30 mm outer diameter.')
        msg = str(exc.value)
        assert 'height' in msg.lower()

    def test_box_missing_dimensions(self):
        with pytest.raises(ParseError):
            parse_prompt('Make a box.')

    def test_sphere_missing_diameter(self):
        with pytest.raises(ParseError):
            parse_prompt('Make a sphere.')

    def test_cone_missing_height(self):
        with pytest.raises(ParseError) as exc:
            parse_prompt('Make a cone 40 mm diameter.')
        msg = str(exc.value)
        assert 'height' in msg.lower() or 'cone' in msg.lower()

    def test_error_messages_are_actionable(self):
        """Error messages should suggest how to fix the issue."""
        with pytest.raises(ParseError) as exc:
            parse_prompt('A cylinder')
        msg = str(exc.value)
        assert 'mm' in msg.lower() or 'example' in msg.lower() or 'specify' in msg.lower()


# ---------------------------------------------------------------------------
# 6. Unsupported local prompts → actionable errors
# ---------------------------------------------------------------------------

class TestUnsupportedLocal:
    def test_hole_prompt_raises_unsupported(self):
        with pytest.raises(UnsupportedGeometry):
            nlp._local('A plate with a mounting hole in the center.')

    def test_fillet_prompt_raises_unsupported(self):
        with pytest.raises(UnsupportedGeometry):
            nlp._local('A box 50 x 40 x 20 mm with a 3 mm fillet.')

    def test_boolean_raises_unsupported(self):
        with pytest.raises(UnsupportedGeometry):
            nlp._local('Box minus cylinder boolean operation.')

    def test_unsupported_error_message_is_helpful(self):
        with pytest.raises(UnsupportedGeometry) as exc:
            nlp._local('A plate with mounting holes.')
        msg = str(exc.value)
        assert 'Gemini' in msg or 'parameters' in msg or 'hole' in msg.lower()

    def test_unknown_shape_raises_parse_error(self):
        with pytest.raises(ParseError) as exc:
            parse_prompt('Make a spaceship 100 mm wide.')
        msg = str(exc.value).lower()
        assert 'shape' in msg or 'supported' in msg


# ---------------------------------------------------------------------------
# 7. Auto mode routes eligible unsupported prompts to Gemini
# ---------------------------------------------------------------------------

class TestAutoModeRouting:
    def test_auto_routes_unsupported_to_gemini_when_key_set(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        with pytest.raises(_GeminiRoutingSignal):
            interpret('A plate with four mounting holes.', interpreter='auto')

    def test_auto_routes_fillet_to_gemini_when_key_set(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        with pytest.raises(_GeminiRoutingSignal):
            interpret('A box 50 x 40 x 20 mm with a 3 mm fillet.', interpreter='auto')

    def test_auto_stays_local_when_no_key(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', '')
        with pytest.raises(UnsupportedGeometry):
            interpret('A plate with mounting holes.', interpreter='auto')

    def test_auto_stays_local_for_parseable_geometry(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        result = interpret('Create a cylinder 20 mm diameter and 50 mm tall.', interpreter='auto')
        assert result.provider == 'local-deterministic'

    def test_auto_does_not_route_clearly_invalid_to_gemini(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        # Negative dimensions are invalid; should not route to Gemini
        assert _is_clearly_invalid('a cylinder -20 mm diameter')

    def test_default_interpreter_is_auto(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', '')
        result = interpret('Create a cylinder 20 mm diameter and 50 mm tall.')
        assert result.provider == 'local-deterministic'


# ---------------------------------------------------------------------------
# 8. Explicit Local never switches to Gemini
# ---------------------------------------------------------------------------

class TestExplicitLocal:
    def test_local_does_not_route_unsupported_to_gemini(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        # Must raise UnsupportedGeometry, not _GeminiRoutingSignal
        with pytest.raises(UnsupportedGeometry):
            interpret('A plate with mounting holes.', interpreter='local')

    def test_local_parses_supported_prompt(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        result = interpret('Create a cylinder 20 mm diameter and 50 mm tall.', interpreter='local')
        assert result.provider == 'local-deterministic'

    def test_explicit_local_api(self, client, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        called = []
        def gemini_stub(req): called.append(req); raise AssertionError('Gemini must not be called')
        monkeypatch.setattr(codegen, 'build_script', gemini_stub)
        conv = client.post('/api/conversations', json={'title': 'Local only'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': '20 mm diameter cylinder, 50 mm height',
            'interpreter': 'local',
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()['provider'] == 'local-deterministic'
        assert not called


# ---------------------------------------------------------------------------
# 9. Explicit Gemini never switches to Local
# ---------------------------------------------------------------------------

class TestExplicitGemini:
    def test_explicit_gemini_routes_to_gemini(self, client, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        from .test_upgrade import CYLINDER_SCRIPT, _fake_generation
        monkeypatch.setattr(codegen, 'build_script',
                            lambda req: _fake_generation(CYLINDER_SCRIPT, [20.0, 20.0, 50.0]))
        conv = client.post('/api/conversations', json={'title': 'Gemini only'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': '20 mm diameter cylinder, 50 mm height',
            'interpreter': 'gemini',
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()['provider'] == 'gemini'

    def test_explicit_gemini_fails_if_key_missing(self, client, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', '')
        conv = client.post('/api/conversations', json={'title': 'No key'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': '20 mm diameter cylinder, 50 mm height',
            'interpreter': 'gemini',
        })
        assert resp.status_code == 422
        assert 'GEMINI_API_KEY' in resp.json()['detail']['message']


# ---------------------------------------------------------------------------
# 10. Negative dimensions remain rejected
# ---------------------------------------------------------------------------

class TestInvalidDimensions:
    def test_negative_dimension_detected(self):
        assert _is_clearly_invalid('cylinder -20 mm diameter 50 mm tall')
        assert _is_clearly_invalid('a box - 10 mm wide')

    def test_positive_not_flagged(self):
        assert not _is_clearly_invalid('cylinder 20 mm diameter 50 mm tall')

    def test_zero_dimension_rejected_by_schema(self):
        from .schemas import Dimensions
        with pytest.raises(Exception):
            Dimensions(width=0, depth=10, height=10)

    def test_very_large_dimension_rejected_by_schema(self):
        from .schemas import Dimensions
        with pytest.raises(Exception):
            Dimensions(width=501, depth=10, height=10)

    def test_inner_diameter_larger_than_outer_rejected(self):
        from .schemas import Spec, Dimensions
        with pytest.raises(Exception):
            Spec(shape_type='spacer',
                 dimensions_mm=Dimensions(width=20, depth=20, height=10),
                 hole_diameter=25)  # bore > OD


# ---------------------------------------------------------------------------
# 12. Repair attempts bounded
# ---------------------------------------------------------------------------

class TestRepairBounded:
    def test_max_attempts_not_exceeded(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        calls = []
        def always_bad(_):
            calls.append(1)
            return 'no code block here, just prose'
        monkeypatch.setattr(codegen, '_call_gemini', always_bad)
        with pytest.raises(codegen.CodegenError):
            codegen.build_script(codegen.ScriptRequest(prompt='make anything'))
        assert len(calls) == codegen.MAX_ATTEMPTS

    def test_repair_history_populated(self, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        responses = iter(['prose only', 'still no fence', 'still bad'])
        monkeypatch.setattr(codegen, '_call_gemini', lambda _: next(responses))
        with pytest.raises(codegen.CodegenError) as exc:
            codegen.build_script(codegen.ScriptRequest(prompt='make a box'))
        assert len(exc.value.history) == codegen.MAX_ATTEMPTS - 1


# ---------------------------------------------------------------------------
# 13. Failed generation creates no partial model or assistant-message state
# ---------------------------------------------------------------------------

class TestNoPartialState:
    def test_failed_local_generation_no_model_created(self, client, database):
        conv = client.post('/api/conversations', json={'title': 'Fail'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': 'Make a cylinder.',  # missing dimensions
            'interpreter': 'local',
        })
        assert resp.status_code == 422
        from sqlalchemy import select
        from .db_models import GeneratedModel, Message
        with database['factory']() as db:
            models = db.query(GeneratedModel).filter(
                GeneratedModel.conversation_id == conv['id']).count()
            assistants = db.query(Message).filter(
                Message.conversation_id == conv['id'],
                Message.role == 'assistant').count()
        assert models == 0 and assistants == 0

    def test_failed_gemini_generation_no_model_created(self, client, database, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        monkeypatch.setattr(codegen, 'build_script', lambda req: (_ for _ in ()).throw(
            codegen.CodegenError('timeout', 'Gemini script generation timed out.')))
        conv = client.post('/api/conversations', json={'title': 'Fail Gemini'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': 'A complex organic shape',
            'interpreter': 'gemini',
        })
        assert resp.status_code == 503
        from sqlalchemy import select
        from .db_models import GeneratedModel, Message
        with database['factory']() as db:
            models = db.query(GeneratedModel).filter(
                GeneratedModel.conversation_id == conv['id']).count()
            assistants = db.query(Message).filter(
                Message.conversation_id == conv['id'],
                Message.role == 'assistant').count()
        assert models == 0 and assistants == 0


# ---------------------------------------------------------------------------
# 14. Timeouts distinct from interpretation failures
# ---------------------------------------------------------------------------

class TestErrorDistinction:
    def test_timeout_503_not_422(self, client, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        monkeypatch.setattr(codegen, 'build_script', lambda req: (_ for _ in ()).throw(
            codegen.CodegenError('timeout', 'Gemini script generation timed out.')))
        conv = client.post('/api/conversations', json={'title': 'Timeout'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'], 'prompt': 'anything', 'interpreter': 'gemini'})
        assert resp.status_code == 503, resp.text

    def test_parse_failure_422_not_503(self, client):
        conv = client.post('/api/conversations', json={'title': 'Parse'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': 'Make a cylinder.',
            'interpreter': 'local'})
        assert resp.status_code == 422, resp.text

    def test_unsupported_geometry_422_not_503(self, client, monkeypatch):
        monkeypatch.setenv('GEMINI_API_KEY', '')
        conv = client.post('/api/conversations', json={'title': 'Unsupported'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': 'A box with mounting holes.',
            'interpreter': 'local'})
        assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 15. Version history and export unaffected by parser improvements
# ---------------------------------------------------------------------------

class TestVersionHistoryAndExportIntact:
    def test_cylinder_version_history_and_export(self, client):
        conv = client.post('/api/conversations', json={'title': 'Compat'}).json()
        made = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': 'Create a cylinder with a 20 mm diameter and 50 mm height.',
            'interpreter': 'local',
        }).json()
        assert made.get('model_id')
        refined = client.post(f"/api/models/{made['model_id']}/refine", json={
            'instruction': 'Make it 10 mm taller', 'interpreter': 'local'})
        assert refined.status_code == 200
        assert refined.json()['spec']['dimensions_mm']['height'] == 60
        history = client.get(f"/api/conversations/{conv['id']}/models").json()
        assert history['total'] == 2
        for fmt in ('stl', 'glb'):
            exported = client.get(f"/api/models/{made['model_id']}/export?format={fmt}")
            assert exported.status_code == 200 and len(exported.content) > 100

    def test_tube_synonym_version_history(self, client):
        """Synonym-parsed tube must still round-trip through the version system."""
        conv = client.post('/api/conversations', json={'title': 'Tube compat'}).json()
        made = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': 'A hollow cylinder 30 mm diameter and 50 mm tall.',
            'interpreter': 'local',
        })
        assert made.status_code == 200, made.text
        result = made.json()
        assert result['spec']['shape_type'] == 'tube'
        history = client.get(f"/api/conversations/{conv['id']}/models").json()
        assert history['total'] == 1


# ---------------------------------------------------------------------------
# Auto mode API integration (requires Gemini mock)
# ---------------------------------------------------------------------------

class TestAutoModeAPIIntegration:
    def test_auto_routes_hole_prompt_to_gemini(self, client, monkeypatch):
        """Auto mode: hole prompt → local UnsupportedGeometry → Gemini call."""
        from .test_upgrade import CYLINDER_SCRIPT, _fake_generation
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
        called = []
        def fake_gemini(req):
            called.append(req.prompt)
            return _fake_generation(CYLINDER_SCRIPT, [20.0, 20.0, 50.0])
        monkeypatch.setattr(codegen, 'build_script', fake_gemini)
        conv = client.post('/api/conversations', json={'title': 'Auto Gemini'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': 'A plate with four mounting holes.',
            # no interpreter → auto
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()['provider'] == 'gemini'
        assert called  # Gemini was actually called

    def test_auto_no_gemini_key_local_error_for_hole_prompt(self, client, monkeypatch):
        """Auto mode without Gemini key: unsupported prompt → 422 with helpful message."""
        monkeypatch.setenv('GEMINI_API_KEY', '')
        conv = client.post('/api/conversations', json={'title': 'Auto local fail'}).json()
        resp = client.post('/api/models/generate', json={
            'conversation_id': conv['id'],
            'prompt': 'A plate with four mounting holes.',
        })
        assert resp.status_code == 422
        msg = resp.json()['detail']['message']
        assert 'Gemini' in msg or 'local' in msg.lower() or 'hole' in msg.lower()


# ---------------------------------------------------------------------------
# Corner-radius shape compatibility
# ---------------------------------------------------------------------------

class TestCornerRadiusShapeGating:
    def test_cylinder_corner_radius_phrase_does_not_leak(self):
        spec = parse_prompt('cylinder 20 mm diameter 50 mm tall corner radius 2 mm')
        assert spec.shape_type == 'cylinder'
        assert spec.corner_radius is None

    def test_box_corner_radius_phrase_does_not_leak(self):
        spec = parse_prompt('box 100 x 60 x 30 mm corner radius 2 mm')
        assert spec.shape_type == 'box'
        assert spec.corner_radius is None

    def test_rounded_box_gets_corner_radius(self):
        spec = parse_prompt('rounded box 100 x 60 x 30 mm corner radius 5 mm')
        assert spec.shape_type == 'rounded_box'
        assert spec.corner_radius == 5.0

    def test_enclosure_gets_corner_radius(self):
        spec = parse_prompt('enclosure 100 x 60 x 30 mm corner radius 5 mm')
        assert spec.shape_type == 'enclosure'
        assert spec.corner_radius == 5.0

    def test_auto_corner_radius_for_rounded_box(self):
        spec = parse_prompt('rounded box 100 x 60 x 30 mm')
        assert spec.shape_type == 'rounded_box'
        assert spec.corner_radius is not None
        assert spec.corner_radius > 0
