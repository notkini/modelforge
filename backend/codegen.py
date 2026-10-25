"""Gemini -> CadQuery script generation.

Gemini returns a complete, deterministic CadQuery Python program (not a
GeometryInterpretation plan). The program is executed inside
:mod:`script_runner`'s OS-sandboxed worker, and the resulting geometry is
validated through the shared CadQuery/OpenCASCADE pipeline in
:mod:`cad_geometry`. Failures go back to Gemini as sanitized errors for a
bounded number of repair attempts. There is never a fallback to the local
interpreter on this path.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import trimesh

from .cad_geometry import (cadquery_module, script_dimension_tolerance, validate_from_cad_shape,
                           split_compound_components, TESSELLATION_TOLERANCE_MM, ANGULAR_TOLERANCE_RAD)
from .config import get_settings
from .nlp import _gemini_failure_category, _gemini_failure_reason
from .script_runner import ScriptSandboxError, cleanup_workdir, run_script

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 8192
MAX_ATTEMPTS = 3
MAX_RECENT_CONTEXT = 6
MAX_DIMENSION_MM = 500.0
SCRIPT_METHOD = 'cadquery_script'

SCRIPT_INSTRUCTIONS = """You are ModelForge's CadQuery construction agent. You write complete, executable CadQuery\
 Python programs and return them in exactly one fenced code block (```python ... ```). No prose outside the fence.

PROGRAM CONTRACT (violations fail validation and trigger repair attempts):
1. Allowed imports: EXACTLY `import cadquery as cq` and `import math`. No other imports of any kind.
2. Millimetre units. Build the real geometry with CadQuery operations: boxes, cylinders, spheres, cones,\
 polyline extrusions, revolves, lofts, unions, cuts, intersections, fillets, chamfers, and Workplane\
 transformations. Assemblies are composed by grouping positioned primitives into one `result` compound\
 (`.val()` wrapped in `cq.Compound.makeCompound([...])`) when parts must remain separate and printable;\
 use `.union()` only when the user explicitly asks for a single fused object.
3. Center the model near the origin (bounding box roughly symmetric).
4. The outer bounding box of `result` must match the declared plan dimensions exactly (within 0.5 mm).\
 Floating-point drift is not an excuse: sizes are arithmetic, not approximations.
5. Assign the final geometry to a variable named `result` (a cq.Workplane, cq.Solid, cq.Compound, or cq.Shape).
6. The script begins with comment lines:
   # plan-dimensions: <width>,<depth>,<height>
   # explanation: <one short sentence>
   # components: <name1>@<w>,<d>,<h>;<name2>@<w>,<d>,<h>  (only when multiple separate parts are requested)
7. Fully deterministic: no randomness, no time reads, no files, no network, no subprocesses, no input().\
 Never wrap modeling calls in try/except.
8. Prefer a single fused solid for printable objects; keep separate parts only when the user explicitly\
 requests multiple independently printable components. When parts must fit together (lid on enclosure,\
 clamp on bracket), add explicit clearance (typically 0.2-0.5 mm) between mating surfaces; do not rely on\
 touching faces.

