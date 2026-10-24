import hashlib, json, logging, secrets, time, uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import numpy as np
import trimesh
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import Response as RawResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.auth.exceptions import TransportError as GoogleTransportError
from pydantic import field_validator
from google.oauth2 import id_token
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from .auth import (CSRF_COOKIE, NONCE_COOKIE, SESSION_COOKIE, _digest, cookie_settings,
                   csrf_protected, current_user, hash_password, issue_session, user_payload, verify_password)
from .config import get_settings
from .database import get_db
from .db_models import AuthNonce, AuthSession, Conversation, GeneratedModel, Message, User, now_utc
from .geometry import generate_with_kernel
from .import codegen
from .cad_geometry import save_step
from .interpreter import ParseError
from .nlp import InterpretationResult, UnsupportedGeometry, interpret
from .script_runner import ScriptSandboxError
from .schemas import (ConversationCreate, ConversationPatch, GenerateRequest, InterpretRequest,
                      LocalImportRequest, MessageCreate, ParseRequest, RefineRequest, Spec)

router=APIRouter(prefix='/api')
logger=logging.getLogger(__name__)
_rate_windows=defaultdict(deque)

def rate_limit(request: Request, category: str, maximum: int, window: int=60):
    now=time.monotonic(); key=(category,request.client.host if request.client else 'unknown')
    items=_rate_windows[key]
    while items and items[0] < now-window: items.popleft()
    if len(items)>=maximum: raise HTTPException(429,detail={'message':'Too many requests. Please wait a moment and try again.'},headers={'Retry-After':str(window)})
    items.append(now)

def csrf_cookie(response: Response, value: str):
    response.set_cookie(CSRF_COOKIE,value,httponly=False,secure=get_settings()['secure_cookies'],samesite='strict',path='/',max_age=SESSION_DAYS_SECONDS)

SESSION_DAYS_SECONDS=14*24*60*60

@router.get('/health')
def health():
    s=get_settings()
    from .cad_geometry import cad_available
    return {'status':'ok','engine':'CadQuery/OpenCASCADE + Gaussian implicit meshes','cadquery_available':cad_available(),'gemini_configured':bool(s['gemini_api_key']),'gemini_model':s['gemini_model'] if s['gemini_api_key'] else None}

def verify_google_token(token: str, audience: str):
    return id_token.verify_oauth2_token(token,GoogleRequest(),audience)

@router.get('/auth/config')
def auth_config(response: Response, request: Request, db: Session=Depends(get_db)):
    settings=get_settings()
    csrf=request.cookies.get(CSRF_COOKIE)
    session_raw=request.cookies.get(SESSION_COOKIE)
    session=db.query(AuthSession).filter(AuthSession.token_hash==_digest(session_raw)).first() if session_raw else None
    if not csrf or (session and not secrets.compare_digest(session.csrf_hash,_digest(csrf))):
        csrf=secrets.token_urlsafe(32)
        if session: session.csrf_hash=_digest(csrf); db.commit()
        csrf_cookie(response,csrf)
    nonce=secrets.token_urlsafe(32)
    db.add(AuthNonce(nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),csrf_hash=_digest(csrf),expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)))
    db.query(AuthNonce).filter(AuthNonce.expires_at < datetime.now(timezone.utc)).delete(synchronize_session=False)
    db.commit()
    response.set_cookie(NONCE_COOKIE,nonce,httponly=True,secure=settings['secure_cookies'],samesite='lax',path='/',max_age=300)
    return {'google_client_id':settings['google_client_id'] or None,'csrf_token':csrf,'nonce':nonce}

class GoogleLogin(BaseModel):
    credential: str = Field(min_length=50,max_length=10000)

class LocalRegister(BaseModel):
    name: str = Field(min_length=1,max_length=120)
    email: str = Field(min_length=3,max_length=320)
    password: str = Field(min_length=12,max_length=128)

    @field_validator('name')
    @classmethod
    def clean_name(cls,value):
        value=value.strip()
        if not value: raise ValueError('Enter your name.')
        return value

    @field_validator('email')
    @classmethod
    def clean_email(cls,value):
        value=value.strip().lower()
        import re
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",value): raise ValueError('Enter a valid email address.')
        return value

