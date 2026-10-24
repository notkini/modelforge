import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from .database import Base, get_db
from .db_models import User
from .main import app
from .auth import current_user, csrf_protected

@pytest.fixture
def database(tmp_path,monkeypatch):
    monkeypatch.setenv('ARTIFACT_DIR',str(tmp_path/'artifacts'))
    monkeypatch.setenv('SESSION_SECRET','test-session-secret-not-production-random-key')
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Factory=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    def override_db():
        db=Factory()
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db]=override_db
    with Factory() as db:
        user=User(id=str(uuid.uuid4()),google_sub='test-google-sub-a',email='a@example.test',display_name='Test A',status='active'); db.add(user)
        other=User(id=str(uuid.uuid4()),google_sub='test-google-sub-b',email='b@example.test',display_name='Test B',status='active'); db.add(other); db.commit()
        db.expunge(user); db.expunge(other)
    yield {'engine':engine,'factory':Factory,'user':user,'other':other}
    app.dependency_overrides.clear(); engine.dispose()

@pytest.fixture
def client(database):
    identity={'user':database['user']}
    def fake_user(): return identity['user']
    app.dependency_overrides[current_user]=fake_user
    app.dependency_overrides[csrf_protected]=lambda:None
    with TestClient(app) as c:
        c.test_identity=identity
        yield c

@pytest.fixture
def unauth_client(database):
    with TestClient(app) as c: yield c
