"""Provider-independent prompt interpretation; no remote inference is used.

Improvements (Priority 5):
- Unit normalization: mm, cm, m, inch/in/inches → mm.
- Expanded synonym support: rectangular block→box, washer/ring→tube,
  shaft/disk/disc/rod→cylinder, bushing/spacer (no bore) → cylinder,
  hollow cylinder→tube, pipe→tube.
- Reordered dimension phrases ("50 mm tall, 20 mm in diameter").
- Dimension formats: "20mm-diameter", "dia=20mm", "dia 20mm".
- Missing-dimension messages name the specific absent value.
"""
import re
from .schemas import Spec, Dimensions

class ParseError(ValueError): pass

# ---------------------------------------------------------------------------
# Unit normalization
# ---------------------------------------------------------------------------

_UNIT_RE = re.compile(
    r'([\d]+(?:\.[\d]+)?)\s*'
    r'(millimetre?s?|millimeter?s?|mm|centimetre?s?|centimeter?s?|cm|metres?|meters?|m\b|inch(?:es)?|in\b|")',
    re.I,
)

_TO_MM = {
    'mm': 1.0, 'millimetre': 1.0, 'millimetres': 1.0,
    'millimeter': 1.0, 'millimeters': 1.0,
    'cm': 10.0, 'centimetre': 10.0, 'centimetres': 10.0,
    'centimeter': 10.0, 'centimeters': 10.0,
    'm': 1000.0, 'metre': 1000.0, 'metres': 1000.0,
    'meter': 1000.0, 'meters': 1000.0,
    'inch': 25.4, 'inches': 25.4, 'in': 25.4, '"': 25.4,
}


def _normalize_units(text: str) -> str:
    """Replace every `<value> <unit>` phrase with `<value_in_mm> mm`."""
    def replace(m):
        value = float(m.group(1))
        unit_key = m.group(2).lower().rstrip('s') if m.group(2).lower() not in _TO_MM else m.group(2).lower()
        # Try exact key first, then singular form
        factor = _TO_MM.get(m.group(2).lower()) or _TO_MM.get(unit_key, 1.0)
        converted = value * factor
        return f'{converted:g} mm'
    return _UNIT_RE.sub(replace, text)


# ---------------------------------------------------------------------------
# Synonym → canonical shape token
# ---------------------------------------------------------------------------

_SHAPE_SYNONYMS = [
    # Must be checked before shorter substrings that may overlap
    (r'\bhollow\s+cylinder\b',          'tube'),
    (r'\bhollow\s+tube\b',              'tube'),
    (r'\bannular\s+(?:ring|shape)\b',   'tube'),
    (r'\bpipe\b',                       'tube'),
    (r'\bwasher\b',                     'tube'),   # ring-like, needs OD+ID+H
    (r'\bring\b',                       'tube'),
    (r'\brectangular\s+block\b',        'box'),
    (r'\brectangular\s+prism\b',        'box'),
    (r'\bcuboid\b',                     'box'),
    (r'\bcube\b',                       'box'),
    (r'\bblock\b',                      'box'),
    (r'\bcylindrical\b',                'cylinder'),  # "a cylindrical part"
    (r'\bshaft\b',                      'cylinder'),
    (r'\brod\b',                        'cylinder'),
    (r'\bpin\b',                        'cylinder'),
    (r'\bdisc\b',                       'cylinder'),
    (r'\bdisk\b',                       'cylinder'),
    (r'\bpillar\b',                     'cylinder'),
    (r'\bpeg\b',                        'cylinder'),
    (r'\bblobby\b',                     'organic'),
    (r'\bamorphous\b',                  'organic'),
]


def _canonicalize_shape(text: str) -> str:
    """Replace synonym phrases with canonical shape tokens."""
    for pattern, replacement in _SHAPE_SYNONYMS:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


# ---------------------------------------------------------------------------
# Dimension extraction helpers
# ---------------------------------------------------------------------------

def _num(pattern: str, text: str):
    """Return the first numeric match for pattern in text, or None."""
    m = re.search(pattern, text, re.I)
    return float(m.group(1)) if m else None


# Extended diameter patterns: "diameter 20 mm", "dia=20 mm", "dia 20mm",
#   "⌀20 mm", "20mm-diameter", "d=20mm"
_DIA_PATTERNS = [
    r'(?:diameter|dia\.?|⌀|diam\.?)\s*[=:–\-]?\s*(\d+(?:\.\d+)?)\s*mm',
    r'(\d+(?:\.\d+)?)\s*mm\s*(?:diameter|dia\.?|diam\.?)',
    r'(\d+(?:\.\d+)?)\s*mm[-\s]*dia(?:meter)?',
    r'\bd\s*=\s*(\d+(?:\.\d+)?)\s*mm',
    r'\boutside\s+(?:diameter|dia)\s*[=:–]?\s*(\d+(?:\.\d+)?)\s*mm',
    r'\bouter\s+(?:diameter|dia)\s*[=:–]?\s*(\d+(?:\.\d+)?)\s*mm',
    r'\bod\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm',
]