class LocalLogin(BaseModel):
    email: str = Field(min_length=3,max_length=320)
    password: str = Field(min_length=1,max_length=128)

    @field_validator('email')
    @classmethod
    def clean_email(cls,value): return value.strip().lower()

def _check_session_secret(settings):
    if len(settings['session_secret']) < 40 or settings['session_secret'].lower().startswith('replace-with-'):
        raise HTTPException(503,detail={'message':'Set SESSION_SECRET to a randomly generated value of at least 40 characters before enabling sign-in.'})

def _set_login_session(user: User, csrf: str, db: Session, response: Response):
    raw=issue_session(db,user,csrf)
    response.set_cookie(SESSION_COOKIE,raw,**cookie_settings(),max_age=SESSION_DAYS_SECONDS)
    return {'user':user_payload(user),'csrf_token':csrf}

@router.post('/auth/google')
def google_login(payload: GoogleLogin, request: Request, response: Response, _: None=Depends(csrf_protected), db: Session=Depends(get_db)):
    rate_limit(request,'auth',10)
    settings=get_settings(); client_id=settings['google_client_id']
    _check_session_secret(settings)
    if not client_id: raise HTTPException(503,detail={'message':'Google sign-in is not configured. Set GOOGLE_CLIENT_ID in the server environment.'})
    nonce=request.cookies.get(NONCE_COOKIE)
    if not nonce: raise HTTPException(403,detail={'message':'Sign-in challenge expired. Please try again.'})
    csrf=request.cookies.get(CSRF_COOKIE,'')
    try:
        claims=verify_google_token(payload.credential,client_id)
    except Exception as exc:
        # Log only the exception class. Never log the credential, claims, or exception text.
        logger.warning('Google ID token verification failed (%s)', type(exc).__name__)
        if isinstance(exc, GoogleTransportError):
            message='The backend could not reach Google to verify sign-in. Check server internet access, DNS, and system time, then try again.'
        elif isinstance(exc, ValueError):
            message='Google returned an invalid or expired credential, or one issued for a different client ID. Confirm the Web client ID and try again.'
        else:
            message='Google could not verify this sign-in credential. Check the backend log for the safe failure category, then try again.'
        raise HTTPException(401,detail={'message':message})
    if claims.get('aud') != client_id or claims.get('iss') not in ('accounts.google.com','https://accounts.google.com'):
        raise HTTPException(401,detail={'message':'Google token audience or issuer is invalid.'})
    if not secrets.compare_digest(str(claims.get('nonce','')),nonce):
        raise HTTPException(401,detail={'message':'Sign-in nonce validation failed. Refresh and try again.'})
    challenge=db.query(AuthNonce).filter(AuthNonce.nonce_hash==hashlib.sha256(nonce.encode()).hexdigest()).first()
    challenge_expiry=challenge.expires_at.replace(tzinfo=timezone.utc) if challenge and challenge.expires_at.tzinfo is None else (challenge.expires_at if challenge else None)
    if not challenge or challenge_expiry <= datetime.now(timezone.utc) or not secrets.compare_digest(challenge.csrf_hash,_digest(csrf)):
        raise HTTPException(401,detail={'message':'Sign-in challenge expired. Refresh and try again.'})
    sub=claims.get('sub'); email=claims.get('email')
    if not sub or not email or claims.get('email_verified') is not True:
        raise HTTPException(401,detail={'message':'Google account did not provide a verified email and subject.'})
    db.delete(challenge)
    user=db.query(User).filter(User.google_sub==str(sub)).first()
    if user is None:
        email_user=db.query(User).filter(func.lower(User.email)==str(email).strip().lower()).first()
        if email_user:
            raise HTTPException(409,detail={'message':'An account already uses this email. Sign in with its existing method; ModelForge will not link accounts by email alone.'})
        user=User(id=str(uuid.uuid4()),google_sub=str(sub),email=str(email),display_name=str(claims.get('name') or email)[:200],picture_url=claims.get('picture'),status='active')
        db.add(user)
    else:
        user.email=str(email); user.display_name=str(claims.get('name') or email)[:200]; user.picture_url=claims.get('picture'); user.last_login_at=now_utc()
    db.flush()
    login_response=_set_login_session(user,csrf,db,response)
    response.delete_cookie(NONCE_COOKIE,path='/',secure=settings['secure_cookies'],httponly=True,samesite='lax')
    return login_response

