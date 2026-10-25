"""Exact parametric geometry built with CadQuery and the OpenCASCADE kernel."""
from __future__ import annotations

import math
import os
from pathlib import Path
import numpy as np
import trimesh
from .schemas import CAD_TOLERANCE_MM, CADComponent, Spec

TESSELLATION_TOLERANCE_MM=0.12
ANGULAR_TOLERANCE_RAD=0.18
# Geometry complexity caps; env-overridable per run (values validated/clamped):
# MF_MAX_CAD_SOLIDS (1..1024), MF_MAX_MESH_VERTICES (12..4_000_000),
# MF_MAX_MESH_FACES (12..8_000_000). Defaults are far above anything the
# parametric builders legitimately produce.
DEFAULT_MAX_SOLID_COUNT=32
DEFAULT_MAX_MESH_VERTICES=400_000
DEFAULT_MAX_MESH_FACES=800_000

def _clamped_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value=int(float(os.environ.get(name,'')))
    except (TypeError,ValueError): return default
    return min(max(value,minimum),maximum)

class CadKernelUnavailable(RuntimeError): pass

def cadquery_module():
    try:
        import cadquery as cq
        return cq
    except ImportError as exc:
        raise CadKernelUnavailable('CadQuery/OpenCASCADE is required for mechanical CAD generation. Install the CAD requirements in a supported Python 3.11 or 3.12 environment; mesh fallback is intentionally disabled.') from exc

def cad_available() -> bool:
    try:
        cadquery_module(); return True
    except CadKernelUnavailable:
        return False

def _primitive(cq,kind:str,w:float,d:float,h:float):
    wp=cq.Workplane('XY')
    if kind=='box': return wp.box(w,d,h)
    if kind=='cylinder':
        if abs(w-d)>CAD_TOLERANCE_MM: raise ValueError('A cylinder needs equal width and depth (diameter).')
        return wp.cylinder(h,w/2)
    if kind=='sphere':
        if max(w,d,h)-min(w,d,h)>CAD_TOLERANCE_MM: raise ValueError('A sphere requires equal width, depth, and height dimensions.')
        return wp.sphere(w/2)
    if kind=='cone':
        if abs(w-d)>CAD_TOLERANCE_MM: raise ValueError('A cone needs equal width and depth (base diameter).')
        solid=cq.Solid.makeCone(w/2,0,h,cq.Vector(0,0,-h/2),cq.Vector(0,0,1))
        return wp.newObject([solid])
    raise ValueError(f'Unsupported Boolean component shape: {kind}.')

def _cut_holes(shape,cq,spec:Spec):
    bounds=shape.val().BoundingBox() if isinstance(shape,cq.Workplane) else shape.BoundingBox()
    mins={'x':bounds.xmin,'y':bounds.ymin,'z':bounds.zmin}
    extents={'x':bounds.xlen,'y':bounds.ylen,'z':bounds.zlen}
    for hole in spec.holes:
        x,y,z=hole.position_mm; radius=hole.diameter/2
        axis=hole.axis; coords={'x':x,'y':y,'z':z}
        direction=cq.Vector(*(1 if axis==key else 0 for key in ('x','y','z')))
        for cross_axis in (key for key in ('x','y','z') if key!=axis):
            if coords[cross_axis]-radius < mins[cross_axis]-CAD_TOLERANCE_MM or coords[cross_axis]+radius > mins[cross_axis]+extents[cross_axis]+CAD_TOLERANCE_MM:
                raise ValueError(f'The {hole.diameter:g} mm hole does not fit inside the part along the {cross_axis.upper()} axis.')
        before=float(shape.val().Volume() if isinstance(shape,cq.Workplane) else shape.Volume())
        if hole.depth:
            center=cq.Vector(x,y,z); length=hole.depth
            base=center-direction.multiply(length/2)
        else:
            coords[axis]=mins[axis]-1
            base=cq.Vector(coords['x'],coords['y'],coords['z']); length=extents[axis]+2
        cutter=cq.Solid.makeCylinder(radius,length,base,direction)
        shape=shape.cut(cutter)
        after=float(shape.val().Volume() if isinstance(shape,cq.Workplane) else shape.Volume())
        if not math.isfinite(after) or after>=before-1e-6:
            raise ValueError('The requested cylindrical hole did not intersect the solid; the hole was not created.')
    return shape