_INNER_DIA_PATTERNS = [
    r'(?:inner|bore|internal|inside)\s+(?:diameter|dia)\s*[=:–]?\s*(\d+(?:\.\d+)?)\s*mm',
    r'\bid\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm',
    r'(\d+(?:\.\d+)?)\s*mm\s+(?:bore|inner(?:\s+dia(?:meter)?)?|inside(?:\s+dia(?:meter)?)?)',
    r'wall\s+thickness\s*[=:–]?\s*(\d+(?:\.\d+)?)\s*mm',  # fallback: derive OD-2*wall
]

_HEIGHT_PATTERNS = [
    r'(?:height|tall|high|length|long|taller|higher)\s*[=:–]?\s*(\d+(?:\.\d+)?)\s*mm',
    r'(\d+(?:\.\d+)?)\s*mm\s*(?:tall(?:er)?|high(?:er)?|in\s+height|height|length|long)',
    r'\bh\s*=\s*(\d+(?:\.\d+)?)\s*mm',
]

_WALL_PATTERNS = [
    r'(?:wall(?:s?(?:\s+thickness)?)?|thickness)\s*(?:of|to|=)?\s*(\d+(?:\.\d+)?)\s*mm',
    r'(\d+(?:\.\d+)?)\s*mm\s*(?:walls?|wall\s+thickness)',
    r'\bwt\s*=\s*(\d+(?:\.\d+)?)\s*mm',
]

_WIDTH_PATTERNS = [
    r'(?:width|wide)\s*[=:–]?\s*(\d+(?:\.\d+)?)\s*mm',
    r'(\d+(?:\.\d+)?)\s*mm\s*(?:wide|width)',
    r'\bw\s*=\s*(\d+(?:\.\d+)?)\s*mm',
]

_DEPTH_PATTERNS = [
    r'(?:depth|deep)\s*[=:–]?\s*(\d+(?:\.\d+)?)\s*mm',
    r'(\d+(?:\.\d+)?)\s*mm\s*(?:deep|depth)',
]

_RADIUS_PATTERNS = [
    r'(?:radius|rad)\s*[=:–]?\s*(\d+(?:\.\d+)?)\s*mm',
    r'(\d+(?:\.\d+)?)\s*mm\s*(?:radius)',
    r'\br\s*=\s*(\d+(?:\.\d+)?)\s*mm',
]

_CORNER_RADIUS_PATTERNS = [
    r'(?:corner\s+radius|edge\s+radius|fillet)\s*(?:of|=)?\s*(\d+(?:\.\d+)?)\s*mm',
]