@router.post('/auth/register')
def register(payload: LocalRegister,request: Request,response: Response,_: None=Depends(csrf_protected),db: Session=Depends(get_db)):
    rate_limit(request,'auth',10); _check_session_secret(get_settings())
    if db.query(User).filter(func.lower(User.email)==payload.email).first():
        raise HTTPException(409,detail={'message':'An account already uses this email. Sign in with that account or use Google sign-in if it was registered there.'})
    user=User(id=str(uuid.uuid4()),google_sub=None,password_hash=hash_password(payload.password),email=payload.email,
              display_name=payload.name,picture_url=None,status='active')
    db.add(user); db.commit(); db.refresh(user)
    return _set_login_session(user,request.cookies[CSRF_COOKIE],db,response)

@router.post('/auth/login')
def local_login(payload: LocalLogin,request: Request,response: Response,_: None=Depends(csrf_protected),db: Session=Depends(get_db)):
    rate_limit(request,'auth',10); _check_session_secret(get_settings())
    user=db.query(User).filter(func.lower(User.email)==payload.email).first()
    if not user or user.status!='active' or not verify_password(payload.password,user.password_hash):
        raise HTTPException(401,detail={'message':'Email or password is incorrect.'})
    user.last_login_at=now_utc(); db.commit()
    return _set_login_session(user,request.cookies[CSRF_COOKIE],db,response)

@router.get('/auth/me')
def me(user: User=Depends(current_user)):
    return {'user':user_payload(user)}

@router.post('/auth/logout')
def logout(request: Request,response: Response,_: None=Depends(csrf_protected),db: Session=Depends(get_db)):
    raw=request.cookies.get(SESSION_COOKIE)
    if raw:
        session=db.query(AuthSession).filter(AuthSession.token_hash==_digest(raw)).first()
        if session: db.delete(session); db.commit()
    opts=cookie_settings()
    response.delete_cookie(SESSION_COOKIE,path='/',secure=opts['secure'],httponly=True,samesite=opts['samesite'])
    return {'ok':True}

def owned_conversation(db: Session, conversation_id: str, user: User):
    item=db.query(Conversation).filter(Conversation.id==conversation_id,Conversation.owner_id==user.id).first()
    if not item: raise HTTPException(404,detail={'message':'Conversation not found.'})
    return item

def _conversation_json(c: Conversation):
    return {'id':c.id,'title':c.title,'archived':c.archived,'created_at':c.created_at.isoformat(),'updated_at':c.updated_at.isoformat()}

@router.get('/conversations')
def list_conversations(limit: int=Query(30,ge=1,le=100),offset: int=Query(0,ge=0),include_archived: bool=False,db: Session=Depends(get_db),user: User=Depends(current_user)):
    q=db.query(Conversation).filter(Conversation.owner_id==user.id)
    if not include_archived: q=q.filter(Conversation.archived.is_(False))
    total=q.count(); rows=q.order_by(Conversation.updated_at.desc()).offset(offset).limit(limit).all()
    return {'items':[_conversation_json(c) for c in rows],'total':total,'limit':limit,'offset':offset}

@router.post('/conversations')
def create_conversation(payload: ConversationCreate,db: Session=Depends(get_db),user: User=Depends(current_user),_:None=Depends(csrf_protected)):
    c=Conversation(id=str(uuid.uuid4()),owner_id=user.id,title=payload.title.strip() or 'New creation'); db.add(c); db.commit(); return _conversation_json(c)

def model_snapshot(model: GeneratedModel):
    return {'model_id':model.id,'spec':model.parameters_json,'validation':model.validation_json,'generation_method':model.generation_method,'version':model.version,'parent_version_id':model.parent_version_id}

@router.get('/conversations/{conversation_id}')
def get_conversation(conversation_id: str,db: Session=Depends(get_db),user: User=Depends(current_user)):
    c=owned_conversation(db,conversation_id,user)
    messages=[]
    for m in c.messages:
        data={'id':m.id,'role':m.role,'text':m.text,'created_at':m.created_at.isoformat()}
        if m.metadata_json: data['metadata']=m.metadata_json
        mid=(m.metadata_json or {}).get('model_id') if m.metadata_json else None
        if mid:
            model=db.query(GeneratedModel).filter(GeneratedModel.id==mid,GeneratedModel.owner_id==user.id).first()
            if model: data['model']=model_snapshot(model)
        messages.append(data)
    return {'conversation':_conversation_json(c),'messages':messages}

