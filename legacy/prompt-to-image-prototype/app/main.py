import base64
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .service import GenerationError, MAX_PROMPT_LENGTH, generate_image, validate_prompt

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("modelforge")
app = FastAPI(title="ModelForge API", version="1.0.0", description="Prompt-to-image generation with Gemini.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("FRONTEND_ORIGINS", "http://localhost:5173").split(","), allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="Exact user-entered prompt", max_length=MAX_PROMPT_LENGTH)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/generate")
def generate(request: GenerateRequest):
    prompt = request.prompt
    try:
        validate_prompt(prompt)
        result = generate_image(prompt)
    except GenerationError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": exc.args[0]}) from None
    logger.info("Generated image model=%s elapsed_ms=%d mime=%s size=%d dimensions=%dx%d", result.model, result.duration_ms, result.mime_type, len(result.data), result.width, result.height)
    return {"prompt": prompt, "model": result.model, "duration_ms": result.duration_ms, "mime_type": result.mime_type, "width": result.width, "height": result.height, "size_bytes": len(result.data), "image_base64": base64.b64encode(result.data).decode("ascii")}
