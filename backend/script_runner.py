"""Run untrusted CadQuery scripts inside an isolated worker process.

Contract for the generated script: it must assign a CadQuery Workplane,
Solid, or Compound to a variable named ``result``. Units are millimetres.

Defense layers:
- a fresh interpreter started in isolated mode (``-I -E -s``) with a clean
  working directory, a scrubbed environment, and no console;
- import allow-list ({cadquery, math}) plus execution guards for file
  access, eval/exec, and similar calls originating from the script's frames;
- OS-level enforcement on Windows through a Job Object: hard memory cap,
  CPU-time cap, and kill-on-close so the whole process tree dies even if
  the script spawns children or hangs;
- a wall-clock timeout in the parent with TerminateJobObject;
- bounded script size, BREP output size, and job process count;
- geometry leaves the worker only as a B-rep file written to the exact
  path the parent designated - the worker returns no other artifacts.

Import restrictions alone are not a security boundary; the Job Object is
the enforcement of last resort. Attribute traversal is also not a security
boundary: as verified empirically, generated scripts can reach ``sys``,
``io``, ``ctypes``, or ``subprocess`` through helper-class ``__globals__``
maps, so file access and network connections remain possible inside the
contained process tree. The Job Object bounds CPU, memory, wall time, and
child-process count; it does not restrict filesystem paths or network
access. Deploy behind an operating-system boundary and see
``SANDBOX_SECURITY.md`` before serving untrusted users. Windows is
required. The untrusted script is never executed before the parent
attaches the worker to the Job Object: the child signals readiness with a
marker file and waits for a second marker file before reading the payload.
Every failure path removes the worker's temporary directory.
"""
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import threading

DEFAULT_WALL_TIMEOUT_S = 90.0
DEFAULT_CPU_SECONDS = 30.0
MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
MAX_SCRIPT_CHARS = 100_000
MAX_BREP_BYTES = 16 * 1024 * 1024
ACTIVE_PROCESS_LIMIT = 8
# Limits are read per run and clamped. Unreadable or out-of-range env values
# fall back to the tuned defaults: MF_SANDBOX_WALL_TIMEOUT_S (5..600 s),
# MF_SANDBOX_CPU_SECONDS (1..600 s), MF_SANDBOX_MEMORY_BYTES (128 MiB..8 GiB),
# MF_MAX_SCRIPT_CHARS (1 000..2 000 000), MF_MAX_BREP_BYTES (4 KiB..512 MiB).
ALLOWED_IMPORT_ROOTS = ('cadquery', 'math')

PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
JOB_OBJECT_LIMIT_JOB_TIME = 0x4
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9

_IS_WINDOWS = sys.platform.startswith('win')


class ScriptSandboxError(RuntimeError):
    """The sandboxed script failed, violated its contract, or was killed."""

    def __init__(self, code: str, message: str, detail: str = ''):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


