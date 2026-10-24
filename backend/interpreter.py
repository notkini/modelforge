"""Provider-independent prompt interpretation; no remote inference is used."""
import re
from .schemas import Spec, Dimensions

class ParseError(ValueError): pass

def parse_prompt(prompt: str) -> Spec:
    p = prompt.lower()
    nums = [float(x) for x in re.findall(r'(?<![a-z])\d+(?:\.\d+)?', p)]
    def match(pattern):
        m = re.search(pattern, p)
        return float(m.group(1)) if m else None
    wall = match(r'(?:wall(?:s| thickness)?|thickness)\s*(?:of|to|=)?\s*(\d+(?:\.\d+)?)\s*mm')
    if wall is None:
        wall = match(r'(\d+(?:\.\d+)?)\s*mm\s*(?:walls?|wall thickness)')
    dia = match(r'(?:diameter|dia\.?|⌀)\s*(?:of|=)?\s*(\d+(?:\.\d+)?)\s*mm')
    height = match(r'(?:height|tall|taller)\s*(?:of|=)?\s*(\d+(?:\.\d+)?)\s*mm')
    xyz = re.search(r'(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)',p)
    if 'cylinder' in p or 'tube' in p:
        if dia is None and nums: dia=nums[0]
        if height is None and len(nums)>1: height=nums[-1]
        if not dia or not height: raise ParseError('Please specify a diameter and height in millimetres, for example “cylinder, 20 mm diameter and 50 mm tall”.')
        shape='tube' if 'hollow' in p or 'tube' in p else 'cylinder'
        d=Dimensions(width=dia,depth=dia,height=height)
    elif 'enclosure' in p or 'box' in p:
        if xyz: w,dep,h=map(float,xyz.groups())
        elif len(nums)>=3: w,dep,h=nums[:3]
        else: raise ParseError('Please provide enclosure dimensions as width × depth × height in millimetres.')
        shape='enclosure' if 'enclosure' in p or wall else ('rounded_box' if 'round' in p else 'box')
        d=Dimensions(width=w,depth=dep,height=h)
    elif 'sphere' in p:
        r=match(r'(?:diameter|dia)\s*(\d+(?:\.\d+)?)') or (nums[0] if nums else None)
        if not r: raise ParseError('Please specify the sphere diameter in millimetres.')
        shape='sphere'; d=Dimensions(width=r,depth=r,height=r)
    elif 'cone' in p:
        if len(nums)<2: raise ParseError('Please specify the cone diameter and height in millimetres.')
        shape='cone'; d=Dimensions(width=nums[0],depth=nums[0],height=nums[-1])
    elif 'organic' in p or 'blob' in p or 'creature' in p:
        if len(nums)>=3 and xyz: w,dep,h=map(float,xyz.groups())
        elif len(nums)>=2: w=nums[0]; h=nums[-1]; dep=w
        else: raise ParseError('Please give at least a width and height for the organic shape.')
        shape='organic'; d=Dimensions(width=w,depth=dep,height=h)
    else:
        raise ParseError('I could not identify a supported shape. Try a cylinder, box, enclosure, sphere, cone, tube, or organic shape.')
    corner=match(r'(?:corner radius|edge radius|radius)\s*(?:of|=)?\s*(\d+(?:\.\d+)?)\s*mm')
    if 'round' in p and shape=='enclosure' and corner is None: corner=min(d.width,d.depth,d.height)*0.05
    return Spec(shape_type=shape,dimensions_mm=d,wall_thickness=wall,corner_radius=corner if 'round' in p else None)

