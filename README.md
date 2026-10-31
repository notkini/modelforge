# ModelForge

ModelForge is a conversational 3D creation workspace. It combines a React, TypeScript, Vite and Three.js client with a FastAPI geometry service. The workspace includes Google sign-in, per-user SQL persistence, model versions linked to each conversation, refinement with parent/child version relationships, and STL/GLB export. Prompts are handled by two selectable interpreters: an optional Gemini path that writes complete CadQuery Python programs, and a deterministic local parser. Gemini-written code is executed only inside the restricted Windows script worker, and every generated form must pass the local OpenCASCADE pipeline before it becomes a model.

## Windows setup (PowerShell)

Run the backend and frontend in separate terminals. Start both from the repository root when using the commands below.

```powershell
# First-time backend setup
py -3.11 -m venv backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install --upgrade pip
.\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item .env.example .env
# Edit .env with your Google client ID, a random SESSION_SECRET (command below), and optional Gemini key.
.\backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
.\backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

```powershell
# First-time frontend setup and start (second terminal)
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

Open http://localhost:5173. The API health check is http://127.0.0.1:8000/api/health. If your prompt currently ends in `ModelForge\backend>`, run `Set-Location ..` before starting Uvicorn. That avoids the `No module named 'backend'` error caused by launching the package from inside its own directory.

To activate the backend environment instead, from the project root run `.\backend\.venv\Scripts\Activate.ps1`. If your current directory is `frontend`, use `..\backend\.venv\Scripts\Activate.ps1`. PowerShell may require `Set-ExecutionPolicy -Scope Process Bypass` for activation. Calling the Python executable directly avoids that policy change.

## Configuration

`.env.example` contains placeholders only. Keep `.env` private. Required for any sign-in: a strong random `SESSION_SECRET`; Google sign-in additionally needs `GOOGLE_CLIENT_ID`. Gemini is optional: without `GEMINI_API_KEY`, prompts use the deterministic local parser and the Gemini interpreter cannot be selected. `DATABASE_URL` defaults to the root-level SQLite file `modelforge.db`. `ARTIFACT_DIR` defaults to `private_artifacts` and must be a private, persistent directory outside the frontend static root.

Generate a session secret in PowerShell with:

```powershell
py -c "import secrets; print(secrets.token_urlsafe(48))"
```

For deployment, set `SECURE_COOKIES=true`, serve over HTTPS, restrict `FRONTEND_ORIGIN` to the actual app origin, and use a private persistent artifact volume. PostgreSQL is supported with a SQLAlchemy URL such as `postgresql+psycopg://user:password@host:5432/modelforge`; use managed secret storage rather than checking credentials into files. SQLite is intended for local development and a single application process.

## Google sign-in setup

1. In Google Cloud Console, configure the OAuth consent screen and create an OAuth client of type **Web application**.
2. Add Authorized JavaScript origins `http://localhost:5173` and `http://127.0.0.1:5173` for local use.
3. Set `GOOGLE_CLIENT_ID` in the backend `.env` to the web client ID. This Google Identity Services popup credential flow does not need a client secret or redirect URI. The client ID is a public identifier; never put a client secret in the frontend.
4. Restart the API and sign in. The backend verifies the signed, expiring ID token, issuer and audience; it also checks the short-lived nonce and verified Google `sub`. The app uses an opaque HTTP-only session cookie and stores only an HMAC hash of the session identifier in SQL. State-changing requests require a CSRF header matching the CSRF cookie.

Only the sign-in identity scopes are requested. The Google credential is sent directly to the backend and is not stored in browser local storage. The API has no third-party Google redirect URI in this flow.

The sign-in page also supports local email/password account creation and login. Passwords must be at least 12 characters and are stored as salted scrypt hashes. Local accounts do not require Google configuration. Accounts are not automatically linked based on matching email addresses; use the same sign-in method used at account creation. If an email already belongs to a Google account, use Google sign-in for that account.

## Database and migrations

