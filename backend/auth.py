import hashlib, hmac, secrets, uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from .config import get_settings
from .database import get_db
from .db_models import AuthSession, User

SESSION_COOKIE='modelforge_session'
CSRF_COOKIE='modelforge_csrf'
NONCE_COOKIE='modelforge_nonce'
SESSION_DAYS=14

def _require_session_secret() -> None:
    secret = get_settings()['session_secret']
    if not secret or len(secret) < 40 or secret.lower().startswith('replace-with-'):
        raise HTTPException(
            503,
            detail={'message': 'Set SESSION_SECRET to a randomly generated value of at least 40 characters before enabling sign-in.'},
        )


def _digest(value: str) -> str:
    _require_session_secret()
    secret = get_settings()['session_secret']
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()

def hash_password(password: str) -> str:
    salt=secrets.token_bytes(16)
    derived=hashlib.scrypt(password.encode('utf-8'),salt=salt,n=2**14,r=8,p=1,dklen=64)
    return f"scrypt$16384$8$1${urlsafe_b64encode(salt).decode()}${urlsafe_b64encode(derived).decode()}"

def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded: return False
    try:
        algorithm,n,r,p,salt,expected=encoded.split('$')
        if algorithm!='scrypt': return False
        actual=hashlib.scrypt(password.encode('utf-8'),salt=urlsafe_b64decode(salt),n=int(n),r=int(r),p=int(p),dklen=len(urlsafe_b64decode(expected)))
        return hmac.compare_digest(actual,urlsafe_b64decode(expected))
    except (ValueError,TypeError,MemoryError):
        return False

def _unauthorized(): return HTTPException(status_code=401,detail={'message':'Sign in to continue.'})

def current_user(request: Request, db: Session=Depends(get_db)) -> User:
    raw=request.cookies.get(SESSION_COOKIE)
    if not raw: raise _unauthorized()
    session=db.query(AuthSession).filter(AuthSession.token_hash==_digest(raw)).first()
    expires=session.expires_at.replace(tzinfo=timezone.utc) if session and session.expires_at.tzinfo is None else (session.expires_at if session else None)
    if not session or expires <= datetime.now(timezone.utc):
        if session: db.delete(session); db.commit()
        raise _unauthorized()
    if session.user.status!='active': raise _unauthorized()
    request.state.auth_session=session
    return session.user

def csrf_protected(request: Request, db: Session=Depends(get_db)):
    cookie=request.cookies.get(CSRF_COOKIE); header=request.headers.get('x-csrf-token')
    if not cookie or not header or not hmac.compare_digest(cookie,header):
        raise HTTPException(403,detail={'message':'CSRF validation failed. Refresh the page and retry.'})
    raw=request.cookies.get(SESSION_COOKIE)
    if raw:
        session=db.query(AuthSession).filter(AuthSession.token_hash==_digest(raw)).first()
        if session and not hmac.compare_digest(session.csrf_hash,_digest(cookie)):
            raise HTTPException(403,detail={'message':'CSRF token expired. Refresh the page and retry.'})

def user_payload(user: User):
    return {'id':user.id,'email':user.email,'name':user.display_name,'picture':user.picture_url}

def issue_session(db: Session,user: User,csrf: str):
    raw=secrets.token_urlsafe(32)
    record=AuthSession(id=str(uuid.uuid4()),user_id=user.id,token_hash=_digest(raw),csrf_hash=_digest(csrf),expires_at=datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS))
    db.add(record); db.commit()
    return raw

def cookie_settings():
    secure=get_settings()['secure_cookies']
    return {'httponly':True,'secure':secure,'samesite':'lax','path':'/'}
