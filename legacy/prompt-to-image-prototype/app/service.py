"""Gemini image generation and image validation."""
import io
import os
import time
from dataclasses import dataclass

from google import genai
from PIL import Image, UnidentifiedImageError

MAX_PROMPT_LENGTH = 2000
MAX_IMAGE_BYTES = 12 * 1024 * 1024
DEFAULT_MODEL = "gemini-nano-banana-2.1"


class GenerationError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class GeneratedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    model: str
    duration_ms: int


def validate_prompt(prompt: str) -> str:
    if not prompt or not prompt.strip():
        raise GenerationError("Enter a prompt before generating an image.", 422)
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise GenerationError(f"Prompt must be {MAX_PROMPT_LENGTH} characters or fewer.", 422)
    return prompt


def _provider_image(response):
    """Read image data from the SDK's Interactions API response."""
    import base64

    for output in getattr(response, "outputs", None) or []:
        if getattr(output, "type", None) == "image" and getattr(output, "data", None):
            data = output.data
            if isinstance(data, str):
                data = base64.b64decode(data, validate=True)
            return bytes(data), getattr(output, "mime_type", None)
    return None, None


def generate_image(prompt: str) -> GeneratedImage:
    validate_prompt(prompt)
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise GenerationError("Gemini API credentials are not configured. Set GEMINI_API_KEY in backend/.env.", 503)
    model = os.getenv("GEMINI_IMAGE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    started = time.perf_counter()
    try:
        response = genai.Client(api_key=key).interactions.create(
            model=model,
            input=prompt,
            response_modalities=["IMAGE"],
        )
    except Exception as exc:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        if "auth" in name or "api key" in message or "unauthorized" in message or "permission_denied" in message:
            raise GenerationError("Gemini authentication failed. Check GEMINI_API_KEY.", 401) from None
        if "quota" in name or "resource_exhausted" in message or "429" in message:
            raise GenerationError("Gemini quota or rate limit reached. Try again later or check your Google AI quota.", 429) from None
        if "not_found" in message or "model" in message and ("not found" in message or "unsupported" in message):
            raise GenerationError("The configured Gemini image model is unavailable. Check GEMINI_IMAGE_MODEL.", 400) from None
        if "timeout" in name or "connection" in name or "network" in name:
            raise GenerationError("Could not reach Gemini. Check your connection and try again.", 502) from None
        raise GenerationError("Gemini image generation failed. Check the backend configuration and try again.", 502) from None
    duration = round((time.perf_counter() - started) * 1000)
    try:
        data, mime = _provider_image(response)
    except Exception:
        data, mime = None, None
    if not data:
        raise GenerationError("Gemini returned no image data. Try a different prompt.", 502)
    if len(data) > MAX_IMAGE_BYTES:
        raise GenerationError("Gemini returned an image that exceeds the 12 MB limit.", 502)
    if mime not in ("image/png", "image/jpeg", "image/webp"):
        raise GenerationError("Gemini returned an unsupported or missing image MIME type.", 502)
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        if width < 1 or height < 1 or width * height > 80_000_000:
            raise ValueError("unsafe dimensions")
        expected = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
        if expected != mime:
            raise ValueError("MIME does not match image format")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise GenerationError("Gemini returned invalid or undecodable image data.", 502) from None
    return GeneratedImage(data, mime, width, height, model, duration)