The database contains users, conversations, messages, generated model versions, hashed sessions and short-lived sign-in nonces. Google `sub` is the stable identity key; email is profile data. Queries scope conversations, models and artifacts to the authenticated owner. Each regeneration or refinement inserts a new model version into the same conversation, numbered sequentially, with a `parent_version_id` link to the version it was derived from; earlier versions, their specs and artifacts remain available. Mesh arrays are stored as compressed files under the private artifact directory, not in SQL.

Apply migrations from the project root:

```powershell
.\backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
.\backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini current
```

After pulling a migration, run `upgrade head` again. Review migration scripts before deployment. Back up the database and artifact directory together before upgrades; do not delete or recreate either to resolve migration errors. The local-password migration refuses to downgrade while password-only accounts exist, because doing so would lock those users out. Existing browser conversations are offered for import after sign-in. The import is bound to the signed-in account, idempotent, and the original browser history is retained after import; legacy mesh arrays remain in that browser copy and are not uploaded as artifacts.

## Interpreters and Gemini code generation

The backend dispatches prompt understanding through one of two interpreters, selected per request (`auto`, `gemini`, or `local`; the UI persists the choice). `auto` resolves to Gemini when `GEMINI_API_KEY` is configured, otherwise to the local parser. An explicit selection is enforced literally: the chosen interpreter must succeed, and the backend never silently substitutes the other one. Gemini failures surface as plain 422 or 503 messages (timeouts, quota limits, contract violations), so a user who selected Gemini is never served local-parser output instead.

When Gemini generates a model (no parameter spec supplied), the backend uses Google's supported Python Gen AI SDK (`google-genai`). The model receives the prompt plus a small recent conversation context and must return exactly one fenced `python` code block containing a complete CadQuery program. The contract: only `import cadquery as cq` and `import math`; a `# plan-dimensions: <width>,<depth>,<height>` header and a `# explanation:` comment; the final geometry assigned to a variable named `result`; millimetre units with the outer bounding box matching the declared plan; and a fully deterministic body — no network, files, randomness, subprocesses, or wrapped `try/except`. Scripts missing any of this fail before execution.

The program then runs in the restricted script worker described below, and the resulting B-rep passes the same OpenCASCADE validation used for local parametric specs: valid solids, non-zero volume, outer bounds within max(0.5 mm, 1 %) of the declared plan, and a watertight tessellation. Gemini gets up to three attempts: sanitized failure categories — never raw exception text — are returned to it and counted as repair attempts, and the model records that count. There are no arbitrary tools, shell commands, image-to-3D inference, or vendor fallbacks. Existing parametric models can be refined into script models by sending their spec or components as structured context; a script model can only be refined with a Gemini-selected interpreter.

The default `GEMINI_MODEL` is **`gemini-3.8-flash`**, selected as the strongest current stable text-capable Gemini API model explicitly shown by Google's model and pricing documentation as having free-tier input/output pricing. Configure another supported model by changing `GEMINI_MODEL`; source changes are not required. Free tier is quota/rate limited, availability may depend on account and region, and it is not unlimited. Google documents that free-tier content may be used to improve its products; do not send sensitive prompts. Eligibility and quotas can change, so check [Gemini API model documentation](https://ai.google.dev/gemini-api/docs/models) and [official pricing and free-tier terms](https://ai.google.dev/gemini-api/docs/pricing) before relying on a model. Obtain an API key through Google AI Studio and set it only in the backend `.env` as `GEMINI_API_KEY`.

### Script sandbox (Windows required)

The worker is a fresh CPython started in isolated mode (`-I -E -s`) with a clean working directory, a scrubbed environment (no application secrets, verified by test), no console, and null stdio; it can import only `cadquery` and `math`, dangerous builtins (`open`, `exec`, `eval`, `compile`, `input`, …) are refused when called from script frames, and it can deliver geometry only by writing a B-rep file to the exact path the parent designated. Nothing untrusted runs before a launch handshake confirms the resource limits are attached, and the temporary work directory is removed after every run, success or failure.