def _base_shape(cq,spec:Spec):
    w,d,h=spec.dimensions_mm.width,spec.dimensions_mm.depth,spec.dimensions_mm.height
    wp=cq.Workplane('XY'); s=spec.shape_type
    if s=='box': shape=wp.box(w,d,h)
    elif s=='rounded_box':
        radius=spec.corner_radius or min(w,d,h)*0.1
        shape=wp.box(w,d,h).edges().fillet(radius)
    elif s=='cylinder': shape=_primitive(cq,'cylinder',w,d,h)
    elif s=='sphere': shape=_primitive(cq,'sphere',w,d,h)
    elif s=='cone': shape=_primitive(cq,'cone',w,d,h)
    elif s=='tube':
        if abs(w-d)>CAD_TOLERANCE_MM: raise ValueError('A hollow tube needs equal width and depth (outer diameter).')
        wall=spec.wall_thickness or max(1,w*0.1)
        if wall>=w/2: raise ValueError('Tube wall thickness must be smaller than the outer radius.')
        shape=wp.circle(w/2).circle(w/2-wall).extrude(h)
    elif s=='enclosure':
        wall=spec.wall_thickness or 2.0; floor=spec.floor_thickness or wall
        if 2*wall>=min(w,d,h): raise ValueError('Enclosure wall thickness must be less than half the smallest dimension.')
        if floor>=h: raise ValueError('Enclosure floor thickness must be less than its height.')
        body_h=h-spec.thickness if spec.lid and spec.thickness else h
        if spec.lid and not spec.thickness: body_h=h-wall
        if body_h<=floor: raise ValueError('The enclosure lid leaves no room for a cavity.')
        body=cq.Workplane('XY').box(w,d,body_h).translate((0,0,-h/2+body_h/2))
        if spec.enclosure_open:
            cavity_h=body_h-floor
        else:
            cavity_h=body_h-floor-wall
        if cavity_h<=0: raise ValueError('Wall and floor thicknesses leave no enclosure cavity.')
        cavity=cq.Workplane('XY').box(w-2*wall,d-2*wall,cavity_h).translate((0,0,-h/2+floor+cavity_h/2))
        body=body.cut(cavity)
        if spec.lid:
            lid_t=h-body_h
            lid=cq.Workplane('XY').box(w,d,lid_t).translate((0,0,h/2-lid_t/2))
            shape=cq.Compound.makeCompound([body.val(),lid.val()])
        else: shape=body
    elif s in ('mounting_plate','bracket'):
        thickness=spec.thickness or (h if s=='mounting_plate' else min(h,8.0))
        if s=='mounting_plate' and abs(thickness-h)>CAD_TOLERANCE_MM:
            raise ValueError('Mounting plate thickness is its requested height; set dimensions_mm.height to that value.')
        if s=='bracket' and thickness>=min(w,d,h): raise ValueError('Bracket thickness must be smaller than every outer dimension.')
        if s=='mounting_plate': shape=wp.box(w,d,h)
        else:
            base=cq.Workplane('XY').box(w,d,thickness).translate((0,0,-h/2+thickness/2))
            upright=cq.Workplane('XY').box(w,thickness,h).translate((0,d/2-thickness/2,0))
            shape=base.union(upright)
    elif s in ('spacer','bushing'):
        if abs(w-d)>CAD_TOLERANCE_MM: raise ValueError('A spacer or bushing needs equal width and depth (outer diameter).')
        bore=spec.hole_diameter
        if bore is None or bore>=w: raise ValueError('Bore diameter must be smaller than the outside diameter.')
        shape=wp.circle(w/2).circle(bore/2).extrude(h,combine=True).translate((0,0,-h/2))
    elif s=='adapter':
        if abs(w-d)>CAD_TOLERANCE_MM: raise ValueError('A stepped adapter needs equal width and depth (outer diameter).')
        bore=spec.hole_diameter; second=spec.step_diameter; step_h=spec.step_height
        if not bore or not second or not step_h: raise ValueError('An adapter needs bore diameter, second diameter, and step height.')
        if bore>=min(w,second) or second<=0 or step_h>=h: raise ValueError('Adapter bore and step dimensions are incompatible with its outer dimensions.')
        lower_h=h-step_h
        lower=wp.circle(w/2).extrude(lower_h).translate((0,0,-h/2))
        upper=wp.circle(second/2).extrude(step_h).translate((0,0,h/2-step_h))
        shape=lower.union(upper)
        bore_cutter=cq.Workplane('XY').circle(bore/2).extrude(h+2).translate((0,0,-h/2-1))
        shape=shape.cut(bore_cutter)
    elif s=='extruded_profile':
        points=list(spec.profile_points_mm)
        if len(set(points))<3: raise ValueError('Extrusion profile points must form a non-degenerate polygon.')
        shape=wp.polyline(points).close().extrude(h)
    elif s=='loft':
        sections=sorted(spec.loft_sections,key=lambda section:section.z_mm)
        if len({section.z_mm for section in sections})!=len(sections): raise ValueError('Loft profile heights must be distinct.')
        if sections[0].z_mm< -h/2-CAD_TOLERANCE_MM or sections[-1].z_mm>h/2+CAD_TOLERANCE_MM:
            raise ValueError('Loft section heights must fit within the requested height, centered about zero.')
        chain=cq.Workplane('XY').workplane(offset=sections[0].z_mm).polyline(list(sections[0].points_mm)).close()
        previous=sections[0].z_mm
        for section in sections[1:]:
            chain=chain.workplane(offset=section.z_mm-previous).polyline(list(section.points_mm)).close(); previous=section.z_mm
        shape=chain.loft(combine=True)
    elif s in ('boolean','assembly'):
        components=[]
        for component in spec.components:
            dims=component.dimensions_mm
            item=_primitive(cq,component.shape_type,dims.width,dims.depth,dims.height)
            rx,ry,rz=component.rotation_deg
            if rx: item=item.rotate((0,0,0),(1,0,0),rx)
            if ry: item=item.rotate((0,0,0),(0,1,0),ry)
            if rz: item=item.rotate((0,0,0),(0,0,1),rz)
            item=item.translate(component.position_mm)
            components.append(item)
        if s=='assembly':
            if spec.assembly_mode=='compound':
                shape=cq.Compound.makeCompound([item.val() for item in components])
            else:
                shape=components[0]
                try:
                    for item in components[1:]: shape=shape.union(item)
                except Exception as exc:
                    raise ValueError(f'OpenCASCADE could not fuse the requested assembly components: {type(exc).__name__}. Use a compound for separate parts.') from exc
        else:
            shape=components[0]
            try:
                for item in components[1:]:
                    if spec.operation=='union': shape=shape.union(item)
                    elif spec.operation=='difference': shape=shape.cut(item)
                    elif spec.operation=='intersection': shape=shape.intersect(item)
            except Exception as exc: raise ValueError(f'OpenCASCADE Boolean {spec.operation} failed: {type(exc).__name__}.') from exc
    elif s=='organic': raise ValueError('Organic Gaussian meshes use the separate implicit-surface generator.')
    else: raise ValueError(f'Unsupported CAD shape: {s}.')
    return shape