@router.patch('/conversations/{conversation_id}')
def patch_conversation(conversation_id: str,payload: ConversationPatch,db: Session=Depends(get_db),user: User=Depends(current_user),_:None=Depends(csrf_protected)):
    c=owned_conversation(db,conversation_id,user)
    if payload.title is not None: c.title=payload.title.strip()
    if payload.archived is not None: c.archived=payload.archived
    c.updated_at=now_utc(); db.commit(); return _conversation_json(c)

@router.delete('/conversations/{conversation_id}')
def delete_conversation(conversation_id: str,db: Session=Depends(get_db),user: User=Depends(current_user),_:None=Depends(csrf_protected)):
    c=owned_conversation(db,conversation_id,user)
    for model in db.query(GeneratedModel).filter(GeneratedModel.conversation_id==c.id).all(): _remove_artifact(model,user)
    db.delete(c); db.commit(); return {'ok':True}

@router.post('/conversations/{conversation_id}/messages')
def create_message(conversation_id: str,payload: MessageCreate,db: Session=Depends(get_db),user: User=Depends(current_user),_:None=Depends(csrf_protected)):
    c=owned_conversation(db,conversation_id,user)
    m=Message(id=str(uuid.uuid4()),conversation_id=c.id,role=payload.role,text=payload.text,metadata_json=payload.metadata)
    c.updated_at=now_utc(); db.add(m); db.commit()
    return {'id':m.id,'role':m.role,'text':m.text,'created_at':m.created_at.isoformat()}

@router.post('/conversations/import-local')
def import_local(payload: LocalImportRequest,db: Session=Depends(get_db),user: User=Depends(current_user),_:None=Depends(csrf_protected)):
    imported=0
    for item in payload.conversations:
        existing=db.query(Conversation).filter(Conversation.owner_id==user.id,Conversation.client_import_id==item.id).first()
        if existing: continue
        c=Conversation(id=str(uuid.uuid4()),owner_id=user.id,title=item.title[:240] or 'Imported conversation',client_import_id=item.id)
        db.add(c); db.flush()
        for msg in item.messages:
            # Keep only small, JSON-safe import metadata. Mesh arrays stay in the original browser record.
            meta=msg.metadata
            if meta:
                meta={k:v for k,v in meta.items() if k in ('local_model_id','shape_type','dimensions_mm','generation_method','validation')}
            db.add(Message(id=str(uuid.uuid4()),conversation_id=c.id,role=msg.role,text=msg.text,metadata_json=meta))
        imported+=1
    db.commit(); return {'imported':imported,'total':len(payload.conversations)}

def _artifact_folder(user: User, model: GeneratedModel) -> Path:
    root=get_settings()['artifact_dir']
    folder=(root/user.id/model.id).resolve()
    if root not in folder.parents: raise HTTPException(404,detail={'message':'Model artifact not found.'})
    return folder

def _save_mesh(user: User, model_id: str, mesh: trimesh.Trimesh, cad_shape=None) -> str:
    folder=(get_settings()['artifact_dir']/user.id/model_id).resolve()
    folder.mkdir(parents=True,exist_ok=True)
    if get_settings()['artifact_dir'] not in folder.parents: raise ValueError('Invalid private artifact location.')
    np.savez_compressed(folder/'mesh.npz',vertices=mesh.vertices.astype(np.float32),faces=mesh.faces.astype(np.int32))
    if cad_shape is not None: save_step(cad_shape,folder/'model.step')
    return model_id

def _load_mesh(user: User,model: GeneratedModel) -> trimesh.Trimesh:
    folder=_artifact_folder(user,model)
    path=folder/'mesh.npz'
    if not model.storage_key or not path.is_file(): raise HTTPException(410,detail={'message':'This imported model has metadata only; its original mesh is not available for preview or export.'})
    try:
        data=np.load(path,allow_pickle=False)
        mesh=trimesh.Trimesh(vertices=data['vertices'],faces=data['faces'],process=True)
        data.close()
        if len(mesh.vertices)>2_000_000 or len(mesh.faces)>4_000_000: raise ValueError('Stored mesh exceeds limits.')
        return mesh
    except Exception as exc: raise HTTPException(422,detail={'message':'Stored model artifact is invalid.'}) from exc