RUNNER_CHILD = r'''
import io, json, os, sys, time
OUTPUT_PATH, INPUT_PATH = sys.argv[1], sys.argv[2]
READY_PATH, GO_PATH = sys.argv[3], sys.argv[4]
sys.stdout = io.StringIO()
sys.stderr = io.StringIO()
# Exact-module allow-list (sub-tree imports like cadquery.occ_impl.* are
# refused): from-imports first resolve the module through __import__, so
# blocking dotted names stops the "from internal.module import os" escape.
_SCRIPT = 'MF_SCRIPT'
_GO_WAIT = 60.0
_REAL_IMPORT = __builtins__.__import__
_originals = {}


def _from_script(frame):
    while frame is not None:
        if frame.f_code.co_filename == _SCRIPT:
            return True
        frame = frame.f_back
    return False


def _deny(name, *args, **kwargs):
    if _from_script(sys._getframe(2)):
        raise RuntimeError('The generated script is not allowed to call %s.' % name)
    return _originals[name](*args, **kwargs)


def _guarded_import(name, *args, **kwargs):
    if _from_script(sys._getframe(1)) and name not in ('cadquery', 'math'):
        raise RuntimeError('The generated script is not allowed to import %s.' % name)
    return _REAL_IMPORT(name, *args, **kwargs)


import builtins as _builtins_module

for _guard in ('open', 'exec', 'eval', 'compile', 'input', 'breakpoint', 'help'):
    _original = getattr(_builtins_module, _guard, None)
    if _original is not None:
        _originals[_guard] = _original
        setattr(_builtins_module, _guard, (lambda label: lambda *a, **k: _deny(label, *a, **k))(_guard))
_originals['__import__'] = _REAL_IMPORT
_builtins_module.__import__ = _guarded_import

with open(READY_PATH, 'w') as ready:
    ready.write('ready')
_deadline = time.time() + _GO_WAIT
while not os.path.exists(GO_PATH):
    if time.time() >= _deadline:
        sys.exit(3)
    time.sleep(0.01)

import cadquery as cq

# The root cadquery namespace can expose aliases of stdlib modules picked
# up as imports inside cq's own code. Dotted imports are already blocked,
# so the only remaining alias surface is attribute traversal on the root
# module itself - scrub it. Sub-modules are untouched so that CadQuery's
# internal calls keep working.
for _alias in ('os', 'sys', 'io', 'inspect', 'importlib', 'ctypes', 'gc', 'pickle',
               'threading', 'subprocess', 'shutil', 'tempfile', 'socket', 'builtins',
               'code', 'codeop', 'compileall', 'traceback', 'getpass', 'time',
               'operator', 'itertools', 'contextlib', 'random'):
    try:
        delattr(cq, _alias)
    except AttributeError:
        pass


def _prewarm():
    """Run a trusted first pass over the modeling operations.

    CadQuery's ``multimethod`` dispatch compiles type hints lazily on the
    first call of each operation. Doing that while the script's frame is on
    the stack would trip the ``compile`` guard, so initialize everything
    here where the untrusted frame is not yet present.
    """
    import cadquery as _cq
    try:
        base = _cq.Workplane('XY').box(1, 1, 1)
        base.val()
        base.translate((0.5, 0.5, 0.5)).rotate((0, 0, 0), (0, 0, 1), 15)
        base.faces('>Z').edges().fillet(0.05)
        base.faces('>Z').edges().chamfer(0.05)
        circle = _cq.Workplane('XY').circle(0.5).extrude(1)
        cylinder = _cq.Workplane('XY').cylinder(1, 0.5)
        cylinder.union(circle).cut(_cq.Workplane('XY').circle(0.25).extrude(1)).val()
        cylinder.intersect(circle).val()
        other = _cq.Workplane('XY').rect(0.5, 0.25).extrude(1)
        other.union(cylinder).cut(circle).val()
        _cq.Workplane('XY').sphere(0.5).val()
        _cq.Workplane('XY').cone(0.5, 1).val()
        sections = _cq.Workplane('XY').circle(0.5).workplane(offset=1).circle(0.25)
        sections.loft().val()
        _cq.Workplane('XY').polygon(6, 1).extrude(0.5).mirror('XY').val()
        _cq.Workplane('XY').polyline([(0, 0), (1, 0), (1, 1)]).close().extrude(0.5).val()
        revolved = _cq.Workplane('XY').rect(0.5, 0.25, centered=False).extrude(0.5)
        revolved.fillet(0.05).chamfer(0.05).val()
    except Exception:
        pass


_prewarm()


def _sanitize(text):
    cleaned = str(text)[:500]
    for token in (OUTPUT_PATH, INPUT_PATH, READY_PATH, GO_PATH):
        cleaned = cleaned.replace(token, '<path>')
    return cleaned


def _emit(ok, code, message, detail=None):
    with open(OUTPUT_PATH + '.status', 'w') as handle:
        json.dump({'ok': ok, 'code': code, 'message': message, 'detail': detail}, handle)
    sys.exit(0 if ok else 1)


try:
    with open(INPUT_PATH) as handle:
        payload = json.load(handle)
except Exception:
    _emit(False, 'internal_error', 'sandbox payload unreadable')
    raise

_namespace = {'__file__': _SCRIPT, '__name__': 'MF_SCRIPT'}
try:
    exec(compile(payload['script'], _SCRIPT, 'exec'), _namespace)
except BaseException as exc:
    _emit(False, 'exception', 'CadQuery script failed during execution.', _sanitize(repr(exc)))
    raise

_result = _namespace.get('result')
if _result is None:
    _emit(False, 'no_result', 'The CadQuery script never assigned a geometry object to a variable named result.')
    sys.exit(1)

try:
    from cadquery import exporters
    exporters.export(_result, OUTPUT_PATH, exportType=exporters.ExportTypes.BREP)
except BaseException as exc:
    _emit(False, 'export_failed', 'CadQuery could not export the script result geometry.', _sanitize(repr(exc)))
    raise

_emit(True, None, None)
'''