def generate_cad(spec:Spec):
    cq=cadquery_module()
    shape=_base_shape(cq,spec)
    shape=_cut_holes(shape,cq,spec)
    if spec.corner_radius and spec.shape_type=='enclosure':
        try: shape=shape.edges().fillet(spec.corner_radius)
        except Exception as exc: raise ValueError(f'OpenCASCADE could not apply the requested enclosure corner radius: {type(exc).__name__}.') from exc
    if spec.fillet_radius:
        try: shape=shape.edges().fillet(spec.fillet_radius)
        except Exception as exc: raise ValueError(f'OpenCASCADE could not apply a {spec.fillet_radius:g} mm fillet to the selected edges: {type(exc).__name__}.') from exc
    if spec.chamfer_distance:
        try: shape=shape.edges().chamfer(spec.chamfer_distance)
        except Exception as exc: raise ValueError(f'OpenCASCADE could not apply a {spec.chamfer_distance:g} mm chamfer to the selected edges: {type(exc).__name__}.') from exc
    return validate_from_cad_shape(shape.val() if isinstance(shape,cq.Workplane) else shape,
        requested=[spec.dimensions_mm.width,spec.dimensions_mm.depth,spec.dimensions_mm.height],
        tolerance=CAD_TOLERANCE_MM,shape_type=spec.shape_type,
        assembly_mode=spec.assembly_mode if spec.shape_type=='assembly' else None)