def _remove_artifact(model: GeneratedModel,user: User):
    folder=_artifact_folder(user,model)
    if folder.exists():
        for p in folder.iterdir():
            if p.is_file(): p.unlink()
        folder.rmdir()

def _model_json(model_id: str,spec,mesh: trimesh.Trimesh,validation: dict,method: str,version: int,parent: Optional[str],include_mesh=True):
    parameters=spec.model_dump() if isinstance(spec,Spec) else dict(spec)
    result={'model_id':model_id,'spec':parameters,'validation':validation,'generation_method':method,'version':version,'parent_version_id':parent}
    if include_mesh: result.update({'vertices':mesh.vertices.tolist(),'faces':mesh.faces.tolist()})
    return result

def _resolve_interpreter(interpreter: Optional[str]) -> str:
    if not interpreter or interpreter=='auto':
        return 'gemini' if get_settings()['gemini_api_key'] else 'local'
    if interpreter not in ('gemini','local'):
        raise HTTPException(422,detail={'message':"Unrecognized interpreter selection: use 'auto', 'gemini', or 'local'."})
    return interpreter

SANDBOX_CODES_503={'timeout','cpu_limit','memory_limit','internal_error','startup_failed','unsupported_platform','script_too_large','invalid_payload'}

def _codegen_http_error(exc: codegen.CodegenError) -> HTTPException:
    status=503 if exc.code in SANDBOX_CODES_503 else 422
    return HTTPException(status,detail={'message':exc.message})

def _create_model(db: Session,user: User,spec: Spec,conversation: Conversation,prompt: str,provider: str,explanation: str,parent: Optional[GeneratedModel]=None,fallback_reason: Optional[str]=None):
    mesh,validation,cad_shape=generate_with_kernel(spec)
    version=(db.query(func.max(GeneratedModel.version)).filter(GeneratedModel.conversation_id==conversation.id).scalar() or 0)+1
    model_id=str(uuid.uuid4()); method='gaussian_implicit_mesh' if spec.shape_type=='organic' else 'parametric_cad'
    validation['generation_method']=method
    if spec.shape_type=='assembly':
        validation['component_count']=len(spec.components)
        validation['components']=[component.name for component in spec.components]
    if spec.object_identity: validation['object_identity']=spec.object_identity
    if spec.intended_use: validation['intended_use']=spec.intended_use
    validation['warnings']=list(spec.design_warnings)
    method_label={'parametric_cad':'parametric CAD','gaussian_implicit_mesh':'Gaussian implicit mesh'}.get(method,'local geometry')
    if provider=='gemini':
        parts=[p for p in (explanation,'Interpretation: Gemini.') if p]
    elif provider=='local-deterministic':
        parts=['Interpretation: local deterministic parser.']
    elif provider=='parameter-inspector':
        parts=['Parameters supplied by the inspector.']
    else:
        parts=[f'Interpretation provider: {provider}.']
    parts.append(f'Geometry generated locally using {method_label}.')
    if fallback_reason: parts.append(f'Gemini fallback: {fallback_reason}')
    assistant_text=' '.join(parts)
    metadata={'model_id':model_id,'provider':provider,'interpretation_provider':provider,
              'geometry_generation_method':method,'fallback_reason':fallback_reason}
    msg=Message(id=str(uuid.uuid4()),conversation_id=conversation.id,role='assistant',text=assistant_text,metadata_json=metadata)
    db.add(msg); db.flush()
    model=GeneratedModel(id=model_id,owner_id=user.id,conversation_id=conversation.id,message_id=msg.id,parent_version_id=parent.id if parent else None,shape_type=spec.shape_type,generation_method=method,parameters_json=spec.model_dump(),validation_json=validation,version=version,storage_key=model_id)
    db.add(model); conversation.updated_at=now_utc(); db.flush()
    _save_mesh(user,model_id,mesh,cad_shape); db.commit()
    return model,mesh,validation,assistant_text