if _IS_WINDOWS:
    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ('ReadOperationCount', wintypes.ULARGE_INTEGER), ('WriteOperationCount', wintypes.ULARGE_INTEGER),
            ('OtherOperationCount', wintypes.ULARGE_INTEGER), ('ReadTransferCount', wintypes.ULARGE_INTEGER),
            ('WriteTransferCount', wintypes.ULARGE_INTEGER), ('OtherTransferCount', wintypes.ULARGE_INTEGER),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ('PerProcessUserTimeLimit', wintypes.LARGE_INTEGER), ('PerJobUserTimeLimit', wintypes.LARGE_INTEGER),
            ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
            ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
            ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ('BasicLimitInformation', _BasicLimitInformation), ('IoInfo', _IoCounters),
            ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
            ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def available() -> bool:
    return _IS_WINDOWS


def _clamped_number(raw: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value or value in (float('inf'), float('-inf')):
        return default
    return min(max(value, minimum), maximum)


def _clamped_int(raw: str, default: int, minimum: int, maximum: int) -> int:
    return int(_clamped_number(raw, float(default), float(minimum), float(maximum)))


def _scrub(text: str, workdir: str) -> str:
    cleaned = str(text)[:500]
    return cleaned.replace(workdir, '<workdir>')


def _job(cpu_seconds: float, memory_bytes: int):
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ScriptSandboxError('internal_error', 'Could not create the sandbox job object.', 'CreateJobObjectW failed')
    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY
        | JOB_OBJECT_LIMIT_JOB_TIME | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    info.BasicLimitInformation.PerJobUserTimeLimit = int(cpu_seconds * 1e7)
    info.BasicLimitInformation.ActiveProcessLimit = ACTIVE_PROCESS_LIMIT
    info.ProcessMemoryLimit = memory_bytes
    info.JobMemoryLimit = memory_bytes
    ok = _kernel32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(info),
                                           ctypes.sizeof(info))
    if not ok:
        _kernel32.CloseHandle(job)
        raise ScriptSandboxError('internal_error', 'Could not configure sandbox limits.', 'SetInformationJobObject failed')
    return job


def _assign(job, proc) -> bool:
    process = _kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
    if not process:
        return False
    try:
        return bool(_kernel32.AssignProcessToJobObject(job, process))
    finally:
        _kernel32.CloseHandle(process)


def _kill_tree(job, proc):
    if job is not None and _kernel32 is not None:
        _kernel32.TerminateJobObject(job, 1)
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def _proc_cpu_seconds(proc) -> float:
    """User+kernel CPU seconds of the worker, still valid after its death."""
    if proc is None or not _IS_WINDOWS:
        return 0.0
    counts = (ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong())
    try:
        ok = ctypes.windll.kernel32.GetProcessTimes(int(proc._handle), *[ctypes.byref(c) for c in counts])
    except Exception:
        return 0.0
    if not ok:
        return 0.0
    return (counts[1].value + counts[2].value) / 1e7  # counts: created, exited, kernel, user


def _killed_error(proc, cpu_seconds: float, code: str, message: str) -> ScriptSandboxError:
    if proc is not None and proc.returncode not in (0, None):
        used = _proc_cpu_seconds(proc)
        if used >= cpu_seconds * 0.9:
            return ScriptSandboxError('cpu_limit', 'The generated script exceeded its CPU time budget and was terminated.')
        if proc.returncode < 0:
            return ScriptSandboxError('memory_limit', 'The generated script exceeded the sandbox memory limit and was terminated.')
        return ScriptSandboxError(code, 'The sandbox worker stopped unexpectedly (exit code %s).' % proc.returncode)
    return ScriptSandboxError(code, message)


