"""User settings API: per-user provider selection and encrypted API key storage.

All mutating endpoints are CSRF-protected. The encrypted API key is never
returned to the client; GET exposes only provider, configured flag, and the
last connection-test status.
"""
from __future__ import annotations

from typing import Optional, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth import csrf_protected, current_user
from .config import get_settings
from .crypto import decrypt_api_key, encrypt_api_key
from .database import get_db
from .db_models import User, UserSetting
from .nlp import _gemini_failure_category, _gemini_failure_reason
from .schemas import SettingsResponse, SettingsSave, SettingsTestRequest, SettingsTestResponse

router = APIRouter()


def _get_setting(db: Session, user: User) -> Optional[UserSetting]:
    return db.query(UserSetting).filter(UserSetting.user_id == user.id).first()


@router.get('/settings', response_model=SettingsResponse)
def get_settings_endpoint(user: User = Depends(current_user), db: Session = Depends(get_db)):
    setting = _get_setting(db, user)
    if not setting:
        return SettingsResponse(provider='local', configured=False, connection_status=None)
    return SettingsResponse(
        provider=setting.provider,  # type: ignore[arg-type]
        configured=bool(setting.api_key_encrypted),
        connection_status=setting.connection_status,
    )


@router.patch('/settings', response_model=dict)
def save_settings(
    payload: SettingsSave,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    _: None = Depends(csrf_protected),
):
    settings = get_settings()
    if len(settings['session_secret']) < 40:
        raise HTTPException(503, detail={'message': 'Server is not configured for encrypted settings.'})

    setting = _get_setting(db, user)
    if payload.provider == 'gemini':
        if not payload.api_key:
            raise HTTPException(422, detail={'message': 'An API key is required to enable Gemini.'})
        encrypted = encrypt_api_key(payload.api_key)
        if setting:
            setting.provider = 'gemini'
            setting.api_key_encrypted = encrypted
            setting.connection_status = None
        else:
            setting = UserSetting(id=str(uuid4()), user_id=user.id, provider='gemini', api_key_encrypted=encrypted)
            db.add(setting)
    else:
        if setting:
            setting.provider = 'local'
            setting.api_key_encrypted = None
            setting.connection_status = None
        else:
            setting = UserSetting(id=str(uuid4()), user_id=user.id, provider='local')
            db.add(setting)
    db.commit()
    return {'ok': True, 'provider': setting.provider, 'configured': bool(setting.api_key_encrypted)}


@router.delete('/settings', response_model=dict)
def clear_settings(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    _: None = Depends(csrf_protected),
):
    setting = _get_setting(db, user)
    if setting is not None:
        db.delete(setting)
        db.commit()
    return {'ok': True}


@router.post('/settings/test', response_model=SettingsTestResponse)
def test_settings(
    payload: SettingsTestRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    _: None = Depends(csrf_protected),
):
    if payload.provider != 'gemini':
        return SettingsTestResponse(status='ok', message='Local interpreter requires no key.')

    key = payload.api_key
    if not key:
        setting = _get_setting(db, user)
        if setting and setting.api_key_encrypted:
            key = decrypt_api_key(setting.api_key_encrypted)
    if not key:
        return SettingsTestResponse(status='failed', message='No Gemini API key available.')

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=20000))
        client.models.count_tokens(model=get_settings()['gemini_model'], contents='ping')
        return SettingsTestResponse(status='ok', message='Gemini connection succeeded.')
    except Exception as exc:
        category = _gemini_failure_category(exc)
        return SettingsTestResponse(status='failed', message=_gemini_failure_reason(exc))
