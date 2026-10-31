import math
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CAD_TOLERANCE_MM = 0.05

Shape = Literal['box','rounded_box','cylinder','sphere','cone','tube','enclosure','organic',
                'extruded_profile','loft','boolean','assembly','bracket','mounting_plate','spacer','bushing','adapter']
PrimitiveShape = Literal['box','cylinder','sphere','cone']
BooleanOperation = Literal['union','difference','intersection']
AssemblyMode = Literal['union','compound']

class Dimensions(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    width: float = Field(gt=0, le=500)
    depth: float = Field(gt=0, le=500)
    height: float = Field(gt=0, le=500)

class GaussianLobe(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    center: tuple[float,float,float] = (0,0,0)
    scales: tuple[float,float,float] = (1,1,1)
    amplitude: float = Field(default=1, gt=0, le=10)

class CADHole(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    position_mm: tuple[float,float,float] = (0,0,0)
    diameter: float = Field(gt=0.5,le=100)
    depth: Optional[float] = Field(default=None,gt=0,le=500)
    axis: Literal['x','y','z'] = 'z'

class CADComponent(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    name: str = Field(default='part', min_length=1, max_length=80)
    shape_type: PrimitiveShape
    dimensions_mm: Dimensions
    position_mm: tuple[float,float,float] = (0,0,0)
    rotation_deg: tuple[float,float,float] = (0,0,0)

    @field_validator('position_mm')
    @classmethod
    def bounded_position(cls, value):
        if any(abs(axis)>500 for axis in value): raise ValueError('Component positions must be within 500 mm of the assembly origin.')
        return value

    @field_validator('rotation_deg')
    @classmethod
    def bounded_rotation(cls, value):
        if any(abs(angle)>3600 for angle in value): raise ValueError('Component rotations must be within ten full turns per axis.')
        return value


def _world_abs_matrix(component):
    """Absolute row coefficients |Rz*Ry*Rx| of an assembly component's Euler rotation."""
    rx,ry,rz=component.rotation_deg
    def rotation(axis, deg):
        t=math.radians(deg); c,s=math.cos(t),math.sin(t)
        if axis=='x': return [[1,0,0],[0,c,-s],[0,s,c]]
        if axis=='y': return [[c,0,s],[0,1,0],[-s,0,c]]
        return [[c,-s,0],[s,c,0],[0,0,1]]
    def multiply(a, b):
        return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return multiply(rotation('z',rz),multiply(rotation('y',ry),rotation('x',rx)))


def _rotated_aabb(component):
    """World-space AABB (lo, hi) of an assembly component after its Euler rotations."""
    dims=(component.dimensions_mm.width,component.dimensions_mm.depth,component.dimensions_mm.height)
    world=_world_abs_matrix(component)
    extents=tuple(sum(abs(world[i][j])*dims[j] for j in range(3)) for i in range(3))
    position=component.position_mm
    return tuple(position[i]-extents[i]/2 for i in range(3)),tuple(position[i]+extents[i]/2 for i in range(3))


def _assembly_connected(bounds):
    """Greedy proximity graph over component AABBs; a conservative pass-gate only."""
    count=len(bounds)
    if count<2: return True
    def adjacent(first, second):
        return all(first[1][i]>=second[0][i]-1e-9 and second[1][i]>=first[0][i]-1e-9 for i in range(3))
    seen={0}; stack=[0]
    while stack:
        current=stack.pop()
        for other in range(count):
            if other not in seen and adjacent(bounds[current],bounds[other]):
                seen.add(other); stack.append(other)
    return len(seen)==count


def _slide_flush(components, requested):
    """Slide every component overhanging a requested face exactly flush to it.

    A component longer than the requested extent on an axis (overhang on both
    faces) is longer than the box and is left for the rescale pass instead.
    """
    moved=list(components)
    for index,component in enumerate(moved):
        low,high=_rotated_aabb(component)
        shift=[0.0,0.0,0.0]
        for axis in range(3):
            limit=requested[axis]/2
            over_top=high[axis]-limit>0
            over_bottom=-limit-low[axis]>0
            if over_top and over_bottom: continue
            if over_top: shift[axis]=-(high[axis]-limit)
            elif over_bottom: shift[axis]=-limit-low[axis]
        if any(shift):
            x,y,z=component.position_mm
            moved[index]=component.model_copy(update={'position_mm':(x+shift[0],y+shift[1],z+shift[2])})
    return moved


def fit_assembly_components(components, dimensions):
    """Repair an assembly plan against the requested outer bounds.

    Repairs escalate: overhangs are slid flush to the nearest requested face,
    short faces are closed by nudging the strand of parts tied at that face,
    and remaining span mismatches are repaired by proportionally rescaling
    component dimensions along the short axis while positions map affinely so
    overlaps survive. Rotations never change, so a planned tilt stays tilted.
    Returns the fitted components, or None when no repair can satisfy the
    requested bounds; the CAD kernel then rejects the specification unchanged.
    """
    requested=(dimensions.width,dimensions.depth,dimensions.height)
    tolerance=CAD_TOLERANCE_MM
    fitted=list(components)
    for _ in range(20):
        fitted=_slide_flush(fitted,requested)
        bounds=[_rotated_aabb(component) for component in fitted]
        spans=[max(high[axis] for low,high in bounds)-min(low[axis] for low,high in bounds) for axis in range(3)]
        if all(abs(spans[axis]-requested[axis])<=tolerance for axis in range(3)):
            if _assembly_connected(bounds): return fitted
            repaired=_connect_floating(fitted,requested)
            if repaired is not None and _assembly_gates(repaired,requested) is not None: return repaired
            return None
        nudged=list(fitted)
        nudged_changed=False
        for axis in range(3):
            if abs(spans[axis]-requested[axis])<=tolerance: continue
            limit=requested[axis]/2
            union_low=min(low[axis] for low,high in bounds)
            union_high=max(high[axis] for low,high in bounds)
            top=[i for i in range(len(nudged)) if bounds[i][1][axis]>=union_high-tolerance]
            bottom=[i for i in range(len(nudged)) if bounds[i][0][axis]<=union_low+tolerance]
            if set(top)&set(bottom): continue
            for strand,delta in ((top,limit-union_high),(bottom,union_low+limit)):
                for i in strand:
                    if delta<=tolerance: continue
                    position=list(nudged[i].position_mm)
                    position[axis]+=delta
                    nudged[i]=nudged[i].model_copy(update={'position_mm':tuple(position)})
                    nudged_changed=True
        if nudged_changed and _assembly_gates(nudged,requested) is not None: return nudged
        stretched=fitted
        for axis in range(3):
            if abs(spans[axis]-requested[axis])<=tolerance: continue
            ratio=requested[axis]/spans[axis]
            if not 0.25<=ratio<=4.0: return None
            current=[_rotated_aabb(component) for component in stretched]
            union_low=min(low[axis] for low,high in current)
            stretched=_stretched(stretched,axis,ratio,union_low,-requested[axis]/2)
        fitted=stretched
    return None


def _stretched(components, axis, ratio, union_low, target_low):
    """Scale component dimensions along one axis so the union extent grows by ratio.

    Growth is distributed across each part's local dimensions in proportion to
    how much they currently contribute to the world-space extent, so rotated
    parts keep their rotation while the world extent on that axis scales by
    exactly `ratio`, and positions map affinely so overlaps survive.
    """
    grown=[]
    for component in components:
        dims=(component.dimensions_mm.width,component.dimensions_mm.depth,component.dimensions_mm.height)
        row=_world_abs_matrix(component)[axis]
        contributions=[abs(row[j])*dims[j] for j in range(3)]
        extent=sum(contributions)
        slope=sum(contribution*contribution for contribution in contributions)/extent
        scaled=tuple(dims[j]*(1.0+(ratio-1.0)*contributions[j]/slope) for j in range(3))
        position=list(component.position_mm)
        position[axis]=target_low+(position[axis]-union_low)*ratio
        grown.append(component.model_copy(update={
            'dimensions_mm':component.dimensions_mm.model_copy(update={'width':scaled[0],'depth':scaled[1],'height':scaled[2]}),
            'position_mm':tuple(position),
        }))
    return grown


def _assembly_gates(components, requested):
    """Accept a repaired plan only when union bounds are exact and the parts touch."""
    bounds=[_rotated_aabb(component) for component in components]
    for axis in range(3):
        span=max(high[axis] for low,high in bounds)-min(low[axis] for low,high in bounds)
        if abs(span-requested[axis])>CAD_TOLERANCE_MM: return None
    if not _assembly_connected(bounds): return None
    return bounds


def _adjacent(first, second):
    return all(first[1][i]>=second[0][i]-1e-9 and second[1][i]>=first[0][i]-1e-9 for i in range(3))


def _settle_move(index, components, bounds, requested):
    """Cheapest contact repair for one floating part: (axis, kind, value, anchor).

    A part owning a requested face on the gap axis must grow into the assembly:
    its dimension scales by `value` and the face stays, anchored at `anchor`.
    A free part slides by `value` along its cheapest axis. Both reach the
    nearest part the floating one already cross-sections in the other two axes.
    """
    embed=0.05
    limit_move=0.05*min(requested)
    best=None
    for other in range(len(components)):
        if other==index: continue
        for axis in range(3):
            others=[a for a in range(3) if a!=axis]
            if not all(bounds[index][1][a]>=bounds[other][0][a]-1e-9 and bounds[other][1][a]>=bounds[index][0][a]-1e-9 for a in others): continue
            mine=bounds[index]; theirs=bounds[other]
            if theirs[0][axis]>mine[1][axis]: gap=theirs[0][axis]-mine[1][axis]; toward=1
            elif mine[0][axis]>theirs[1][axis]: gap=mine[0][axis]-theirs[1][axis]; toward=-1
            else: continue
            peers=[o for o in range(len(bounds)) if o!=index]
            owns_high=all(mine[1][axis]>=bounds[o][1][axis]-1e-9 for o in peers)
            owns_low=all(mine[0][axis]<=bounds[o][0][axis]+1e-9 for o in peers)
            if owns_high and owns_low: continue
            if owns_high or owns_low:
                component=components[index]
                dims=(component.dimensions_mm.width,component.dimensions_mm.depth,component.dimensions_mm.height)
                extent=sum(abs(_world_abs_matrix(component)[axis][j])*dims[j] for j in range(3))
                grow=1.0+(gap+embed)/extent
                if not 1.0<=grow<=1.2: continue
                if best is None or gap<best[0]: best=(gap,axis,'grow',grow,'high' if owns_high else 'low')
            else:
                if gap>limit_move: continue
                if best is None or gap<best[0]: best=(gap,axis,'slide',toward*(gap+embed),None)
    return None if best is None else best[1:]


def _connect_floating(components, requested):
    """Close tiny float gaps so every disconnected part genuinely contacts a neighbor.

    Exact union bounds can still hold a planned part a fraction of a millimetre
    above its support, which the kernel correctly refuses to fuse. A part pinned
    at a requested face grows just enough to reach the part it already
    cross-sections; a free-floating part slides along its cheapest axis.
    Rotations are never touched.
    """
    repaired=list(components)
    for _ in range(4):
        bounds=[_rotated_aabb(component) for component in repaired]
        floating=[i for i in range(len(repaired)) if not any(_adjacent(bounds[i],bounds[o]) for o in range(len(repaired)) if o!=i)]
        if not floating: break
        progressed=False
        for index in floating:
            move=_settle_move(index,repaired,bounds,requested)
            if move is None: continue
            axis,kind,value,anchor=move
            component=repaired[index]
            if kind=='grow':
                dims=(component.dimensions_mm.width,component.dimensions_mm.depth,component.dimensions_mm.height)
                row=_world_abs_matrix(component)[axis]
                contributions=[abs(row[j])*dims[j] for j in range(3)]
                extent=sum(contributions)
                slope=sum(contribution*contribution for contribution in contributions)/extent
                scaled=tuple(dims[j]*(1.0+(value-1.0)*contributions[j]/slope) for j in range(3))
                low,high=bounds[index]
                position=list(component.position_mm)
                if anchor=='high':
                    position[axis]=high[axis]-extent*value/2
                else:
                    position[axis]=low[axis]+extent*value/2
                repaired[index]=component.model_copy(update={
                    'dimensions_mm':component.dimensions_mm.model_copy(update={'width':scaled[0],'depth':scaled[1],'height':scaled[2]}),
                    'position_mm':tuple(position)})
            else:
                position=list(component.position_mm)
                position[axis]+=value
                repaired[index]=component.model_copy(update={'position_mm':tuple(position)})
            progressed=True
        if not progressed: return None
        repaired=_slide_flush(repaired,requested)
    return repaired

class ProfileSection(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    z_mm: float = Field(ge=-250,le=250)
    points_mm: list[tuple[float,float]] = Field(min_length=3,max_length=128)

class ComponentMeta(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    dimensions_mm: list[float] = Field(min_length=3, max_length=3)
    position_mm: list[float] = Field(min_length=3, max_length=3)
    rotation_deg: list[float] = Field(min_length=3, max_length=3)
    solid_count: int = Field(default=1, ge=1)
    volume_mm3: Optional[float] = Field(default=None, gt=0)
    watertight: Optional[bool] = None
    vertices: Optional[int] = Field(default=None, ge=0)
    faces: Optional[int] = Field(default=None, ge=0)
    mesh_file: str = Field(min_length=1, max_length=120)


class Spec(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    shape_type: Shape
    dimensions_mm: Dimensions
    wall_thickness: Optional[float] = Field(default=None, gt=0, le=50)
    floor_thickness: Optional[float] = Field(default=None, gt=0, le=50)
    radius: Optional[float] = Field(default=None, gt=0, le=250)
    corner_radius: Optional[float] = Field(default=None,gt=0,le=100)
    hole_diameter: Optional[float] = Field(default=None,gt=0.5,le=100)
    holes: list[CADHole] = Field(default_factory=list,max_length=32)
    fillet_radius: Optional[float] = Field(default=None,gt=0,le=50)
    chamfer_distance: Optional[float] = Field(default=None,gt=0,le=50)
    enclosure_open: bool = True
    lid: bool = False
    operation: Optional[BooleanOperation] = None
    assembly_mode: AssemblyMode = 'union'
    components: list[CADComponent] = Field(default_factory=list,max_length=16)
    object_identity: Optional[str] = Field(default=None, max_length=120)
    intended_use: Optional[str] = Field(default=None, max_length=240)
    design_warnings: list[str] = Field(default_factory=list, max_length=8)
    profile_points_mm: list[tuple[float,float]] = Field(default_factory=list,max_length=128)
    loft_sections: list[ProfileSection] = Field(default_factory=list,max_length=16)
    thickness: Optional[float] = Field(default=None,gt=0,le=100)
    step_diameter: Optional[float] = Field(default=None,gt=0.5,le=500)
    step_height: Optional[float] = Field(default=None,gt=0.5,le=500)
    lobes: list[GaussianLobe] = Field(default_factory=list, max_length=16)
    resolution: int = Field(default=48, ge=24, le=96)

    @model_validator(mode='after')
    def validate_shape_parameters(self):
        d=self.dimensions_mm
        if self.shape_type=='organic' and (self.holes or self.wall_thickness is not None or self.floor_thickness is not None or self.corner_radius is not None or self.hole_diameter is not None or self.fillet_radius is not None or self.chamfer_distance is not None or self.lid):
            raise ValueError('Mechanical holes, walls, fillets, chamfers, and lids are not available on Gaussian organic meshes.')
        if self.shape_type not in ('enclosure','rounded_box') and self.corner_radius is not None:
            raise ValueError('Corner radius is supported for rounded boxes and enclosures only.')
        if self.shape_type not in ('enclosure','tube') and (self.wall_thickness is not None or self.floor_thickness is not None):
            raise ValueError('Wall and floor thickness are supported for tubes and enclosures only.')
        if self.shape_type=='rounded_box':
            r=self.corner_radius or min(d.width,d.depth,d.height)*0.1
            if r*2>=min(d.width,d.depth,d.height): raise ValueError('Corner radius must be less than half the smallest box dimension.')
        if self.shape_type=='enclosure':
            wall=self.wall_thickness or 2.0; floor=self.floor_thickness or wall
            if 2*wall>=min(d.width,d.depth,d.height): raise ValueError('Wall thickness must be less than half the smallest enclosure dimension.')
            if floor>=d.height: raise ValueError('Floor thickness must be smaller than enclosure height.')
            if self.lid and not self.enclosure_open: raise ValueError('A separate lid can only be requested for an open enclosure.')
        if self.shape_type in ('spacer','bushing') and self.hole_diameter is None:
            raise ValueError('Specify a bore diameter for the spacer or bushing.')
        if self.shape_type in ('spacer','bushing') and self.hole_diameter is not None and self.hole_diameter>=min(d.width,d.depth):
            raise ValueError('Bore diameter must be smaller than the spacer or bushing outside diameter.')
        if self.shape_type=='adapter' and (self.hole_diameter is None or self.step_diameter is None or self.step_height is None):
            raise ValueError('An adapter needs a bore diameter, second outer diameter, and step height.')
        if self.shape_type=='adapter' and self.hole_diameter is not None and self.step_diameter is not None:
            if self.hole_diameter>=min(d.width,self.step_diameter) or self.step_diameter>d.width or self.step_height>=d.height:
                raise ValueError('Adapter bore, step diameter, and step height must fit inside its requested outer dimensions.')
        if self.shape_type in ('boolean','assembly'):
            if self.shape_type=='boolean' and (not self.operation or len(self.components)<2): raise ValueError('A Boolean model needs an operation and at least two primitive components.')
            if self.shape_type=='assembly' and (self.operation is not None or not self.components): raise ValueError('An assembly needs at least one component and does not take a Boolean operation.')
            if self.shape_type=='assembly' and self.components:
                fitted=fit_assembly_components(self.components,self.dimensions_mm)
                if fitted is not None: self.components=fitted
        elif self.operation is not None or self.components:
            raise ValueError('Components and Boolean operations are only valid for Boolean models and assemblies.')
        if self.shape_type=='extruded_profile' and len(self.profile_points_mm)<3:
            raise ValueError('An extruded profile needs at least three 2D points.')
        if self.shape_type=='loft' and len(self.loft_sections)<2:
            raise ValueError('A loft needs at least two profile sections.')
        if self.shape_type not in ('extruded_profile','loft') and (self.profile_points_mm or self.loft_sections):
            raise ValueError('Profile points are only valid for profile and loft geometry.')
        for hole in self.holes:
            x,y,_=hole.position_mm
            if abs(x)>d.width/2 or abs(y)>d.depth/2:
                raise ValueError('Hole center must lie inside the requested part bounds.')
        for size,label in ((self.fillet_radius,'Fillet'),(self.chamfer_distance,'Chamfer')):
            if size is not None and size*2>=min(d.width,d.depth,d.height):
                raise ValueError(f'{label} size must be less than half the smallest part dimension.')
        return self

InterpreterChoice=Literal['auto','gemini','local']

SettingsProvider=Literal['gemini','local']

class SettingsSave(BaseModel):
    provider: SettingsProvider
    api_key: Optional[str] = Field(default=None, max_length=4000)

class SettingsResponse(BaseModel):
    provider: SettingsProvider
    configured: bool
    connection_status: Optional[str] = None

class SettingsTestRequest(BaseModel):
    provider: SettingsProvider = 'gemini'
    api_key: Optional[str] = Field(default=None, max_length=4000)

class SettingsTestResponse(BaseModel):
    status: str  # 'ok' | 'failed'
    message: str


class GenerateRequest(BaseModel):
    spec: Optional[Spec] = None
    prompt: str = Field(default='', max_length=4000)
    conversation_id: Optional[str] = None
    parent_model_id: Optional[str] = None
    interpreter: Optional[InterpreterChoice] = None
    clearance_mm: Optional[float] = Field(default=None, ge=0)

class ParseRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    interpreter: Optional[InterpreterChoice] = None

class RefineRequest(BaseModel):
    model_id: Optional[str] = None
    instruction: str = Field(min_length=3, max_length=500)
    interpreter: Optional[InterpreterChoice] = None
    clearance_mm: Optional[float] = Field(default=None, ge=0)

class ConversationCreate(BaseModel):
    title: str = Field(default='New creation', min_length=1, max_length=240)

class ConversationPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=240)
    archived: Optional[bool] = None

class MessageCreate(BaseModel):
    role: Literal['user','assistant','system']
    text: str = Field(min_length=1, max_length=8000)
    metadata: Optional[dict] = None

class LocalImportMessage(BaseModel):
    role: Literal['user','assistant','system']
    text: str = Field(max_length=8000)
    metadata: Optional[dict] = None

class LocalImportConversation(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(default='Imported conversation', max_length=240)
    messages: list[LocalImportMessage] = Field(default_factory=list, max_length=200)

class LocalImportRequest(BaseModel):
    conversations: list[LocalImportConversation] = Field(max_length=100)

class DimensionsInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    width: float = Field(gt=0, le=500)
    depth: float = Field(gt=0, le=500)
    height: float = Field(gt=0, le=500)

class GeometryInterpretation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    shape_type: Shape
    generation_method: Literal['parametric_cad','gaussian_implicit_mesh']
    dimensions_mm: DimensionsInput
    wall_thickness: Optional[float] = Field(default=None, gt=0, le=50)
    floor_thickness: Optional[float] = Field(default=None,gt=0,le=50)
    corner_radius: Optional[float] = Field(default=None,gt=0,le=100)
    hole_diameter: Optional[float] = Field(default=None,gt=0.5,le=100)
    holes: list[CADHole] = Field(default_factory=list,max_length=32)
    fillet_radius: Optional[float] = Field(default=None,gt=0,le=50)
    chamfer_distance: Optional[float] = Field(default=None,gt=0,le=50)
    enclosure_open: bool = True
    lid: bool = False
    operation: Optional[BooleanOperation] = None
    assembly_mode: AssemblyMode = 'union'
    components: list[CADComponent] = Field(default_factory=list,max_length=16)
    object_identity: Optional[str] = Field(default=None, max_length=120)
    intended_use: Optional[str] = Field(default=None, max_length=240)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)
    profile_points_mm: list[tuple[float,float]] = Field(default_factory=list,max_length=128)
    loft_sections: list[ProfileSection] = Field(default_factory=list,max_length=16)
    thickness: Optional[float] = Field(default=None,gt=0,le=100)
    step_diameter: Optional[float] = Field(default=None,gt=0.5,le=500)
    step_height: Optional[float] = Field(default=None,gt=0.5,le=500)
    lobes: list[GaussianLobe] = Field(default_factory=list, max_length=16)
    resolution: int = Field(default=48, ge=24, le=96)
    features: list[str] = Field(default_factory=list, max_length=8)
    needs_clarification: bool = False
    clarifying_question: Optional[str] = Field(default=None, max_length=500)
    explanation: str = Field(default='', max_length=500)

class InterpretRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    previous_spec: Optional[Spec] = None
    recent_context: list[dict] = Field(default_factory=list, max_length=8)
    interpreter: Optional[InterpreterChoice] = None


class CancelRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)


class CancelResponse(BaseModel):
    request_id: str
    cancelled: bool
    status: str  # 'running' | 'cancellation_requested' | 'cancelled' | 'completed' | 'failed' | 'unknown'