def validate_from_cad_shape(cad_shape,requested,tolerance=CAD_TOLERANCE_MM,shape_type=None,assembly_mode=None):
    """Validate a CadQuery/OCCT object, tessellate it, and build the validation payload.

    Shared by the parametric kernel and by the Gemini CadQuery-script path.
    """
    try:
        valid=bool(cad_shape.isValid())
        solids=cad_shape.Solids()
        volume=float(cad_shape.Volume())
        box=cad_shape.BoundingBox()
    except Exception as exc: raise ValueError(f'OpenCASCADE could not validate the generated B-rep: {type(exc).__name__}.') from exc
    if not valid or not solids or not math.isfinite(volume) or volume<=0:
        raise ValueError('OpenCASCADE produced an invalid, empty, or zero-volume CAD solid.')
    if shape_type=='assembly' and assembly_mode=='union' and len(solids)!=1:
        raise ValueError('Assembly components in union mode must fuse into a single connected solid; parts that merely touch bounding boxes are not enough. Ensure the components physically contact each other, or request assembly_mode="compound" for separate pieces.')
    max_solids=_clamped_env_int('MF_MAX_CAD_SOLIDS',DEFAULT_MAX_SOLID_COUNT,1,1024)
    if len(solids)>max_solids:
        raise ValueError('The generated model contains %d solids; the configured limit is %d.' % (len(solids),max_solids))
    actual=np.array([box.xlen,box.ylen,box.zlen],dtype=float)
    if not np.isfinite(actual).all() or np.min(actual)<=1e-6: raise ValueError('OpenCASCADE produced degenerate outer bounds.')
    requested=np.array(requested,dtype=float)
    discrepancy=np.abs(actual-requested)
    if np.any(discrepancy>tolerance):
        raise ValueError('CAD dimensions differ from the request beyond %.2f mm: requested %s mm, actual %s mm.' % (tolerance,requested.round(3).tolist(),actual.round(3).tolist()))
    return _validated_mesh_payload(cad_shape,valid,len(solids),volume,actual,requested,discrepancy,tolerance)


def split_compound_components(cad_shape, components, tolerance=CAD_TOLERANCE_MM):
    """Split a CadQuery Compound into per-component meshes.

    Each solid in the compound is matched to a component declaration by
    comparing the solid's bounding-box dimensions to the component's declared
    dimensions, then to position. Solid ordering alone is not treated as
    semantic identity because BREP import does not preserve solid names.

    Returns a list of (component_id, trimesh.Trimesh) tuples in component
    declaration order. Raises ValueError if the compound cannot be split or a
    component cannot be matched to a distinct solid.
    """
    try:
        solids=cad_shape.Solids()
    except Exception as exc: raise ValueError(f'OpenCASCADE could not enumerate compound solids: {type(exc).__name__}.') from exc
    if not solids: raise ValueError('Compound contains no solids.')
    if len(solids)<len(components): raise ValueError(f'Compound has {len(solids)} solids but {len(components)} components were declared.')

    # Build per-solid bounding boxes.
    solid_boxes=[]
    for solid in solids:
        try:
            box=solid.BoundingBox()
            dims=[box.xlen,box.ylen,box.zlen]
            center=[(box.xmin+box.xmax)/2,(box.ymin+box.ymax)/2,(box.zmin+box.zmax)/2]
            volume=float(solid.Volume())
        except Exception as exc: raise ValueError(f'OpenCASCADE could not read a compound solid: {type(exc).__name__}.') from exc
        solid_boxes.append({'dims':dims,'center':center,'volume':volume,'solid':solid})

    def dims_match(solid_dims, comp_dims, tol):
        return all(abs(solid_dims[i]-comp_dims[i])<=tol for i in range(3))

    assignments=[]
    used=set()
    for comp in components:
        comp_dims=[comp.dimensions_mm.width,comp.dimensions_mm.depth,comp.dimensions_mm.height]
        comp_center=list(comp.position_mm)
        best=None; best_score=None
        for idx,sb in enumerate(solid_boxes):
            if idx in used: continue
            if not dims_match(sb['dims'],comp_dims,tolerance*4): continue
            # Position proximity is a secondary tiebreaker.
            dist=sum((sb['center'][i]-comp_center[i])**2 for i in range(3))**0.5
            score=(dist,sb['volume'])
            if best_score is None or score<best_score:
                best=idx; best_score=score
        if best is None: raise ValueError(f'Could not match component "{comp.name}" to a distinct solid in the compound.')
        used.add(best); assignments.append((comp,best))

    # Build meshes in component declaration order.
    results=[]
    for comp,solid_idx in assignments:
        solid=solid_boxes[solid_idx]['solid']
        try:
            points,triangles=solid.tessellate(TESSELLATION_TOLERANCE_MM,ANGULAR_TOLERANCE_RAD)
        except Exception as exc: raise ValueError(f'Component "{comp.name}" failed to tessellate: {type(exc).__name__}.') from exc
        vertices=np.asarray([[point.x,point.y,point.z] for point in points],dtype=np.float64)
        faces=np.asarray(triangles,dtype=np.int64)
        mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=True,validate=True)
        mesh.merge_vertices(digits_vertex=5)
        mesh.remove_unreferenced_vertices()
        mesh.fix_normals()
        if mesh.is_empty or not np.isfinite(mesh.vertices).all(): raise ValueError(f'Component "{comp.name}" produced an empty or non-finite mesh.')
        results.append((comp,mesh))
    return results

