from typing import Optional
import numpy as np
import trimesh
from skimage.measure import marching_cubes
from .schemas import Spec

def _mesh(spec: Spec) -> trimesh.Trimesh:
    w,d,h=spec.dimensions_mm.width,spec.dimensions_mm.depth,spec.dimensions_mm.height
    s=spec.shape_type
    if s in ('box','rounded_box','enclosure'):
        if s=='enclosure':
            t=spec.wall_thickness or 2
            if 2*t>=min(w,d,h): raise ValueError('Wall thickness must be less than half the smallest enclosure dimension.')
            # Open-top shell. Panels meet at their edges and use the requested outer bounds.
            pieces=[trimesh.creation.box([w,d,t])]
            pieces[0].apply_translation([0,0,t/2])
            for x in (-w/2+t/2,w/2-t/2):
                part=trimesh.creation.box([t,d-2*t,h-t]); part.apply_translation([x,0,(h+t)/2]); pieces.append(part)
            for y in (-d/2+t/2,d/2-t/2):
                part=trimesh.creation.box([w,t,h-t]); part.apply_translation([0,y,(h+t)/2]); pieces.append(part)
            return trimesh.util.concatenate(pieces)
        return trimesh.creation.box([w,d,h])
    if s=='cylinder': return trimesh.creation.cylinder(radius=w/2,height=h,sections=64)
    if s=='tube':
        t=spec.wall_thickness or max(1,w*.1)
        if t>=w/2: raise ValueError('Tube wall thickness must be less than its radius.')
        return trimesh.creation.annulus(r_min=w/2-t,r_max=w/2,height=h,sections=64)
    if s=='sphere': return trimesh.creation.icosphere(subdivisions=4,radius=.5)
    if s=='cone': return trimesh.creation.cone(radius=w/2,height=h,sections=64)
    if s=='organic': return _gaussian(spec)
    raise ValueError(f'Unsupported shape: {s}')

def _gaussian(spec):
    w,d,h=spec.dimensions_mm.width,spec.dimensions_mm.depth,spec.dimensions_mm.height
    n=spec.resolution
    # Normalized coordinates bound memory at n^3 <= 884,736 samples.
    axis=np.linspace(-1.25,1.25,n,dtype=np.float32)
    x,y,z=np.meshgrid(axis,axis,axis,indexing='ij')
    # A broad central lobe plus deterministic satellites gives a smooth organic form.
    centers=[(0,0,0,.95, .72,.72,.72),(.43,.05,.04,.62,.38,.43,.46),(-.38,-.06,.02,.60,.40,.45,.43),(.05,.04,.43,.56,.46,.43,.38),(-.04,0,-.43,.53,.45,.44,.38)]
    if spec.lobes:
        centers=[(a.center[0],a.center[1],a.center[2],a.amplitude,*a.scales) for a in spec.lobes]
    field=np.zeros_like(x)
    for cx,cy,cz,amp,sx,sy,sz in centers:
        field+=amp*np.exp(-.5*(((x-cx)/sx)**2+((y-cy)/sy)**2+((z-cz)/sz)**2))
    if field.max()<.5: raise ValueError('Gaussian field has no surface at the extraction threshold.')
    verts,faces,normals,_=marching_cubes(field,level=.5,spacing=(2.5/(n-1),)*3)
    verts-=verts.mean(axis=0)
    mesh=trimesh.Trimesh(vertices=verts,faces=faces,process=True)
    ext=mesh.extents
    if not np.isfinite(ext).all() or min(ext)<=1e-8: raise ValueError('Generated Gaussian surface is degenerate.')
    mesh.apply_scale([w/ext[0],d/ext[1],h/ext[2]])
    return mesh

def generate_with_kernel(spec: Spec, requested_clearance_mm: Optional[float] = None):
    if spec.shape_type=='organic':
        mesh=_mesh(spec); kernel_shape=None
        generation_method='gaussian_implicit_mesh'
    else:
        from .cad_geometry import generate_cad
        kernel_shape,mesh,validation=generate_cad(spec, requested_clearance_mm=requested_clearance_mm)
        return mesh,validation,kernel_shape
    if len(mesh.vertices)==0 or not np.isfinite(mesh.vertices).all(): raise ValueError('Geometry is empty or contains non-finite vertices.')
    mesh.fix_normals()
    ext=mesh.extents.tolist()
    validation={'watertight':bool(mesh.is_watertight),'winding_consistent':bool(mesh.is_winding_consistent),'vertices':len(mesh.vertices),'faces':len(mesh.faces),'dimensions_mm':[round(v,3) for v in ext],'printability':'validated mesh' if mesh.is_watertight else 'not guaranteed printable','cad_solid_valid':None,'generation_method':generation_method}
    return mesh,validation,kernel_shape

def generate(spec: Spec):
    mesh,validation,_=generate_with_kernel(spec)
    return mesh,validation