When a previous program exists, revise it directly for the requested change and keep its dimensions and\
 features unless the user asked to change them."""

_PLAN_RE = re.compile(r'#\s*plan-dimensions:\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)', re.I)
_EXPLANATION_RE = re.compile(r'#\s*explanation:\s*(.+)', re.I)
_COMPONENTS_RE = re.compile(r'#\s*components:\s*(.+)', re.I)
_CODE_FENCE_RE = re.compile(r'```[a-zA-Z]*\s*\n(.*?)\n?```', re.S)


def _slugify_component_id(name: str) -> str:
    slug=re.sub(r'[^a-z0-9]+','_',name.strip().lower()).strip('_')
    if not slug: slug='component'
    return slug[:64]


def _parse_components_header(text: str) -> list:
    """Parse a `# components:` header into [(name, [w,d,h]), ...] entries.

    Returns an empty list when no header is present. Each entry pairs a
    component name with its expected bounding-box dimensions in millimetres.
    """
    m = _COMPONENTS_RE.search(text)
    if not m:
        return []
    entries = []
    for raw in m.group(1).split(';'):
        raw = raw.strip()
        if not raw:
            continue
        if '@' not in raw:
            continue
        name, dims = raw.split('@', 1)
        name = name.strip()
        dim_vals = [float(x) for x in dims.split(',')]
        if len(dim_vals) != 3:
            continue
        entries.append((name, dim_vals))
    return entries


class CodegenError(RuntimeError):
    """Gemini script path failed; the caller must surface a plain error."""

    def __init__(self, code: str, message: str, history: Optional[list] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.history = history if history is not None else []


@dataclass
class ScriptRequest:
    prompt: str
    instruction: str = ''                # non-empty => revision of the object described by previous_*
    requested_dims: Optional[list] = None        # [w,d,h] outer bounds of the previous model
    previous_spec: Optional[dict] = None         # spec of a parametric parent (context only)
    previous_script: Optional[str] = None        # code of a script parent
    previous_structure: Optional[list] = None    # component summary of a parametric assembly parent
    recent_context: Optional[list] = None
    object_identity: Optional[str] = None
    intended_use: Optional[str] = None


@dataclass
class CodegenResult:
    code: str
    explanation: str
    attempts: int
    plan: list = field(default_factory=list)
    repair_history: list = field(default_factory=list)
    mesh: object = None
    cad_shape: object = None
    validation: dict = field(default_factory=dict)
    component_meshes: list = field(default_factory=list)


def _script_message(request: ScriptRequest, history: list) -> str:
    parts = [SCRIPT_INSTRUCTIONS, '\n\nUSER REQUEST\n']
    parts.append((request.instruction or request.prompt).strip())
    if request.instruction:
        parts.append('\nEXISTING PROGRAM (apply only the requested change; keep the rest):')
        if request.previous_script:
            parts.append(request.previous_script)
        elif request.previous_spec is not None:
            spec_summary = {'shape_type': request.previous_spec.get('shape_type'),
                            'dimensions_mm': request.previous_spec.get('dimensions_mm')}
            parts.append(json.dumps(spec_summary, ensure_ascii=False))
        structure = request.previous_structure or (request.previous_spec or {}).get('components') or []
        if structure:
            parts.append('\nEXISTING GEOMETRY SUMMARY (name, dimensions_mm, position_mm, rotation_deg):')
            parts.append(json.dumps(structure, ensure_ascii=False))
        if request.requested_dims:
            parts.append('\nPrevious outer bounds in mm (keep unless the user asks otherwise): %s'
                         % ','.join('%.2f' % float(axis) for axis in request.requested_dims))
    else:
        if request.object_identity:
            parts.append('Object identity: %s.' % request.object_identity)
        if request.intended_use:
            parts.append('Intended use: %s.' % request.intended_use)
    if request.recent_context:
        parts.append('\nRECENT CONVERSATION (context only):')
        parts.extend('%s: %s' % (item.get('role', '?'), str(item.get('text') or '')[:280])
                     for item in request.recent_context[-MAX_RECENT_CONTEXT:])
    if history:
        parts.append('\nPREVIOUS FAILED ATTEMPTS - fix the named problem; do not repeat it:')
        for index, item in enumerate(history, start=1):
            parts.append('Attempt %d:\n%s' % (index, json.dumps(item, ensure_ascii=False)))
    return '\n'.join(parts)


def _call_gemini(message: str) -> str:
    from google import genai
    from google.genai import types
    settings = get_settings()
    client = genai.Client(api_key=settings['gemini_api_key'], http_options=types.HttpOptions(timeout=60000))
    try:
        response = client.models.generate_content(
            model=settings['gemini_model'], contents=message,
            config=types.GenerateContentConfig(temperature=0, max_output_tokens=MAX_OUTPUT_TOKENS,
                                               thinking_config=types.ThinkingConfig(thinking_level='LOW')))
    except Exception as exc:
        raise CodegenError(_gemini_failure_category(exc) or 'request_failed', _gemini_failure_reason(exc)) from None
    text = (getattr(response, 'text', None) or '').strip()
    if not text:
        raise CodegenError('structured_response_invalid', 'Gemini returned no script text.')
    return text


def _parse_code_block(text: str) -> str:
    candidates = [chunk.strip() for chunk in _CODE_FENCE_RE.findall(text)]
    if not candidates:
        raise CodegenError('no_code_block', 'Gemini did not return a ```python fenced script.')
    best = max(candidates, key=len)
    code = best.strip()
    if 'result' not in code:
        raise CodegenError('no_result_line', 'The returned script never assigns a `result` variable.')
    if not code.startswith('#'):
        raise CodegenError('missing_plan_header',
                           'The script must start with a `# plan-dimensions:` comment line per the contract.')
    return code


def _parse_plan(code: str) -> list:
    match = _PLAN_RE.search(code)
    if not match:
        raise CodegenError('missing_plan_header',
                           'The script must include a `# plan-dimensions: <width>,<depth>,<height>` line.')
    dimensions = [float(match.group(i)) for i in (1, 2, 3)]
    if any(value <= 0 for value in dimensions):
        raise CodegenError('invalid_plan', 'Plan dimensions must all be positive.')
    if any(value > MAX_DIMENSION_MM for value in dimensions):
        raise CodegenError('invalid_plan', 'A plan dimension exceeds the 500 mm limit.')
    if not _EXPLANATION_RE.search(code):
        raise CodegenError('missing_explanation', 'The script must include an `# explanation:` comment.')
    return dimensions


def _import_brep(brep_path: str):
    cq = cadquery_module()
    shape = cq.importers.importBrep(brep_path)
    if shape is None:
        raise CodegenError('empty_shape', 'CadQuery imported an empty B-rep from the sandbox.')
    return shape.val() if hasattr(shape, 'val') else shape


def _attempt(request: ScriptRequest, history: list):
    message = _script_message(request, history)
    text = _call_gemini(message)
    code = _parse_code_block(text)
    plan = _parse_plan(code)
    brep_path, workdir = run_script(code)
    try:
        cad_shape = _import_brep(brep_path)
        cad_shape, mesh, validation = validate_from_cad_shape(
            cad_shape, requested=plan, tolerance=script_dimension_tolerance(plan))
        # Split a Compound into per-component meshes for storage/export.
        component_meshes=[]
        components_detail=[]
        try:
            solids=cad_shape.Solids()
        except Exception:
            solids=[]
        declared=_parse_components_header(code)
        if len(solids)>=2:
            used=set()
            for name,exp_dims in declared:
                best=None; best_score=None
                for idx,sb in enumerate(solids):
                    if idx in used: continue
                    try:
                        box=sb.BoundingBox()
                        sdims=[box.xlen,box.ylen,box.zlen]
                    except Exception:
                        continue
                    if all(abs(sdims[i]-exp_dims[i])<=script_dimension_tolerance(plan)*4 for i in range(3)):
                        center=((box.xmin+box.xmax)/2,(box.ymin+box.ymax)/2,(box.zmin+box.zmax)/2)
                        dist=sum((center[i]-0)**2 for i in range(3))**0.5
                        score=(dist,float(sb.Volume()))
                        if best_score is None or score<best_score:
                            best=idx; best_score=score
                if best is None:
                    continue
                used.add(best)
                solid=solids[best]
                try:
                    points,triangles=solid.tessellate(TESSELLATION_TOLERANCE_MM,ANGULAR_TOLERANCE_RAD)
                except Exception:
                    continue
                vertices=np.asarray([[point.x,point.y,point.z] for point in points],dtype=np.float64)
                faces=np.asarray(triangles,dtype=np.int64)
                comp_mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=True,validate=True)
                comp_mesh.merge_vertices(digits_vertex=5)
                comp_mesh.remove_unreferenced_vertices()
                comp_mesh.fix_normals()
                if comp_mesh.is_empty or not np.isfinite(comp_mesh.vertices).all():
                    continue
                comp_id=_slugify_component_id(name)
                component_meshes.append((comp_id,comp_mesh))
                try:
                    box=solid.BoundingBox()
                    dims=[box.xlen,box.ylen,box.zlen]
                    center=[(box.xmin+box.xmax)/2,(box.ymin+box.ymax)/2,(box.zmin+box.zmax)/2]
                    volume=float(solid.Volume())
                except Exception:
                    dims=exp_dims; center=[0,0,0]; volume=None
                components_detail.append({
                    'id':comp_id,'name':name,
                    'dimensions_mm':[round(d,4) for d in dims],
                    'position_mm':[round(c,4) for c in center],
                    'rotation_deg':[0.0,0.0,0.0],
                    'solid_count':1,
                    'volume_mm3':round(volume,4) if volume is not None else None,
                    'mesh_file':f'mesh_{comp_id}.npz',
                })
            # Any solids not matched by name get fallback IDs.
            for idx,solid in enumerate(solids):
                if idx in used:
                    continue
                try:
                    points,triangles=solid.tessellate(TESSELLATION_TOLERANCE_MM,ANGULAR_TOLERANCE_RAD)
                except Exception:
                    continue
                vertices=np.asarray([[point.x,point.y,point.z] for point in points],dtype=np.float64)
                faces=np.asarray(triangles,dtype=np.int64)
                comp_mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=True,validate=True)
                comp_mesh.merge_vertices(digits_vertex=5)
                comp_mesh.remove_unreferenced_vertices()
                comp_mesh.fix_normals()
                if comp_mesh.is_empty or not np.isfinite(comp_mesh.vertices).all():
                    continue
                comp_id=f'part_{idx+1}'
                component_meshes.append((comp_id,comp_mesh))
                components_detail.append({
                    'id':comp_id,'name':f'Part {idx+1}',
                    'dimensions_mm':[0.0,0.0,0.0],
                    'position_mm':[0.0,0.0,0.0],
                    'rotation_deg':[0.0,0.0,0.0],
                    'solid_count':1,
                    'volume_mm3':None,
                    'mesh_file':f'mesh_{comp_id}.npz',
                })
        validation['component_meshes_count']=len(component_meshes)
        if components_detail:
            validation['components_detail']=components_detail
    finally:
        cleanup_workdir(workdir)
    explanation = _EXPLANATION_RE.search(code)
    return (code, explanation.group(1).strip()[:160] if explanation else 'CadQuery script model.',
            cad_shape, mesh, validation, plan, component_meshes)


def build_script(request: ScriptRequest) -> CodegenResult:
    """Gemini -> runnable script; bounded sanitized repair loop; strict validation."""
    history: list = []
    last_error: CodegenError = CodegenError('request_failed', 'Script generation did not start.')
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            code, explanation, cad_shape, mesh, validation, plan, component_meshes = _attempt(request, history)
            return CodegenResult(code=code, explanation=explanation, attempts=attempt, plan=plan,
                                 repair_history=history, mesh=mesh, cad_shape=cad_shape, validation=validation,
                                 component_meshes=component_meshes)
        except CodegenError as exc:
            last_error = exc
            logger.info('codegen attempt %d failed code=%s', attempt, exc.code)
            if attempt < MAX_ATTEMPTS:
                history.append({'stage': 'response', 'code': exc.code, 'message': exc.message})
        except ScriptSandboxError as exc:
            logger.info('codegen attempt %d sandbox failed code=%s', attempt, exc.code)
            last_error = CodegenError(exc.code, exc.message)
            if attempt < MAX_ATTEMPTS:
                history.append({'stage': 'execution', 'code': exc.code,
                                'message': exc.message, 'detail': (exc.detail or '')[:300]})
        except ValueError as exc:
            message = str(exc)
            logger.info('codegen attempt %d validation failed: %s', attempt, message[:140])
            last_error = CodegenError('validation_failed', message)
            if attempt < MAX_ATTEMPTS:
                history.append({'stage': 'validation', 'message': message[:300]})
        except Exception as exc:
            logger.info('codegen attempt %d request failed category=%s', attempt, _gemini_failure_category(exc))
            last_error = CodegenError(_gemini_failure_category(exc) or 'request_failed', _gemini_failure_reason(exc))
            if attempt < MAX_ATTEMPTS:
                history.append({'stage': 'response', 'code': last_error.code, 'message': last_error.message})
    raise CodegenError(last_error.code, last_error.message, history=history)