def validate_script_shape(cad_shape,requested):
    """Validate a Gemini-generated CadQuery result against the requested bounds."""
    return validate_from_cad_shape(cad_shape,requested,tolerance=script_dimension_tolerance(requested))

def script_dimension_tolerance(requested):
    return max(0.5,0.01*max(float(value) for value in requested))

def _validated_mesh_payload(cad_shape,valid,solid_count,volume,actual,requested,discrepancy,tolerance):
    cq=cadquery_module()
    points,triangles=cad_shape.tessellate(TESSELLATION_TOLERANCE_MM,ANGULAR_TOLERANCE_RAD)
    vertices=np.asarray([[point.x,point.y,point.z] for point in points],dtype=np.float64)
    faces=np.asarray(triangles,dtype=np.int64)
    # validate=True also removes OCC's duplicate seam facets before topology checks.
    mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=True,validate=True)
    # OCC tessellates adjacent faces independently; snap only sub-0.00001 mm seam noise
    # before topology checks so shared B-rep edges become shared mesh vertices.
    mesh.merge_vertices(digits_vertex=5)
    mesh.remove_unreferenced_vertices()
    mesh.fix_normals()
    if mesh.is_empty or not np.isfinite(mesh.vertices).all(): raise ValueError('CAD tessellation produced an empty or non-finite mesh.')
    max_vertices=_clamped_env_int('MF_MAX_MESH_VERTICES',DEFAULT_MAX_MESH_VERTICES,1,4_000_000)
    max_faces=_clamped_env_int('MF_MAX_MESH_FACES',DEFAULT_MAX_MESH_FACES,1,8_000_000)
    if len(mesh.vertices)>max_vertices or len(mesh.faces)>max_faces:
        raise ValueError('The generated geometry tessellated to %d vertices and %d faces; the configured limits are %d vertices and %d faces.' % (len(mesh.vertices),len(mesh.faces),max_vertices,max_faces))
    validation={
        'cad_solid_valid':valid,'solid_count':solid_count,'volume_mm3':round(volume,4),
        'dimensions_mm':[round(float(value),4) for value in actual],
        'requested_dimensions_mm':[round(float(value),4) for value in requested],
        'dimension_error_mm':[round(float(value),4) for value in discrepancy],
        'dimension_tolerance_mm':tolerance,
        'dimensions_within_tolerance':bool(np.all(discrepancy<=tolerance)),
        'watertight':bool(mesh.is_watertight),'winding_consistent':bool(mesh.is_winding_consistent),
        'vertices':len(mesh.vertices),'faces':len(mesh.faces),
        'printability':'validated watertight CAD tessellation' if mesh.is_watertight else 'not guaranteed printable',
    }
    return cad_shape,mesh,validation

def save_step(shape,path:Path):
    cq=cadquery_module()
    cq.exporters.export(shape,str(path),exportType='STEP')
