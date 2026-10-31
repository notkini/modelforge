"""User settings API tests: encrypted per-user provider settings."""

import uuid

import pytest

from . import api_settings
from .crypto import decrypt_api_key, encrypt_api_key
from .db_models import UserSetting


def _patch_gemini(monkeypatch, raise_exc=None):
    class _Models:
        @staticmethod
        def count_tokens(model, contents):
            if raise_exc is not None:
                raise raise_exc
            return object()

    class _Client:
        def __init__(self, api_key=None, http_options=None):
            self.api_key = api_key
            self.models = _Models()

    monkeypatch.setattr('google.genai.Client', _Client)


def test_settings_get_defaults_to_local(client):
    served = client.get('/api/settings')
    assert served.status_code == 200, served.text
    body = served.json()
    assert body == {'provider': 'local', 'configured': False, 'connection_status': None}


def test_settings_save_and_get(client, database, monkeypatch):
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-not-production-random-key')
    saved = client.patch('/api/settings', json={'provider': 'gemini', 'api_key': 'sk-test-12345'})
    assert saved.status_code == 200, saved.text
    assert saved.json()['configured'] is True
    assert saved.json()['provider'] == 'gemini'

    served = client.get('/api/settings')
    assert served.status_code == 200, served.text
    body = served.json()
    assert body['provider'] == 'gemini'
    assert body['configured'] is True
    assert 'api_key_encrypted' not in body

    # Roundtrip the stored encrypted key.
    db = database['factory']()
    try:
        row = db.query(UserSetting).first()
        assert row is not None
        assert row.provider == 'gemini'
        assert decrypt_api_key(row.api_key_encrypted) == 'sk-test-12345'
    finally:
        db.close()


def test_settings_gemini_requires_key(client):
    saved = client.patch('/api/settings', json={'provider': 'gemini'})
    assert saved.status_code == 422, saved.text


def test_settings_switch_to_local_clears_key(client, monkeypatch):
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-not-production-random-key')
    client.patch('/api/settings', json={'provider': 'gemini', 'api_key': 'sk-test-12345'})
    cleared = client.patch('/api/settings', json={'provider': 'local'})
    assert cleared.status_code == 200, cleared.text
    served = client.get('/api/settings')
    body = served.json()
    assert body['provider'] == 'local'
    assert body['configured'] is False


def test_settings_delete_clears(client, monkeypatch):
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-not-production-random-key')
    client.patch('/api/settings', json={'provider': 'gemini', 'api_key': 'sk-test-12345'})
    removed = client.delete('/api/settings')
    assert removed.status_code == 200, removed.text
    served = client.get('/api/settings')
    assert served.json()['configured'] is False


def test_settings_test_connection_ok(client, monkeypatch):
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-not-production-random-key')
    _patch_gemini(monkeypatch)
    client.patch('/api/settings', json={'provider': 'gemini', 'api_key': 'sk-test-12345'})
    tested = client.post('/api/settings/test', json={'provider': 'gemini'})
    assert tested.status_code == 200, tested.text
    assert tested.json()['status'] == 'ok'


def test_settings_test_connection_failed_sanitized(client, monkeypatch):
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-not-production-random-key')

    class _AuthError(Exception):
        pass

    _patch_gemini(monkeypatch, raise_exc=_AuthError('invalid credential details'))
    tested = client.post('/api/settings/test', json={'provider': 'gemini', 'api_key': 'sk-bad'})
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body['status'] == 'failed'
    assert 'invalid credential details' not in body['message']


def test_settings_test_no_key(client):
    tested = client.post('/api/settings/test', json={'provider': 'gemini'})
    assert tested.status_code == 200, tested.text
    assert tested.json()['status'] == 'failed'


def test_settings_user_isolation(client, database):
    # Save settings for the current user.
    client.patch('/api/settings', json={'provider': 'gemini', 'api_key': 'sk-user-a'})
    # Switch to another user; their settings endpoint must not reveal the first user's key.
    client.test_identity['user'] = database['other']
    served = client.get('/api/settings')
    assert served.status_code == 200, served.text
    assert served.json()['configured'] is False
    assert served.json()['provider'] == 'local'

    # Other user can save their own settings independently.
    client.patch('/api/settings', json={'provider': 'gemini', 'api_key': 'sk-user-b'})
    assert client.get('/api/settings').json()['configured'] is True

    # Original user still isolated.
    client.test_identity['user'] = database['user']
    assert client.get('/api/settings').json()['configured'] is True


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-not-production-random-key')
    enc = encrypt_api_key('sk-secret-value')
    assert enc != 'sk-secret-value'
    assert decrypt_api_key(enc) == 'sk-secret-value'
    assert decrypt_api_key('not-valid-encrypted-data!!!') is None
