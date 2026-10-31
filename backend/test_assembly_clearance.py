import cadquery as cq
import pytest

from .cad_geometry import check_component_pairs, generate_cad, validate_from_cad_shape
from .schemas import CADComponent, Dimensions, Spec


def comp(name, x, y, z, w=10.0, d=10.0, h=10.0):
    return CADComponent(
        name=name,
        shape_type='box',
        dimensions_mm=Dimensions(width=w, depth=d, height=h),
        position_mm=(x, y, z),
    )


def make_compound(specs):
    """Build a compound from (workplane, component) pairs."""
    solids = []
    for wp, _ in specs:
        solids.append(wp.val())
    return cq.Compound.makeCompound(solids)


def pairs(cad_shape, components, clearance=None):
    return check_component_pairs(cad_shape, components, requested_clearance_mm=clearance)


def test_clearance_separated_components():
    a = cq.Workplane('XY').box(10, 10, 10).translate((-15, 0, 0))  # x in [-20,-10]
    b = cq.Workplane('XY').box(10, 10, 10).translate((15, 0, 0))    # x in [10,20]
    result = pairs(make_compound([(a, None), (b, None)]), [comp('a', -15, 0, 0), comp('b', 15, 0, 0)])
    assert len(result) == 1
    r = result[0]
    assert r['status'] == 'pass'
    assert r['interference'] is False
    assert r['min_distance_mm'] == pytest.approx(20.0, abs=1e-6)
    assert r['reason'] is None


def test_clearance_contacting_components():
    a = cq.Workplane('XY').box(10, 10, 10).translate((-5, 0, 0))   # x in [-10, 0]
    b = cq.Workplane('XY').box(10, 10, 10).translate((5, 0, 0))     # x in [0, 10]
    result = pairs(make_compound([(a, None), (b, None)]), [comp('a', -5, 0, 0), comp('b', 5, 0, 0)])
    assert len(result) == 1
    r = result[0]
    assert r['status'] == 'warning'
    assert r['interference'] is False
    assert r['min_distance_mm'] == pytest.approx(0.0, abs=1e-6)
    assert 'contact' in (r['reason'] or '')


def test_clearance_contacting_with_clearance_requested_fails():
    a = cq.Workplane('XY').box(10, 10, 10).translate((-5, 0, 0))
    b = cq.Workplane('XY').box(10, 10, 10).translate((5, 0, 0))
    result = pairs(make_compound([(a, None), (b, None)]), [comp('a', -5, 0, 0), comp('b', 5, 0, 0)], clearance=2.0)
    assert result[0]['status'] == 'fail'
    assert result[0]['interference'] is False
    assert result[0]['min_distance_mm'] == pytest.approx(0.0, abs=1e-6)


def test_clearance_intersecting_components():
    a = cq.Workplane('XY').box(10, 10, 10).translate((-2, 0, 0))   # x in [-7, 3]
    b = cq.Workplane('XY').box(10, 10, 10).translate((2, 0, 0))     # x in [-3, 7]
    result = pairs(make_compound([(a, None), (b, None)]), [comp('a', -2, 0, 0), comp('b', 2, 0, 0)])
    assert len(result) == 1
    r = result[0]
    assert r['status'] == 'fail'
    assert r['interference'] is True
    assert r['min_distance_mm'] == pytest.approx(0.0, abs=1e-6)
    assert 'intersect' in (r['reason'] or '')


def test_clearance_met():
    a = cq.Workplane('XY').box(10, 10, 10).translate((-15, 0, 0))
    b = cq.Workplane('XY').box(10, 10, 10).translate((15, 0, 0))   # gap 20 mm
    result = pairs(make_compound([(a, None), (b, None)]), [comp('a', -15, 0, 0), comp('b', 15, 0, 0)], clearance=20.0)
    assert result[0]['status'] == 'pass'
    assert result[0]['requested_clearance_mm'] == 20.0


def test_clearance_violated():
    a = cq.Workplane('XY').box(10, 10, 10).translate((-15, 0, 0))
    b = cq.Workplane('XY').box(10, 10, 10).translate((15, 0, 0))   # gap 20 mm
    result = pairs(make_compound([(a, None), (b, None)]), [comp('a', -15, 0, 0), comp('b', 15, 0, 0)], clearance=25.0)
    assert result[0]['status'] == 'fail'
    assert result[0]['interference'] is False
    assert 'less than requested clearance' in (result[0]['reason'] or '')


def test_clearance_single_component_returns_no_pairs():
    cad = cq.Workplane('XY').box(10, 10, 10).val()
    assert pairs(cad, [comp('a', 0, 0, 0)]) == []
    assert pairs(cad, []) == []


def test_clearance_not_checked_when_solids_cannot_be_enumerated():
    class _FailingShape:
        def Solids(self):
            raise RuntimeError('simulated kernel failure')

    result = pairs(_FailingShape(), [comp('a', 0, 0, 0), comp('b', 0, 0, 0)])
    assert len(result) == 1
    assert result[0]['status'] == 'not_checked'
    assert result[0]['min_distance_mm'] is None
    assert result[0]['interference'] is False
    assert 'could not enumerate compound solids' in (result[0]['reason'] or '')


def test_compound_assembly_validation_stores_component_pairs():
    parts = [
        comp('left', -5, 0, 0),
        comp('right', 5, 0, 0),
    ]
    spec = Spec(
        shape_type='assembly',
        dimensions_mm=Dimensions(width=20, depth=10, height=10),
        components=parts,
        assembly_mode='compound',
    )
    _, _, validation = generate_cad(spec)
    pairs = validation.get('component_pairs')
    assert isinstance(pairs, list)
    assert len(pairs) == 1
    r = pairs[0]
    assert r['component_a_name'] == 'left'
    assert r['component_b_name'] == 'right'
    assert r['status'] in ('pass', 'fail', 'warning', 'not_checked')
    assert 'min_distance_mm' in r
    assert 'interference' in r
    assert 'requested_clearance_mm' in r
    assert 'reason' in r


def test_single_object_validation_has_no_component_pairs():
    _, _, validation = generate_cad(Spec(shape_type='box', dimensions_mm=Dimensions(width=20, depth=20, height=20)))
    assert validation.get('component_pairs') in (None, [])


def test_union_assembly_validation_has_no_component_pairs():
    parts = [
        comp('left', -5, 0, 0),
        comp('right', 5, 0, 0),
    ]
    spec = Spec(
        shape_type='assembly',
        dimensions_mm=Dimensions(width=20, depth=10, height=10),
        components=parts,
        assembly_mode='union',
    )
    _, _, validation = generate_cad(spec)
    assert validation.get('component_pairs') in (None, [])
