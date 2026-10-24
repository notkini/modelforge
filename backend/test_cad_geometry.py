import math
import pytest
import trimesh

import cadquery
from .cad_geometry import CAD_TOLERANCE_MM, generate_cad
from .schemas import CADComponent, CADHole, Dimensions, Spec


def spec(shape,dimensions,**kwargs):
    return Spec(shape_type=shape,dimensions_mm=Dimensions(width=dimensions[0],depth=dimensions[1],height=dimensions[2]),**kwargs)


@pytest.mark.parametrize(('shape','dims'),[
    ('box',(40,30,20)),('rounded_box',(40,30,20)),('cylinder',(20,20,50)),
    ('sphere',(24,24,24)),('cone',(20,20,30)),('tube',(20,20,30)),
    ('mounting_plate',(40,30,5)),('bracket',(40,30,20)),('spacer',(20,20,12)),
    ('bushing',(20,20,12)),('adapter',(30,30,25)),
])
def test_cad_primitive_bounds_and_valid_mesh(shape,dims):
    args={'corner_radius':3} if shape=='rounded_box' else {}
    if shape in ('spacer','bushing'): args['hole_diameter']=8
    if shape=='adapter': args={'hole_diameter':8,'step_diameter':20,'step_height':10}
    if shape=='tube': args={'wall_thickness':2}
    cad,mesh,validation=generate_cad(spec(shape,dims,**args))
    assert cad.isValid() and cad.Solids()
    assert validation['cad_solid_valid'] and validation['dimensions_within_tolerance']
    assert validation['dimension_tolerance_mm']==CAD_TOLERANCE_MM
    assert validation['dimensions_mm']==pytest.approx(dims,abs=CAD_TOLERANCE_MM)
    assert isinstance(mesh,trimesh.Trimesh) and mesh.is_watertight and mesh.is_winding_consistent


def test_enclosure_wall_floor_cavity_volume_and_closed_lid():
    dims=(100,60,30); t=2; floor=3
    open_spec=spec('enclosure',dims,wall_thickness=t,floor_thickness=floor,enclosure_open=True)
    open_cad,_,open_validation=generate_cad(open_spec)
    expected=100*60*30-(100-2*t)*(60-2*t)*(30-floor)
    assert open_cad.isValid() and open_cad.Volume()==pytest.approx(expected,rel=1e-5)
    assert open_validation['dimensions_within_tolerance']
    closed,_,closed_validation=generate_cad(spec('enclosure',dims,wall_thickness=t,floor_thickness=floor,enclosure_open=False))
    closed_expected=100*60*30-(100-2*t)*(60-2*t)*(30-floor-t)
    assert closed.Volume()==pytest.approx(closed_expected,rel=1e-5)
    with_lid,_,lid_validation=generate_cad(spec('enclosure',dims,wall_thickness=t,floor_thickness=floor,lid=True))
    assert lid_validation['dimensions_within_tolerance'] and len(with_lid.Solids())==2


def test_hole_subtraction_removes_material_and_reduces_volume():
    plain=spec('mounting_plate',(40,30,5),thickness=5)
    drilled=spec('mounting_plate',(40,30,5),thickness=5,holes=[CADHole(position_mm=(12,8,0),diameter=6)])
    solid,_,_=generate_cad(plain); cut,mesh,validation=generate_cad(drilled)
    assert cut.Volume()<solid.Volume()
    assert not cut.isInside(cadquery.Vector(12,8,0),1e-4)
    assert mesh.is_watertight and validation['cad_solid_valid']


@pytest.mark.parametrize(('operation','components','expected'),[
    ('union',[
        CADComponent(shape_type='box',dimensions_mm=Dimensions(width=10,depth=10,height=10),position_mm=(-2.5,0,0)),
        CADComponent(shape_type='box',dimensions_mm=Dimensions(width=10,depth=10,height=10),position_mm=(2.5,0,0)),
    ],(15,10,10)),
    ('intersection',[
        CADComponent(shape_type='box',dimensions_mm=Dimensions(width=10,depth=10,height=10),position_mm=(-2.5,0,0)),
        CADComponent(shape_type='box',dimensions_mm=Dimensions(width=10,depth=10,height=10),position_mm=(2.5,0,0)),
    ],(5,10,10)),
])
def test_boolean_union_and_intersection_use_kernel(operation,components,expected):
    cad,mesh,validation=generate_cad(spec('boolean',expected,operation=operation,components=components))
    assert cad.isValid() and cad.Solids() and validation['dimensions_within_tolerance']
    assert mesh.is_watertight