Enforcement of last resort is a Windows Job Object: a 1 GiB memory cap applied per process and to the whole job, a CPU-time budget (30 s default), at most 8 processes, kill-on-close for the entire tree, plus a 90-second wall-clock timeout in the parent. Sizes are bounded in both directions: scripts are capped at 100 000 characters, the sandbox B-rep output at 16 MB, and the server rejects tessellations beyond 400 000 vertices / 800 000 faces or more than 32 solids. All caps are configurable with validated fallback defaults (`MF_*` environment variables; see `SANDBOX_SECURITY.md`).

These import/execution guards are practical instrumentation, not a proof of isolation, and this is verified — not assumed: tests demonstrate that a generated script can reach `sys`, `io`, `ctypes`, and `socket` through attribute traversal, opening arbitrary file handles, creating sockets, and spawning child processes inside the contained tree. The Job Object bounds resources; it does not restrict filesystem paths or network access beyond the rights of the OS user. Run the backend as a restricted OS user (or better, behind a virtualized boundary) before serving untrusted users — the full threat model, deployment recommendations, and the security-test labels live in `SANDBOX_SECURITY.md`. The Gemini script path requires Windows and reports `unsupported_platform` elsewhere; the local CAD, mesh, preview, and export paths are ordinary Python and remain cross-platform.

## Parametric CAD and geometry

Mechanical models are generated as OpenCASCADE B-rep solids with CadQuery 2.8.0 (`parametric_cad`). Organic models remain separate, approximate Gaussian implicit meshes (`gaussian_implicit_mesh`) generated from a bounded NumPy field and scikit-image marching cubes. The API records the method explicitly and never treats the Gaussian mesh as exact CAD.

Implemented CAD features:

- Box, rounded box (kernel fillet), cylinder, sphere, cone, hollow tube, and dimensioned mounting plate.
- Open or closed rectangular enclosures with requested wall and floor thicknesses; an optional separate lid is stored as a two-solid compound.
- Brackets, spacer/bushing with real bore subtraction, and stepped hollow adapters.
- Cylindrical holes in a selected principal axis, with optional depth; the kernel checks that each cut removes material.
- Boolean union, difference and intersection on primitive components.
- Real OpenCASCADE edge fillets and chamfers with invalid-operation errors.
- 2D polygon extrusion and polygon-section lofts.

All API dimensions use millimetres. The B-rep is checked for validity, non-zero volume, and actual bounding dimensions; mismatch beyond 0.05 mm is an error and the part is never rescaled. CAD solids are tessellated at a controlled tolerance for the existing viewer and STL/GLB exporters. Tessellated topology is checked for watertightness and winding. The original B-rep is also retained as a private STEP artifact beside the mesh, and the validated specification remains in the database for regeneration. Model artifacts remain in owner-scoped storage. A watertight tessellation does not guarantee print success.

