import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app import service


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (3, 2), "orange").save(buf, format="PNG")
    return buf.getvalue()


class Part:
    def __init__(self, data, mime="image/png"):
        self.inline_data = type("Inline", (), {"data": data, "mime_type": mime})()


class Response:
    def __init__(self, outputs):
        self.outputs = outputs


class Output:
    def __init__(self, data, mime="image/png", kind="image"):
        self.type, self.data, self.mime_type = kind, data, mime


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    return TestClient(app)


def test_valid_result_preserves_prompt_model_and_image(client, monkeypatch):
    prompt = "A glass observatory floating above a violet sea"
    calls = {}
    def fake_create(**kwargs):
        calls.update(kwargs)
        return Response([Output(base64.b64encode(png_bytes()).decode())])
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "chosen-image-model")
    monkeypatch.setattr(service.genai.Client, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(service.genai.Client, "interactions", property(lambda self: type("Interactions", (), {"create": staticmethod(fake_create)})()), raising=False)
    response = client.post("/api/generate", json={"prompt": prompt})
    assert response.status_code == 200
    body = response.json()
    assert body["prompt"] == prompt and calls["input"] == prompt
    assert calls["response_modalities"] == ["IMAGE"]
    assert body["model"] == "chosen-image-model"
    assert base64.b64decode(body["image_base64"]) == png_bytes()
    assert (body["width"], body["height"], body["mime_type"]) == (3, 2, "image/png")


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 2001])
def test_prompt_validation(client, prompt):
    assert client.post("/api/generate", json={"prompt": prompt}).status_code == 422


def test_missing_credentials(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    assert client.post("/api/generate", json={"prompt": "A lighthouse"}).status_code == 503


@pytest.mark.parametrize("exc,status", [(RuntimeError("API key unauthorized"), 401), (RuntimeError("RESOURCE_EXHAUSTED quota 429"), 429)])
def test_provider_auth_and_quota(client, monkeypatch, exc, status):
    monkeypatch.setattr(service.genai.Client, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(service.genai.Client, "interactions", property(lambda self: type("Interactions", (), {"create": staticmethod(lambda **kw: (_ for _ in ()).throw(exc))})()), raising=False)
    assert client.post("/api/generate", json={"prompt": "A lighthouse"}).status_code == status


def test_text_only_is_error_never_fake_success(client, monkeypatch):
    monkeypatch.setattr(service.genai.Client, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(service.genai.Client, "interactions", property(lambda self: type("Interactions", (), {"create": staticmethod(lambda **kw: Response([]))})()), raising=False)
    response = client.post("/api/generate", json={"prompt": "A lighthouse"})
    assert response.status_code == 502
    assert "image_base64" not in response.json()


def test_invalid_image_bytes(client, monkeypatch):
    monkeypatch.setattr(service.genai.Client, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(service.genai.Client, "interactions", property(lambda self: type("Interactions", (), {"create": staticmethod(lambda **kw: Response([Output(base64.b64encode(b"not an image").decode())]))})()), raising=False)
    assert client.post("/api/generate", json={"prompt": "A lighthouse"}).status_code == 502


def test_oversized_output(client, monkeypatch):
    monkeypatch.setattr(service, "MAX_IMAGE_BYTES", 10)
    monkeypatch.setattr(service.genai.Client, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(service.genai.Client, "interactions", property(lambda self: type("Interactions", (), {"create": staticmethod(lambda **kw: Response([Output(base64.b64encode(png_bytes()).decode())]))})()), raising=False)
    assert client.post("/api/generate", json={"prompt": "A lighthouse"}).status_code == 502


def test_health_is_lightweight(client):
    assert client.get("/api/health").json() == {"status": "ok"}
