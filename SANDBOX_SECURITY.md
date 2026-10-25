# Sandbox security: the Gemini CadQuery script worker

This document describes how ModelForge executes Gemini-written CadQuery
programs, which protections are currently implemented and verified, which
boundaries are **not** implemented, and what a production deployment must
add. It reflects the code as of the security-hardening pass that introduced
the `MF_*` limit overrides and `backend/test_security.py`.

## 1. Execution model

When the Gemini interpreter generates a model, `backend/codegen.py` sends the
prompt (plus bounded context and sanitized repair history) to Google's Gen AI
SDK and validates the returned fenced `python` block against a strict contract
(imports, `# plan-dimensions:` header, `# explanation:` comment, a `result`
assignment, declarative outer bounds). The validated script text is then
executed by `backend/script_runner.py`:

1. The parent writes the script into a private JSON payload inside a fresh
   `mf-script-*` temporary work directory together with the runner program.
2. The worker is a fresh CPython started with `-I -E -s` (`python -I`:
   isolated mode, no user site-packages, no environment overrides), cwd set to
   the work directory, stdio discarded, console hidden, and a **scrubbed
   environment** that carries only SYSTEMROOT/SystemDrive, the user profile
   paths that OpenCASCADE expects, and `TEMP`/`TMP`/`PATH` pointing at the
   work directory / the Python install. Application secrets
   (`GEMINI_API_KEY`, `SESSION_SECRET`, credentials, database URLs) are absent
   from the worker environment — verified by `test_security.py`.
3. Before the payload is read, the worker installs guards: an import
   allow-list (`cadquery`, `math`; dotted sub-module names refused), builtin
   deny wrappers for `open`, `exec`, `eval`, `compile`, `input`,
   `breakpoint`, `help` (all checked per frame), and a scrub of dangerous
   aliases exposed on the root `cadquery` namespace.
4. The worker signals readiness with a marker file and blocks until the
   parent writes a second marker; the parent only writes that marker after
   attaching the worker to a **Windows Job Object** (hard 1 GiB process and
   job memory cap, 30 s default CPU budget, at most 8 processes, kill on
   job close) — untrusted code cannot run before its resource ceiling is
   attached.
5. The script executes; only its `result` object is exported, as a B-rep
   file written to the exact path the parent designated (stdout/stderr are
   discarded; concepts like network, files, randomness, or subprocesses are
   contract-violations the parent rejects or the guards block on the
   per-frame level).
6. The parent validates the B-rep through the shared OpenCASCADE pipeline
   (valid solids, non-zero volume, bounds within the plan tolerance,
   watertight tessellation), and stores the same mesh the viewer displays.
   Failures are sent back to Gemini as sanitized categories — never raw
   exception text or absolute paths — for at most three repair attempts.

## 2. Verified protections (with automated tests)

`backend/test_sandbox.py` and `backend/test_security.py` prove:

| Control | Test |
| --- | --- |
| Import allow-list blocks direct and dotted imports | `test_run_script_blocks_direct_stdlib_import`, `test_run_script_blocks_dotted_import_escape` |
| Builtin deny wrappers block `open`/`eval`/`exec`/`compile` (also via `os.fdopen`) | `test_run_script_blocks_dangerous_builtins` |
| CadQuery root aliases for `os`/`sys`/… removed | `test_run_script_blocks_cq_attribute_escape` |
| Worker environment carries no application secrets | `test_env_secrets_absent_from_worker` |
| CPU budget enforced (worker terminated) | `test_run_script_enforces_cpu_limit` |
| Wall-clock timeout enforced; whole process tree killed at timeout | `test_run_script_enforces_wall_timeout`, `test_spawned_child_contained_until_wall_timeout` |
| Startup failures map to a controlled error | `test_startup_failure_maps_cleanly` |
| Script size bounded (default 100 000 chars) | `test_run_script_rejects_invalid_payload` |
| BREP output size bounded (default 16 MB) | `test_brep_output_size_cap` |
| Tessellated mesh/solid complexity bounded server-side (default 400 000 vertices / 800 000 faces / 32 solids) | `test_mesh_complexity_caps_reject_excess`, `test_solid_count_cap_rejects_excess` |
| Failed and successful runs remove the worker temp directory | `test_no_temp_workdirs_leak_after_failures`, `test_run_script_executes_and_exports_brep` |
| API bodies expose only sanitized messages (no paths, no internals) | `test_api_sandbox_errors_expose_no_internal_paths`, and contract tests in `test_upgrade.py` |
| Invalid limit overrides fall back to safe defaults | `test_limit_env_overrides_are_validated` |