def run_script(script: str, wall_timeout_s: float = None, cpu_seconds: float = None, request_id: str | None = None):
    """Execute the script; return ``(brep_path, workdir)`` on success.

    The caller must import/remove the B-rep file and finish the work
    directory with :func:`cleanup_workdir`. Every failure removes the work
    directory before re-raising.

    Limits: explicit arguments win; otherwise the ``MF_*`` environment
    overrides apply per run (values are validated and clamped); otherwise
    the tuned module defaults.

    :param request_id: optional request ID for targeted cancellation; when
        provided, the sandbox worker process is registered so that
        :func:`cancel_run` can terminate only this operation's worker tree.
    """
    if not available():
        raise ScriptSandboxError('unsupported_platform', 'The generated script sandbox requires Windows.')
    if not isinstance(script, str) or not script.strip():
        raise ScriptSandboxError('invalid_payload', 'Gemini did not return a readable CadQuery script.')
    max_script_chars = _clamped_int(os.environ.get('MF_MAX_SCRIPT_CHARS', ''), MAX_SCRIPT_CHARS, 1_000, 2_000_000)
    if len(script) > max_script_chars:
        raise ScriptSandboxError('script_too_large', 'The generated script is too large to execute.')
    if wall_timeout_s is None:
        wall_timeout_s = _clamped_number(os.environ.get('MF_SANDBOX_WALL_TIMEOUT_S', ''), DEFAULT_WALL_TIMEOUT_S, 5.0, 600.0)
    else:
        wall_timeout_s = float(wall_timeout_s)
    if cpu_seconds is None:
        cpu_seconds = _clamped_number(os.environ.get('MF_SANDBOX_CPU_SECONDS', ''), DEFAULT_CPU_SECONDS, 1.0, 600.0)
    else:
        cpu_seconds = float(cpu_seconds)
    memory_bytes = _clamped_int(os.environ.get('MF_SANDBOX_MEMORY_BYTES', ''), MEMORY_LIMIT_BYTES, 128 * 1024 * 1024, 8 * 1024 * 1024 * 1024)
    workdir = tempfile.mkdtemp(prefix='mf-script-')
    try:
        brep_path = _run_in_sandbox(script, workdir, wall_timeout_s, cpu_seconds, memory_bytes, request_id=request_id)
        max_brep_bytes = _clamped_int(os.environ.get('MF_MAX_BREP_BYTES', ''), MAX_BREP_BYTES, 256, 512 * 1024 * 1024)
        size = os.path.getsize(brep_path)
        if size > max_brep_bytes:
            raise ScriptSandboxError('output_too_large', 'The sandbox produced a %.1f MB geometry file; the configured limit is %.1f MB.' % (size / 1048576.0, max_brep_bytes / 1048576.0))
        return brep_path, workdir
    except BaseException:
        cleanup_workdir(workdir)
        raise