def _persist_script_model(db: Session,user: User,conversation: Conversation,generation: codegen.CodegenResult,provider_label: str,prompt: str,parent: Optional[GeneratedModel]=None):
    method=codegen.SCRIPT_METHOD
    mesh=generation.mesh
    validation=dict(generation.validation)
    validation['generation_method']=method
    validation['requested_dimensions_mm']=generation.plan
    validation['script_attempts']=generation.attempts
    validation['warnings']=[]
    parameters={'shape_type':method,'creation_script':generation.code,'plan_dimensions_mm':generation.plan}
    version=(db.query(func.max(GeneratedModel.version)).filter(GeneratedModel.conversation_id==conversation.id).scalar() or 0)+1
    model_id=str(uuid.uuid4())
    status_text='' if generation.attempts==1 else ' after %d repair attempts'%generation.attempts
    if provider_label=='refinement':
        assistant_text=f'The model was refined{status_text}: Gemini revised and executed a new CadQuery script; geometry built locally.'
    else:
        assistant_text=f'Generated via a Gemini-written CadQuery model script{status_text}; geometry built and validated locally.'
    metadata={'model_id':model_id,'provider':'gemini','interpretation_provider':'gemini',
              'geometry_generation_method':method,'fallback_reason':None,
              'script_attempts':generation.attempts}
    msg=Message(id=str(uuid.uuid4()),conversation_id=conversation.id,role='assistant',text=assistant_text,metadata_json=metadata)
    db.add(msg); db.flush()
    model=GeneratedModel(id=model_id,owner_id=user.id,conversation_id=conversation.id,message_id=msg.id,
                         parent_version_id=parent.id if parent else None,
                         shape_type=method,generation_method=method,
                         parameters_json=parameters,validation_json=validation,
                         version=version,storage_key=model_id)
    db.add(model); conversation.updated_at=now_utc(); db.flush()
    _save_mesh(user,model_id,mesh,generation.cad_shape); db.commit()
    return model,mesh,validation,assistant_text

@router.post('/parse-prompt')
@router.post('/nlp/interpret')
def parse_prompt_api(payload: ParseRequest,request: Request,user: User=Depends(current_user),_:None=Depends(csrf_protected)):
    rate_limit(request,'nlp:'+user.id,20)
    try: result=interpret(payload.prompt,interpreter=payload.interpreter)
    except (ParseError,UnsupportedGeometry,ValueError) as exc: raise HTTPException(422,detail={'message':str(exc)})
    return {'spec':result.spec.model_dump() if result.spec else None,'provider':result.provider,'explanation':result.explanation,'needs_clarification':result.needs_clarification,'clarifying_question':result.clarifying_question,'fallback_reason':result.fallback_reason}

@router.post('/models/generate')
def create_model(payload: GenerateRequest,request: Request,db: Session=Depends(get_db),user: User=Depends(current_user),_:None=Depends(csrf_protected)):
    rate_limit(request,'generate:'+user.id,20)
    conversation=owned_conversation(db,payload.conversation_id,user) if payload.conversation_id else None
    if conversation is None: raise HTTPException(422,detail={'message':'Create a conversation before generating a model.'})
    parent=None
    if payload.parent_model_id:
        parent=db.query(GeneratedModel).filter(GeneratedModel.id==payload.parent_model_id,GeneratedModel.owner_id==user.id,GeneratedModel.conversation_id==conversation.id).first()
        if not parent: raise HTTPException(404,detail={'message':'Parent model not found in this conversation.'})
    choice=_resolve_interpreter(payload.interpreter)
    if payload.spec is None and choice=='gemini' and not get_settings()['gemini_api_key']:
        raise HTTPException(422,detail={'message':'Gemini interpretation is selected but GEMINI_API_KEY is not configured on the server.'})
    if payload.spec is None and choice=='gemini':
        return _generate_with_gemini_script(db,user,conversation,payload,parent)
    try:
        fallback_reason=None
        if payload.spec is None:
            result=interpret(payload.prompt,interpreter='local')
            if result.needs_clarification or result.spec is None:
                raise ParseError(result.clarifying_question or 'Please clarify the requested dimensions or shape.')
            spec=result.spec; provider=result.provider; explanation=result.explanation; fallback_reason=result.fallback_reason
        else:
            spec=payload.spec; provider='parameter-inspector'; explanation='Generated from validated ModelForge parameters.'
        model,mesh,validation,text=_create_model(db,user,spec,conversation,payload.prompt,provider,explanation,parent,fallback_reason)
        return {**_model_json(model.id,spec,mesh,validation,model.generation_method,model.version,model.parent_version_id),
                'message':text,'provider':provider,'interpretation_provider':provider,
                'geometry_generation_method':model.generation_method,'fallback_reason':fallback_reason}
    except RuntimeError as exc: db.rollback(); raise HTTPException(503,detail={'message':str(exc)})
    except (ValueError,UnsupportedGeometry) as exc: db.rollback(); raise HTTPException(422,detail={'message':str(exc)})