def test_boolean_difference_cuts_actual_cylindrical_volume():
    components=[
        CADComponent(shape_type='box',dimensions_mm=Dimensions(width=20,depth=20,height=20)),
        CADComponent(shape_type='cylinder',dimensions_mm=Dimensions(width=6,depth=6,height=22)),
    ]
    cad,mesh,validation=generate_cad(spec('boolean',(20,20,20),operation='difference',components=components))
    assert cad.Volume()==pytest.approx(20**3-math.pi*3**2*20,rel=0.02)
    assert not cad.isInside(cadquery.Vector(0,0,0),1e-4)
    assert mesh.is_watertight and validation['cad_solid_valid']


@pytest.mark.parametrize(('field','value'),[('fillet_radius',1.5),('chamfer_distance',1.5)])
def test_edge_features_are_real_and_keep_bounds(field,value):
    cad,_,validation=generate_cad(spec('box',(20,20,20),**{field:value}))
    assert cad.isValid() and validation['dimensions_within_tolerance']
    assert cad.Volume()<20**3


def test_invalid_fillet_and_bad_bore_are_rejected():
    with pytest.raises(ValueError): spec('box',(12,12,2),fillet_radius=1.1)
    with pytest.raises(ValueError): spec('spacer',(20,20,10),hole_diameter=20)
    with pytest.raises(ValueError): spec('organic',(30,30,30),fillet_radius=1)


def test_kernel_rejects_incompatible_sequential_fillet_and_chamfer():
    part=spec('box',(10,10,10),fillet_radius=4.9,chamfer_distance=4.9)
    with pytest.raises(ValueError,match='could not apply'):
        generate_cad(part)


def test_profile_extrusion_and_loft():
    extrusion=spec('extruded_profile',(20,20,12),profile_points_mm=[(-10,-10),(10,-10),(10,10),(-10,10)])
    _,mesh,check=generate_cad(extrusion)
    assert mesh.is_watertight and check['dimensions_within_tolerance']
    loft=spec('loft',(20,20,20),loft_sections=[
        {'z_mm':-10,'points_mm':[(-10,-10),(10,-10),(10,10),(-10,10)]},
        {'z_mm':10,'points_mm':[(-5,-5),(5,-5),(5,5),(-5,5)]},
    ])
    cad,mesh,check=generate_cad(loft)
    assert cad.isValid() and mesh.is_watertight and check['dimensions_within_tolerance']


def test_general_assembly_builds_dimensioned_table_from_reusable_parts():
    parts=[CADComponent(name='tabletop',shape_type='box',dimensions_mm=Dimensions(width=40,depth=30,height=3),position_mm=(0,0,18.5))]
    for x in (-18.5,18.5):
        for y in (-13.5,13.5):
            parts.append(CADComponent(name='leg',shape_type='box',dimensions_mm=Dimensions(width=3,depth=3,height=37),position_mm=(x,y,-1.5)))
    cad,mesh,check=generate_cad(spec('assembly',(40,30,40),components=parts,object_identity='miniature table',intended_use='dollhouse furniture',assembly_mode='union'))
    assert cad.isValid() and len(cad.Solids())==1
    assert mesh.is_watertight and mesh.is_winding_consistent
    assert check['dimensions_mm']==pytest.approx((40,30,40),abs=CAD_TOLERANCE_MM)
    assert check['dimensions_within_tolerance']