## 3. Empirically verified limitations (threat model changes required)

These were demonstrated with harmless probes against the real worker; the
probes are encoded as "limitation tripwire" tests whose failure would mean a
new boundary appeared:

- **Attribute traversal is not blocked.** A generated script can enumerate
  `().__class__.__base__.__subclasses__()` and reach helper-class
  `__globals__` maps that contain the `sys`, `io`, and `ctypes` module
  objects. From there it can:
  - read (or write) arbitrary files reachable by the OS user, e.g. via
    `sys.modules['io'].open`;
  - create sockets (`sys.modules['socket']`) — verified with a localhost
    bind/listen;
  - call the Win32 API through `ctypes`;
  - spawn child processes (`os.spawnv` / `os.execl` on absolute paths).
- **Filesystem and network are not restricted beyond the OS user's rights.**
  The Job Object bounds CPU, memory, wall time, and process count; it does
  **not** filter file paths or network access. Secrets are therefore safe
  (not in the worker environment), but the worker can read user files at the
  same privilege level and communicate with the network.
- **Python-level guards are instrumentation, not isolation.** They shape the
  common path and stop script-frame usage of the wrapped builtins, but the
  process itself is ordinary user code.

Tripwire tests that document exactly this state:
`test_stdlib_file_read_outside_workspace_not_blocked_limitation`,
`test_loopback_socket_creation_not_blocked_limitation`,
`test_spawned_child_contained_until_wall_timeout`.

## 4. Configuration

All limits have tuned defaults and are read per run; unreadable values fall
back to the defaults (never disable the guard):

| Environment variable | Default | Range |
| --- | --- | --- |
| `MF_SANDBOX_WALL_TIMEOUT_S` | 90 | 5 – 600 |
| `MF_SANDBOX_CPU_SECONDS` | 30 | 1 – 600 |
| `MF_SANDBOX_MEMORY_BYTES` | 1 GiB (per process and job-wide) | 128 MiB – 8 GiB |
| `MF_MAX_SCRIPT_CHARS` | 100 000 | 1 000 – 2 000 000 |
| `MF_MAX_BREP_BYTES` | 16 MiB | 256 B – 512 MiB |
| `MF_MAX_CAD_SOLIDS` | 32 | 1 – 1024 |
| `MF_MAX_MESH_VERTICES` | 400 000 | 1 – 4 000 000 |
| `MF_MAX_MESH_FACES` | 800 000 | 1 – 8 000 000 |

Set them in `.env` only if you have a reason to move away from the defaults;
tightening them without evidence risks failing ordinary models.

## 5. Deployment requirements

- Windows only for the Gemini script path; the endpoint reports
  `unsupported_platform` elsewhere. CadQuery 2.8.0 (OpenCASCADE) is required
  for validation. No other external dependency (no Docker, no Windows
  Sandbox) is introduced by this implementation.
- Run the server on a machine whose OS user holds no privileges beyond what
  the sandbox worker should have. Everyone whose browser can drive prompts
  controls code execution inside the worker process, at the worker's user
  rights.

## 6. Recommended production architecture

Do **not** treat the in-process Job Object as your only boundary when serving
untrusted users. Recommended, in order of strength:

1. **Operating-system isolation** for the worker (and ideally the whole
   backend): a dedicated low-privilege Windows service account with an ACLed
   filesystem (deny access to the repository, `.env`, database, artifact
   directory, and other profiles), a Windows Firewall profile that blocks
   outbound traffic for that account, and no interactive rights. The worker
   inherits that account's restrictions; ModelForge code does not need to
   change because limits/paths are already externalised.
2. **Virtualisation or containerisation** if Windows Sandbox, Hyper-V
   isolated containers, or similar tooling is available and acceptable in
   your environment; then the account and ACL setup shrinks to the guest
   VM/container image.
3. **Capacity shunting**: run generations through a queue with a bounded
   number of concurrent workers on separate hosts so a hostile workload can
   neither exhaust the API host nor see other users' data.

Anything weaker than (1) does not prevent a hostile script from reading
files reachable to its user, or from opening network connections — see the
verified limitations above. The Python-level guards and the Job Object are
hardening layers; they are **not** a complete sandbox. State this boundary
explicitly in your own deployment documentation, and keep the tripwire tests
in `backend/test_security.py` green whenever the worker changes.

## 7. Running the security tests

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend -q
```

Run the sandbox-focused subset with verbose labels:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/test_sandbox.py backend/test_security.py -v
```

The tests use no live Gemini API, only the local sandbox worker, the system
`ping` binary (loopback destination), temporary files, and localhost-only
sockets.