def _generate_with_gemini_script(db,user,conversation,payload,parent):
    recent=[{'role':m.role,'text':m.text[:1000]} for m in conversation.messages[-6:]]
    request=codegen.ScriptRequest(prompt=payload.prompt,recent_context=recent)
    try:
        generation=codegen.build_script(request)
        model,mesh,validation,text=_persist_script_model(db,user,conversation,generation,'generation',payload.prompt,parent)
        db.commit()
        return {**_model_json(model.id,model.parameters_json,mesh,validation,model.generation_method,model.version,model.parent_version_id),
                'message':text,'provider':'gemini','interpretation_provider':'gemini',
                'geometry_generation_method':codegen.SCRIPT_METHOD,'fallback_reason':None,
                'script_attempts':generation.attempts}
    except codegen.CodegenError as exc: db.rollback(); raise _codegen_http_error(exc)
    except RuntimeError as exc: db.rollback(); raise HTTPException(503,detail={'message':str(exc)})
    except (ValueError,UnsupportedGeometry) as exc: db.rollback(); raise HTTPException(422,detail={'message':str(exc)})

@router.post('/models/{model_id}/refine')
def refine_model(model_id: str,payload: RefineRequest,request: Request,db: Session=Depends(get_db),user: User=Depends(current_user),_:None=Depends(csrf_protected)):
    rate_limit(request,'refine:'+user.id,20)
    old=db.query(GeneratedModel).filter(GeneratedModel.id==model_id,GeneratedModel.owner_id==user.id).first()
    if not old: raise HTTPException(404,detail={'message':'Model not found.'})
    conversation=owned_conversation(db,old.conversation_id,user)
    choice=_resolve_interpreter(payload.interpreter)
    if choice=='gemini' and not get_settings()['gemini_api_key']:
        raise HTTPException(422,detail={'message':'Gemini interpretation is selected but GEMINI_API_KEY is not configured on the server.'})
    recent=[{'role':m.role,'text':m.text[:1000]} for m in conversation.messages[-6:]]
    if old.generation_method==codegen.SCRIPT_METHOD:
        if choice!='gemini':
            raise HTTPException(422,detail={'message':'This model was created from a Gemini CadQuery script; refinements require the Gemini interpreter.'})
        return _refine_script_model(db,user,conversation,old,payload,recent)
    old_spec=Spec.model_validate(old.parameters_json)
    if choice=='gemini':
        return _refine_spec_through_gemini(db,user,conversation,old,payload,recent,old_spec)
    try: result=interpret(payload.instruction,old_spec,recent,interpreter='local')
    except (ParseError,UnsupportedGeometry,ValueError) as exc: raise HTTPException(422,detail={'message':str(exc)})
    if result.needs_clarification or result.spec is None: raise HTTPException(422,detail={'message':result.clarifying_question or 'Please clarify the requested change.'})
    try:
        model,mesh,validation,text=_create_model(db,user,result.spec,conversation,payload.instruction,result.provider,result.explanation,old,result.fallback_reason)
        db.commit()
        return {**_model_json(model.id,result.spec,mesh,validation,model.generation_method,model.version,old.id),
                'message':text,'provider':result.provider,'interpretation_provider':result.provider,
                'geometry_generation_method':model.generation_method,'fallback_reason':result.fallback_reason}
    except RuntimeError as exc: db.rollback(); raise HTTPException(503,detail={'message':str(exc)})
    except (ValueError,UnsupportedGeometry) as exc: db.rollback(); raise HTTPException(422,detail={'message':str(exc)})