def _first_match(patterns: list, text: str):
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return float(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_prompt(prompt: str) -> Spec:
    """Parse a natural-language prompt into a Spec.

    Processing pipeline:
    1. Normalize units → everything becomes mm.
    2. Canonicalize synonym phrases → standard shape tokens.
    3. Extract dimensions with multi-pattern matchers.
    4. Fall back to bare number extraction when explicit patterns miss.
    5. Validate and construct the Spec.
    """
    normalized = _normalize_units(prompt)
    p = _canonicalize_shape(normalized).lower()

    # Bare numbers (as fallback; extracted after unit normalization so
    # they are already in mm after the substitution above)
    nums = [float(x) for x in re.findall(r'(?<![a-z])\d+(?:\.\d+)?(?=\s*mm)', p)]
    if not nums:
        nums = [float(x) for x in re.findall(r'(?<![a-z])\d+(?:\.\d+)?', p)]

    # WxDxH shorthand
    xyz = re.search(r'(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)', p)

    # Shared dimension extractions
    dia = _first_match(_DIA_PATTERNS, p)
    if dia is None:
        # diameter from radius
        r_val = _first_match(_RADIUS_PATTERNS, p)
        if r_val:
            dia = r_val * 2
    height = _first_match(_HEIGHT_PATTERNS, p)
    wall = _first_match(_WALL_PATTERNS, p)
    corner = _first_match(_CORNER_RADIUS_PATTERNS, p)

    # -----------------------------------------------------------------------
    # Shape dispatch
    # -----------------------------------------------------------------------
    if 'cylinder' in p:
        return _parse_cylinder(p, nums, dia, height, wall, corner)

    if 'tube' in p:
        return _parse_tube(p, nums, dia, height, wall)

    if 'box' in p or 'enclosure' in p:
        return _parse_box_or_enclosure(p, nums, xyz, wall, corner)

    if 'sphere' in p:
        return _parse_sphere(p, nums, dia)

    if 'cone' in p:
        return _parse_cone(p, nums, dia, height)

    if 'organic' in p or 'blob' in p or 'creature' in p:
        return _parse_organic(p, nums, xyz)

    raise ParseError(
        'I could not identify a supported shape. '
        'Try: cylinder, tube, box/block, enclosure, sphere, cone, '
        'washer/ring, shaft/rod/disk, or organic.'
    )


# ---------------------------------------------------------------------------
# Per-shape parsers
# ---------------------------------------------------------------------------

def _parse_cylinder(p, nums, dia, height, wall, corner):
    if dia is None and nums:
        dia = nums[0]
    if height is None and len(nums) > 1:
        height = nums[-1]
    missing = []
    if not dia:
        missing.append('diameter')
    if not height:
        missing.append('height')
    if missing:
        raise ParseError(
            f'Please specify a {" and ".join(missing)} in millimetres — '
            f'for example "cylinder, 20 mm diameter and 50 mm tall".'
        )
    d = Dimensions(width=dia, depth=dia, height=height)
    return Spec(shape_type='cylinder', dimensions_mm=d,
                fillet_radius=None, chamfer_distance=None,
                corner_radius=corner if corner and 'round' in p else None)


def _parse_tube(p, nums, dia, height, wall):
    # Tube / hollow cylinder / washer / ring / pipe
    # Try inner diameter
    inner_dia = _first_match(_INNER_DIA_PATTERNS, p)

    if dia is None and nums:
        dia = nums[0]
    if height is None and len(nums) > 1:
        height = nums[-1]

    # wall_thickness: prefer explicit, else derive from inner diameter
    if wall is None and inner_dia is not None and dia is not None:
        wall = (dia - inner_dia) / 2

    # If only two distinct numbers and no explicit dia/height, treat as
    # diameter then height
    missing = []
    if not dia:
        missing.append('outer diameter')
    if not height:
        missing.append('height')
    if missing:
        raise ParseError(
            f'Please specify a {" and ".join(missing)} in millimetres — '
            f'for example "tube, 30 mm outer diameter and 50 mm tall".'
        )
    d = Dimensions(width=dia, depth=dia, height=height)
    return Spec(shape_type='tube', dimensions_mm=d, wall_thickness=wall)


def _parse_box_or_enclosure(p, nums, xyz, wall, corner):
    if xyz:
        w, dep, h = map(float, xyz.groups())
    else:
        width_val = _first_match(_WIDTH_PATTERNS, p)
        depth_val = _first_match(_DEPTH_PATTERNS, p)
        height_val = _first_match(_HEIGHT_PATTERNS, p)
        if width_val and depth_val and height_val:
            w, dep, h = width_val, depth_val, height_val
        elif len(nums) >= 3:
            w, dep, h = nums[0], nums[1], nums[2]
        else:
            raise ParseError(
                'Please provide dimensions as width × depth × height in millimetres, '
                'for example "100 x 60 x 30 mm box".'
            )
    is_enclosure = 'enclosure' in p or wall is not None
    shape = 'enclosure' if is_enclosure else ('rounded_box' if 'round' in p else 'box')
    d = Dimensions(width=w, depth=dep, height=h)
    # corner_radius: only for rounded_box/enclosure
    effective_corner = corner
    if 'round' in p and effective_corner is None and shape in ('rounded_box', 'enclosure'):
        effective_corner = min(w, dep, h) * 0.05
    return Spec(
        shape_type=shape, dimensions_mm=d, wall_thickness=wall,
        corner_radius=effective_corner if shape in ('rounded_box', 'enclosure') else None,
    )


def _parse_sphere(p, nums, dia):
    if dia is None and nums:
        dia = nums[0]
    # Also handle radius → diameter
    if dia is None:
        raise ParseError('Please specify the sphere diameter in millimetres.')
    d = Dimensions(width=dia, depth=dia, height=dia)
    return Spec(shape_type='sphere', dimensions_mm=d)


def _parse_cone(p, nums, dia, height):
    if dia is None and nums:
        dia = nums[0]
    if height is None and len(nums) > 1:
        height = nums[-1]
    missing = []
    if not dia:
        missing.append('base diameter')
    if not height:
        missing.append('height')
    if missing:
        raise ParseError(
            f'Please specify a {" and ".join(missing)} in millimetres — '
            f'for example "cone, 40 mm diameter and 60 mm tall".'
        )
    d = Dimensions(width=dia, depth=dia, height=height)
    return Spec(shape_type='cone', dimensions_mm=d)


def _parse_organic(p, nums, xyz):
    if xyz:
        w, dep, h = map(float, xyz.groups())
    elif len(nums) >= 3:
        w, dep, h = nums[0], nums[1], nums[2]
    elif len(nums) >= 2:
        w = nums[0]; h = nums[-1]; dep = w
    else:
        raise ParseError('Please give at least a width and height for the organic shape.')
    d = Dimensions(width=w, depth=dep, height=h)
    return Spec(shape_type='organic', dimensions_mm=d)