def test_general_assembly_builds_recognizable_toy_car_components():
    parts=[CADComponent(name='body',shape_type='box',dimensions_mm=Dimensions(width=40,depth=18,height=10),position_mm=(0,0,1))]
    for x in (-13,13):
        for y in (-8,8):
            parts.append(CADComponent(name='wheel',shape_type='cylinder',dimensions_mm=Dimensions(width=8,depth=8,height=4),position_mm=(x,y,-2),rotation_deg=(90,0,0)))
    cad,mesh,check=generate_cad(spec('assembly',(40,20,12),components=parts,object_identity='toy car',assembly_mode='compound'))
    assert cad.isValid() and len(cad.Solids())==5
    assert mesh.is_watertight and mesh.faces.shape[0]>0
    assert check['dimensions_mm']==pytest.approx((40,20,12),abs=CAD_TOLERANCE_MM)


def test_fused_everyday_object_assembly_is_watertight():
    parts=[
        CADComponent(name='base',shape_type='box',dimensions_mm=Dimensions(width=80,depth=80,height=10),position_mm=(0,0,-45)),
        CADComponent(name='back_support',shape_type='box',dimensions_mm=Dimensions(width=80,depth=10,height=90),position_mm=(0,35,5)),
        CADComponent(name='front_lip',shape_type='box',dimensions_mm=Dimensions(width=80,depth=15,height=15),position_mm=(0,-32.5,-32.5)),
    ]
    cad,mesh,check=generate_cad(spec('assembly',(80,80,100),components=parts,assembly_mode='union'))
    assert cad.isValid() and len(cad.Solids())==1
    assert mesh.is_watertight and mesh.is_winding_consistent
    assert check['dimensions_mm']==pytest.approx((80,80,100),abs=CAD_TOLERANCE_MM)


def test_assembly_fit_slides_overhanging_tilted_support_into_bounds():
    base=CADComponent(name='base',shape_type='box',dimensions_mm=Dimensions(width=80,depth=80,height=10),position_mm=(0,0,-45))
    support=CADComponent(name='back_support',shape_type='box',dimensions_mm=Dimensions(width=80,depth=10,height=95),position_mm=(0,34.267,3.232),rotation_deg=(-15,0,0))
    lip=CADComponent(name='retaining_lip',shape_type='box',dimensions_mm=Dimensions(width=80,depth=15,height=15),position_mm=(0,-32.5,-32.5))
    fitted=spec('assembly',(80,80,100),components=[base,support,lip])
    moved=fitted.components[1]
    assert (moved.dimensions_mm.width,moved.dimensions_mm.depth,moved.dimensions_mm.height)==(80,10,95)
    assert moved.rotation_deg==(-15,0,0)
    assert tuple(moved.position_mm)==pytest.approx((0.0,22.876,2.824),abs=0.01)
    assert fitted.components[0]==base and fitted.components[2]==lip
    cad,mesh,check=generate_cad(fitted)
    assert check['dimensions_within_tolerance'] and check['dimensions_mm']==pytest.approx((80,80,100),abs=CAD_TOLERANCE_MM)
    assert cad.isValid() and len(cad.Solids())==1
    assert mesh.is_watertight and mesh.is_winding_consistent


def test_assembly_fit_is_noop_for_exact_bounds():
    parts=[CADComponent(name='tabletop',shape_type='box',dimensions_mm=Dimensions(width=40,depth=30,height=3),position_mm=(0,0,18.5))]
    for x in (-18.5,18.5):
        for y in (-13.5,13.5):
            parts.append(CADComponent(name='leg',shape_type='box',dimensions_mm=Dimensions(width=3,depth=3,height=37),position_mm=(x,y,-1.5)))
    exact=spec('assembly',(40,30,40),components=parts)
    revalidated=Spec.model_validate(exact.model_dump())
    assert revalidated.model_dump()==exact.model_dump()


def test_undersized_assembly_is_scaled_to_requested_bounds():
    cube=CADComponent(name='cube',shape_type='box',dimensions_mm=Dimensions(width=40,depth=40,height=40),position_mm=(0,0,0))
    undersized=spec('assembly',(80,80,100),components=[cube])
    grown=undersized.components[0]
    assert (grown.dimensions_mm.width,grown.dimensions_mm.depth,grown.dimensions_mm.height)==(80,80,100)
    assert grown.position_mm==(0,0,0)
    cad,mesh,check=generate_cad(undersized)
    assert cad.isValid() and len(cad.Solids())==1
    assert mesh.is_watertight and check['dimensions_within_tolerance']