def _refine_script_model(db,user,conversation,old,payload,recent):
    script=(old.parameters_json or {}).get('creation_script','')
    plan=(old.parameters_json or {}).get('plan_dimensions_mm') or (old.validation_json or {}).get('requested_dimensions_mm')
    if not script: raise HTTPException(422,detail={'message':'This model has no stored creation script to refine.'})
    request=codegen.ScriptRequest(prompt=payload.instruction,instruction=payload.instruction,previous_script=script,
                                  requested_dims=[float(axis) for axis in (plan or [80,80,100])],
                                  recent_context=recent)
    try:
        generation=codegen.build_script(request)
        model,mesh,validation,text=_persist_script_model(db,user,conversation,generation,'refinement',payload.instruction,old)
        db.commit()
        return {**_model_json(model.id,model.parameters_json,mesh,validation,model.generation_method,model.version,old.id),
                'message':text,'provider':'gemini','interpretation_provider':'gemini',
                'geometry_generation_method':codegen.SCRIPT_METHOD,'fallback_reason':None,
                'script_attempts':generation.attempts}
    except codegen.CodegenError as exc: db.rollback(); raise _codegen_http_error(exc)
    except RuntimeError as exc: db.rollback(); raise HTTPException(503,detail={'message':str(exc)})
    except (ValueError,UnsupportedGeometry) as exc: db.rollback(); raise HTTPException(422,detail={'message':str(exc)})

def _refine_spec_through_gemini(db,user,conversation,old,payload,recent,old_spec):
    request=codegen.ScriptRequest(prompt=payload.instruction,instruction=payload.instruction,
                                  previous_spec=old.parameters_json,
                                  requested_dims=[old_spec.dimensions_mm.width,old_spec.dimensions_mm.depth,old_spec.dimensions_mm.height],
                                  previous_structure=[component.model_dump() for component in old_spec.components] if old_spec.shape_type=='assembly' else None,
                                  recent_context=recent)
    try:
        generation=codegen.build_script(request)
        model,mesh,validation,text=_persist_script_model(db,user,conversation,generation,'refinement',payload.instruction,old)
        db.commit()
        return {**_model_json(model.id,model.parameters_json,mesh,validation,model.generation_method,model.version,old.id),
                'message':text,'provider':'gemini','interpretation_provider':'gemini',
                'geometry_generation_method':codegen.SCRIPT_METHOD,'fallback_reason':None,
                'script_attempts':generation.attempts}
    except codegen.CodegenError as exc: db.rollback(); raise _codegen_http_error(exc)
    except RuntimeError as exc: db.rollback(); raise HTTPException(503,detail={'message':str(exc)})
    except (ValueError,UnsupportedGeometry) as exc: db.rollback(); raise HTTPException(422,detail={'message':str(exc)})

@router.get('/models/{model_id}')
def get_model(model_id: str,db: Session=Depends(get_db),user: User=Depends(current_user)):
    model=db.query(GeneratedModel).filter(GeneratedModel.id==model_id,GeneratedModel.owner_id==user.id).first()
    if not model: raise HTTPException(404,detail={'message':'Model not found.'})
    mesh=_load_mesh(user,model)
    spec=Spec.model_validate(model.parameters_json) if model.generation_method!=codegen.SCRIPT_METHOD else model.parameters_json
    return _model_json(model.id,spec,mesh,model.validation_json,model.generation_method,model.version,model.parent_version_id)

@router.get('/models/{model_id}/export')
def export_model(model_id: str,format: str,db: Session=Depends(get_db),user: User=Depends(current_user)):
    model=db.query(GeneratedModel).filter(GeneratedModel.id==model_id,GeneratedModel.owner_id==user.id).first()
    if not model: raise HTTPException(404,detail={'message':'Model not found.'})
    if format not in ('stl','glb'): raise HTTPException(422,detail={'message':'Format must be stl or glb.'})
    mesh=_load_mesh(user,model)
    try:
        data=mesh.export(file_type=format)
        return RawResponse(content=data,media_type='model/stl' if format=='stl' else 'model/gltf-binary',headers={'Content-Disposition':f'attachment; filename="modelforge.{format}"'})
    except Exception as exc: raise HTTPException(422,detail={'message':'Export failed for this mesh.'}) from exc
