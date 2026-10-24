# Legacy archive

This folder is **not part of ModelForge**. It holds superseded code kept for
historical reference only — the active application is the 3D workspace under
`backend/` and `frontend/`, and nothing in it imports anything from here.

pytest is configured (root `pytest.ini`) so that default discovery collects
only `backend/`; this archive is excluded from the documented test command.

## `prompt-to-image-prototype/`

An earlier, unrelated prototype: a FastAPI service that sent a text prompt to
Gemini image generation (`gemini-nano-banana-2.1` via the SDK's Interactions
API) and returned a Base64 image. It predates ModelForge's prompt-to-3D
architecture and shares no code or models with it.

- `app/main.py`, `app/service.py` — the old `POST /api/generate` service.
- `tests/test_api.py` — its tests. They import `from app.main import app`, which
  resolves only when this prototype's root (the folder containing `app/`) is on
  `sys.path`; no conftest does that, so `pytest legacy` fails at collection.
  They also predate the current app's fixture layout and expect its own
  dependencies. Treat them as reference only.
- `.env.example` — the old configuration: `GEMINI_IMAGE_MODEL` and
  `FRONTEND_ORIGINS` are variables this project no longer reads. The active
  configuration lives in the root `.env.example` (see `backend/config.py`).

## `providers.py`

A 14-line interpreter-boundary stub (`LocalPromptInterpreter` +
`PromptInterpreter` Protocol) from an earlier ModelForge iteration. A
repository-wide search found no import of it; the active pipeline dispatches
through `backend/nlp.py::interpret` and `backend/codegen.py::build_script` instead.

## Guidance

Do not wire anything in this folder into the running application without a
fresh review. It contains a second, divergent `/api/health` route and its own
environment assumptions; it was never mounted by `backend/main.py`.