def test_assembly_repair_nudges_shortfall_without_rescaling():
    base=CADComponent(name='base',shape_type='box',dimensions_mm=Dimensions(width=60,depth=40,height=10),position_mm=(0,0,-15))
    post=CADComponent(name='post',shape_type='box',dimensions_mm=Dimensions(width=40,depth=40,height=30),position_mm=(0,0,4))
    fitted=spec('assembly',(60,40,40),components=[base,post])
    moved=fitted.components[1]
    assert (moved.dimensions_mm.width,moved.dimensions_mm.depth,moved.dimensions_mm.height)==(40,40,30)
    assert tuple(moved.position_mm)==pytest.approx((0.0,0.0,5.0),abs=0.01)
    cad,mesh,check=generate_cad(fitted)
    assert check['dimensions_within_tolerance'] and len(cad.Solids())==1
    assert mesh.is_watertight and mesh.is_winding_consistent


def test_assembly_repair_stretches_bigger_request_around_contacts():
    base=CADComponent(name='base',shape_type='box',dimensions_mm=Dimensions(width=80,depth=80,height=10),position_mm=(0,0,-45))
    support=CADComponent(name='back_support',shape_type='box',dimensions_mm=Dimensions(width=80,depth=15,height=90),position_mm=(0,21.78,0.3),rotation_deg=(-20,0,0))
    lip=CADComponent(name='retaining_lip',shape_type='box',dimensions_mm=Dimensions(width=80,depth=12,height=15),position_mm=(0,-30,-32.5))
    fitted=spec('assembly',(80,80,120),components=[base,support,lip])
    moved=fitted.components[1]
    assert moved.rotation_deg==(-20,0,0)
    assert moved.dimensions_mm.width==80
    assert moved.dimensions_mm.depth>15 and moved.dimensions_mm.height>110
    cad,mesh,check=generate_cad(fitted)
    assert cad.isValid() and len(cad.Solids())==1
    assert mesh.is_watertight and mesh.is_winding_consistent
    assert check['dimensions_mm']==pytest.approx((80,80,120),abs=CAD_TOLERANCE_MM)
    assert check['dimensions_within_tolerance']


def test_oversized_component_is_stretched_into_bounds():
    block=CADComponent(name='block',shape_type='box',dimensions_mm=Dimensions(width=90,depth=40,height=40),position_mm=(0,0,0))
    fitted=spec('assembly',(80,40,40),components=[block])
    shrunk=fitted.components[0]
    assert (shrunk.dimensions_mm.width,shrunk.dimensions_mm.depth,shrunk.dimensions_mm.height)==(80,40,40)
    assert shrunk.position_mm==(0,0,0)
    cad,mesh,check=generate_cad(fitted)
    assert cad.isValid() and check['dimensions_within_tolerance'] and mesh.is_watertight


def test_union_assembly_without_physical_contact_is_rejected():
    first=CADComponent(name='first',shape_type='box',dimensions_mm=Dimensions(width=10,depth=10,height=10),position_mm=(0,0,0))
    second=CADComponent(name='second',shape_type='box',dimensions_mm=Dimensions(width=10,depth=10,height=10),position_mm=(30,0,0))
    asked=spec('assembly',(40,10,10),components=[first,second],assembly_mode='union')
    with pytest.raises(ValueError,match='single connected solid'):
        generate_cad(asked)


def test_boolean_difference_cutter_is_never_repositioned():
    block=CADComponent(shape_type='box',dimensions_mm=Dimensions(width=20,depth=20,height=20),position_mm=(0,0,0))
    cutter=CADComponent(shape_type='cylinder',dimensions_mm=Dimensions(width=6,depth=6,height=22),position_mm=(0,0,0))
    boolean=spec('boolean',(20,20,20),operation='difference',components=[block,cutter])
    assert boolean.components[1].dimensions_mm.height==22 and boolean.components[1].position_mm==(0,0,0)
    cad,mesh,validation=generate_cad(boolean)
    assert validation['dimensions_within_tolerance'] and mesh.is_watertight