def _run_in_sandbox(script: str, workdir: str, wall_timeout_s: float, cpu_seconds: float, memory_bytes: int, request_id: str | None = None) -> str:
    """Worker lifecycle. Returns the BREP path only when the sandbox reports success."""
    payload_path = os.path.join(workdir, 'payload.json')
    runner_path = os.path.join(workdir, 'runner.py')
    brep_path = os.path.join(workdir, 'result.brep')
    ready_path = os.path.join(workdir, 'ready.txt')
    go_path = os.path.join(workdir, 'go.txt')
    with open(payload_path, 'w', encoding='utf-8') as handle:
        json.dump({'script': script}, handle)
    with open(runner_path, 'w', encoding='utf-8') as handle:
        handle.write(RUNNER_CHILD)

    env = {key: value for key, value in {
        'SYSTEMROOT': os.environ.get('SYSTEMROOT', ''),
        'SystemDrive': os.environ.get('SystemDrive', 'C:'),
        'USERPROFILE': os.environ.get('USERPROFILE', os.path.expanduser('~')),
        'HOMEDRIVE': os.environ.get('HOMEDRIVE', ''),
        'HOMEPATH': os.environ.get('HOMEPATH', ''),
        'APPDATA': os.environ.get('APPDATA', ''),
        'LOCALAPPDATA': os.environ.get('LOCALAPPDATA', ''),
        'TEMP': workdir,
        'TMP': workdir,
        'PATH': os.path.dirname(sys.executable),
    }.items() if value}
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

    job = None
    proc = None
    try:
        job = _job(cpu_seconds, memory_bytes)
        try:
            proc = subprocess.Popen(
                [sys.executable, '-I', '-E', '-s', runner_path, brep_path, payload_path, ready_path, go_path],
                cwd=workdir, env=env, creationflags=creationflags,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise ScriptSandboxError('startup_failed', 'The sandbox worker could not be started.',
                                     type(exc).__name__) from None
        if not _assign(job, proc):
            raise _killed_error(proc, cpu_seconds, 'startup_failed',
                                'The sandbox worker could not be attached to its resource limits.')
        if request_id is not None:
            _store_run(request_id, proc, job)
        deadline = time.monotonic() + wall_timeout_s
        while not os.path.isfile(ready_path):
            if proc.poll() is not None:
                raise _killed_error(proc, cpu_seconds, 'startup_failed',
                                    'The sandbox worker stopped during startup.')
            if time.monotonic() >= deadline:
                raise ScriptSandboxError('timeout', 'The generated script sandbox did not start within %.0f seconds.' % wall_timeout_s)
            time.sleep(0.01)
        with open(go_path, 'w') as handle:
            handle.write('go')
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _kill_tree(job, proc)
            raise ScriptSandboxError('timeout', 'The CadQuery script ran longer than %.0f seconds and was terminated.' % wall_timeout_s)

        status = None
        if os.path.isfile(brep_path + '.status'):
            try:
                with open(brep_path + '.status', encoding='utf-8') as handle:
                    status = json.load(handle)
            except Exception:
                status = None
        if status and status.get('ok'):
            if os.path.isfile(brep_path):
                return brep_path
            status = {'ok': False, 'code': 'no_output', 'message': 'The sandbox reported success but produced no geometry file.', 'detail': ''}
        if status and status.get('ok') is False:
            code = str(status.get('code') or 'internal_error')[:60]
            message = str(status.get('message') or 'The sandboxed script failed.')[:400]
            raise ScriptSandboxError(code, message, _scrub(status.get('detail') or '', workdir))
        raise _killed_error(proc, cpu_seconds, 'crashed',
                            'The sandbox worker stopped without a diagnosis.')
    finally:
        if request_id is not None:
            _pop_run(request_id)
        _kill_tree(job, proc)
        if job is not None:
            try:
                _kernel32.CloseHandle(job)
            except Exception:
                pass


def cleanup_workdir(workdir: str):
    """Remove the work directory. Job-node termination is asynchronous and a
    dying descendant can still hold the directory as its current working
    directory, so removal is retried briefly instead of silently skipped."""
    for delay in (0.0, 0.05, 0.1, 0.2, 0.4, 0.8):
        if delay:
            time.sleep(delay)
        shutil.rmtree(workdir, ignore_errors=True)
        if not os.path.isdir(workdir):
            return


# ---------------------------------------------------------------------------
# Request-specific cancellation support for the sandbox worker.
#
# Active runs are keyed by request_id so cancellation can terminate only the
# targeted worker process tree via its Job Object. Stale entries are removed
# when the run completes (success or failure) via _run_in_sandbox's finally
# block, and explicitly by cancel_run.
# ---------------------------------------------------------------------------
_active_runs: dict[str, dict] = {}
_active_runs_lock = threading.Lock()


def _store_run(request_id: str, proc: "subprocess.Popen[str]", job: object) -> None:
    with _active_runs_lock:
        _active_runs[request_id] = {'proc': proc, 'job': job}


def _pop_run(request_id: str) -> dict | None:
    with _active_runs_lock:
        return _active_runs.pop(request_id, None)


def cancel_run(request_id: str) -> bool:
    """Terminate only the sandbox worker tree belonging to request_id.

    Returns True when a live run was found and terminated, False when no
    active run exists for that request ID (already completed, never started,
    or unknown). Safe to call repeatedly (idempotent).
    """
    entry = _pop_run(request_id)
    if entry is None:
        return False
    proc = entry.get('proc')
    job = entry.get('job')
    if proc is not None and proc.poll() is None:
        try:
            if job is not None:
                _kernel32.TerminateJobObject(job, 1)
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
    return True


def has_active_run(request_id: str) -> bool:
    with _active_runs_lock:
        return request_id in _active_runs
