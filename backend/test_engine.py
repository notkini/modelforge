import pytest
from sqlalchemy import select
from .db_models import Conversation, GeneratedModel
from .geometry import generate
from .interpreter import ParseError, parse_prompt

def test_parse_cylinder_dimensions():
    spec=parse_prompt('Create a cylinder with a 20 mm diameter and 50 mm height')
    assert (spec.dimensions_mm.width,spec.dimensions_mm.height)==(20,50)

def test_parse_enclosure():
    spec=parse_prompt('Make a 100 x 60 x 30 mm enclosure with 2 mm walls')
    assert spec.shape_type=='enclosure' and spec.wall_thickness==2

def test_missing_dimension_rejected():
    with pytest.raises(ParseError): parse_prompt('Make a cylinder')

def test_cylinder_geometry_and_stl_glb_export(client,database):
    conversation=client.post('/api/conversations',json={'title':'Cylinder'}).json()
    client.post(f"/api/conversations/{conversation['id']}/messages",json={'role':'user','text':'Create a cylinder 20 mm diameter and 50 mm tall.'})
    response=client.post('/api/models/generate',json={'conversation_id':conversation['id'],'prompt':'Create a cylinder 20 mm diameter and 50 mm tall.','interpreter':'local'})
    assert response.status_code==200,response.text
    model=response.json(); assert abs(max(v[2] for v in model['vertices'])-min(v[2] for v in model['vertices'])-50)<.1
    for fmt in ('stl','glb'):
        exported=client.get(f"/api/models/{model['model_id']}/export?format={fmt}")
        assert exported.status_code==200 and len(exported.content)>100
    with database['factory']() as db:
        row=db.scalar(select(GeneratedModel).where(GeneratedModel.id==model['model_id']))
        assert row.owner_id==database['user'].id and row.storage_key

def test_organic_target_dimensions():
    spec=parse_prompt('organic blob 80 mm wide and 60 mm tall')
    _,validation=generate(spec)
    assert validation['dimensions_mm']==[80.0,80.0,60.0]

def test_bad_wall_thickness():
    with pytest.raises(ValueError):
        spec=parse_prompt('100 x 60 x 30 mm enclosure with 20 mm walls')
        generate(spec)

def test_refinement_creates_parented_version(client,database):
    conversation=client.post('/api/conversations',json={'title':'Cylinder'}).json()
    made=client.post('/api/models/generate',json={'conversation_id':conversation['id'],'prompt':'20 mm diameter cylinder, 50 mm height','interpreter':'local'}).json()
    response=client.post(f"/api/models/{made['model_id']}/refine",json={'instruction':'Make it 10 mm taller','interpreter':'local'})
    assert response.status_code==200,response.text
    revised=response.json(); assert revised['spec']['dimensions_mm']['height']==60
    assert revised['parent_version_id']==made['model_id'] and revised['version']==2
    with database['factory']() as db:
        rows=db.scalars(select(GeneratedModel).where(GeneratedModel.conversation_id==conversation['id']).order_by(GeneratedModel.version)).all()
        assert len(rows)==2 and rows[1].parent_version_id==rows[0].id

def test_inspector_generation_creates_parented_cad_version(client,database):
    conversation=client.post('/api/conversations',json={'title':'Inspector version'}).json()
    first=client.post('/api/models/generate',json={'conversation_id':conversation['id'],'prompt':'20 mm diameter cylinder, 50 mm height','interpreter':'local'}).json()
    spec=first['spec']; spec['dimensions_mm']['height']=55
    updated=client.post('/api/models/generate',json={'conversation_id':conversation['id'],'parent_model_id':first['model_id'],'spec':spec})
    assert updated.status_code==200,updated.text
    result=updated.json(); assert result['parent_version_id']==first['model_id'] and result['version']==2
    assert result['generation_method']=='parametric_cad' and result['validation']['cad_solid_valid']
    with database['factory']() as db:
        row=db.scalar(select(GeneratedModel).where(GeneratedModel.id==result['model_id']))
        assert row.parent_version_id==first['model_id'] and row.parameters_json['dimensions_mm']['height']==55

def test_inspector_rejects_parent_from_another_users_account(client,database):
    conversation=client.post('/api/conversations',json={'title':'Private parent'}).json()
    made=client.post('/api/models/generate',json={'conversation_id':conversation['id'],'prompt':'20 mm diameter cylinder, 50 mm height','interpreter':'local'}).json()
    client.test_identity['user']=database['other']
    own_conversation=client.post('/api/conversations',json={'title':'Other account'}).json()
    denied=client.post('/api/models/generate',json={'conversation_id':own_conversation['id'],'parent_model_id':made['model_id'],'spec':made['spec']})
    assert denied.status_code==404

def test_conversation_pagination_and_delete_confirmation_endpoint(client):
    for i in range(3): client.post('/api/conversations',json={'title':f'Project {i}'})
    page=client.get('/api/conversations?limit=2&offset=0').json()
    assert page['total']==3 and len(page['items'])==2