CadQuery is installed and verified with OpenCASCADE in this Windows development setup using Python 3.11.9; the backend requirements pin `cadquery==2.8.0`. CadQuery publishes Windows installation support and binary OCP wheels; its installation documentation recommends conda/mamba for the most reproducible dependency setup. If pip installation fails on another Windows machine, create a Python 3.11/3.12 environment with Miniforge and install CadQuery using the [official installation guide](https://github.com/CadQuery/cadquery/blob/master/doc/installation.rst), then install the remaining `backend/requirements.txt` packages. CadQuery 2.8.0 requires a supported 64-bit Python environment; Python 3.11 was the environment tested here. See the [CadQuery project](https://github.com/CadQuery/cadquery) for current platform support.

Unsupported geometry requests are rejected rather than approximated or represented only in metadata. The local parser recognizes only basic primitives and enclosures plus a few explicit refinement phrasings ("make it N mm taller/wider", wall-thickness changes); prompts that mention mounting holes, booleans, fillets or chamfers are rejected with guidance until those values are supplied through Gemini or the parameter inspector. Feature positions and profile points must always be explicit. Image attachment remains a local reference UI; image-to-3D is not available.

## General-purpose design plans

Prompt requests are bounded: a prompt becomes either a validated local Spec or a contract-bound CadQuery program, never free-form execution of model text. In addition to the existing CAD primitives, profiles, lofts, Boolean operations, holes, enclosures and Gaussian organic surfaces, the CAD schema supports a bounded assembly of up to 16 named box, cylinder, sphere or cone components. Each part has millimetre dimensions, an origin-relative position and Euler rotations. The schema selects either a fused OpenCASCADE union for fixed connected parts or a compound for separate parts. The backend validates the resulting B-rep, checks actual outer bounds against requested dimensions, tessellates it, and exports the same mesh used by the viewer. Assembly names, part count, object identity, intended use and planner warnings are retained with model metadata and shown in the preview inspector. For compound assemblies, the backend additionally checks pairwise component clearance and interference: each pair of parts is classified as `pass` (separated), `warning` (touching, no shared volume), `fail` (interfering, shared volume detected), or `not_checked` (distance could not be computed reliably). An optional `clearance_mm` value can be supplied with generate or refine requests; pairs below the requested clearance are reported as `fail`. Pairwise checks do not prove mechanical fitness, manufacturability, or correct assembly — they report spatial relationships only.

This shared component representation can describe recognizable simple furniture (table/chair-like forms), toy vehicles, stands and holders without separate object-specific generators. The existing enclosure and bracket builders remain specialized only where their exact shell or hole operations need CAD-specific validation. The component vocabulary is intentionally limited: it does not generate true helical threads, gear teeth, hinges, articulated joints, complex swept forms, detailed animal anatomy, or arbitrary freeform surfaces. A requested unsupported defining feature is rejected by the planner/schema instead of being claimed as present. Organic Gaussian output remains approximate and is not an exact CAD solid. Complex visual fidelity across arbitrary prompts still requires a dedicated hosted text-to-3D or image-to-3D provider; no such paid or external provider is required or configured here.

AI change summaries are generated when a refinement has a parent model with structured metadata. The summary reports components added, removed, and modified (comparing dimensions, position, and rotation), outer dimension changes for single-object models, relevant validation changes, and warnings. When no parent exists or a reliable comparison is not possible, the summary returns an explicit `verified: false` status instead of fabricating changes. Summaries are derived from structured old/new model data only — never from LLM explanation text.

Gemini's output is a CadQuery program, not a JSON plan: the fenced block, header comments and plan dimensions are validated before anything runs, the program executes only in the restricted Windows script worker, and the resulting B-rep must pass the local bounded validators before a model is persisted. Prompts are capped at 4,000 characters, assembly part count at 16, dimensions at 500 mm per axis and component positions at 500 mm from the origin. Units are normalized to millimetres; users should include explicit units when precision matters.

## Checks

From the project root:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend -q
Push-Location frontend
npm.cmd exec tsc -- --noEmit
npm.cmd run build
Pop-Location
```

The tests use temporary SQLite databases and mocked Google/Gemini results; they do not need credentials or external AI calls. The suite includes sandbox tests that execute real CadQuery scripts locally through the worker on Windows. A `legacy/` folder holds a superseded prompt-to-image prototype (see `legacy/README.md`); it is excluded from test discovery and is not part of the application.

## Main routes

- `GET /api/health`
- `GET /api/auth/config`, `POST /api/auth/google`, `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout`
- `GET|POST /api/conversations`, `GET|PATCH|DELETE /api/conversations/{id}`
- `POST /api/conversations/{id}/messages`
- `POST /api/parse-prompt`
- `POST /api/models/generate`, `POST /api/models/{id}/refine`, `GET /api/models/{id}`
- `GET /api/models/{id}/export?format=stl|glb|step`, optional `?component=<id>` for per-component export of compound assemblies
