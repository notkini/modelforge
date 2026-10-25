"""Security regression tests for the Gemini CadQuery script sandbox.

Each test names the guarantee it establishes. Two kinds of tests live here:

- GUARANTEE tests: verify an implemented protection (resource limits,
  cleanup, error mapping, env scrubbing).
- LIMITATION TRIPWIRE tests: empirically document that a boundary does NOT
  exist yet (filesystem reads, socket creation, child execution). If one of
  these fails after an isolation boundary is added, the sandbox security
  document must be updated and the test re-labelled.

Import/builtin guards themselves are exercised in test_sandbox.py.
All payloads are harmless: fail-fast probes, localhost-only binds with no
connections, the system's own timer binary, and per-test sentinel files.
"""
import os
import sys
import tempfile
import uuid

import pytest

from . import codegen
from .script_runner import ScriptSandboxError, cleanup_workdir, run_script
from .test_sandbox import BOX_SCRIPT, _sandbox_error


def _probe_script(body: str) -> str:
    """BOX_SCRIPT plus a harmless probe body; the probe raises, which is how it reports."""
    return BOX_SCRIPT + '\n' + body.rstrip() + '\n'


def _reached(name):
    """Probe body that locates a module object inside the worker purely through
    attribute traversal (helper-class ``__globals__`` maps). Reports
    'UNREACHABLE:<name>' when not found."""
    return """
found = None
for obj in ().__class__.__base__.__subclasses__():
    try:
        g = obj.__init__.__globals__
    except Exception:
        continue
    if %r in g:
        found = g[%r]
        break
if found is None:
    raise RuntimeError('UNREACHABLE:%s')
""" % (name, name, name)


def test_env_secrets_absent_from_worker():
    """GUARANTEE: application secrets never enter the worker environment.

    Proves the env-scrub layer only. Does NOT prove filesystem/network isolation.
    """
    body = _reached('os') + """
secret_hits = [k for k in ('GEMINI_API_KEY', 'SESSION_SECRET', 'DATABASE_URL',
                           'GOOGLE_CLIENT_ID', 'FRONTEND_ORIGIN', 'ARTIFACT_DIR')
               if k in found.environ]
raise RuntimeError('ENVPROBE|%s' % (','.join(secret_hits) or 'none'))
"""
    error = _sandbox_error(run_script, _probe_script(body), wall_timeout_s=60)
    assert error.code == 'exception'
    assert 'ENVPROBE|none' in error.detail


def test_stdlib_file_read_outside_workspace_not_blocked_limitation(tmp_path):
    """LIMITATION TRIPWIRE: filesystem access is NOT isolated.

    The io module is reachable through attribute traversal and reads a benign
    sentinel file that lives outside the worker workspace. If this test fails
    after a real isolation boundary was added, update SANDBOX_SECURITY.md and
    re-label this test as a boundary assertion.
    """
    sentinel = tmp_path / 'sentinel.txt'
    sentinel.write_text('MF-SENTINEL-XYZ', encoding='utf-8')
    body = _reached('io')
    body += """
with found.open(r'{SENTINEL}', 'r') as handle:
    content = handle.read(15)
raise RuntimeError('READPROBE|%s' % content)
""".replace('{SENTINEL}', str(sentinel).replace('\\', '/'))
    error = _sandbox_error(run_script, _probe_script(body), wall_timeout_s=60)
    assert error.code == 'exception'
    assert 'READPROBE|MF-SENTINEL-XYZ' in error.detail


def test_loopback_socket_creation_not_blocked_limitation():
    """LIMITATION TRIPWIRE: socket creation is NOT blocked.

    Binds and listens on a random localhost port with no external addresses and
    no connections. Proves the socket path is reachable; outbound reachability
    is NOT asserted here, and remote network access remains possible.
    """
    body = """
found = None
for obj in ().__class__.__base__.__subclasses__():
    try:
        g = obj.__init__.__globals__
    except Exception:
        continue
    sysm = g.get('sys')
    if sysm is not None and hasattr(sysm, 'modules'):
        sockmod = sysm.modules.get('socket')
        if sockmod is not None:
            found = sockmod
            break
if found is None:
    raise RuntimeError('SOCKET-UNREACHABLE')
sock = found.socket(found.AF_INET, found.SOCK_STREAM)
sock.bind(('127.0.0.1', 0))
sock.listen(1)
port = sock.getsockname()[1]
sock.close()
raise RuntimeError('SOCKPROBE|port=%d' % port)
"""
    error = _sandbox_error(run_script, _probe_script(body), wall_timeout_s=60)
    assert error.code == 'exception'
    assert 'SOCKPROBE|port=' in error.detail


def test_spawned_child_contained_until_wall_timeout():
    """CONTAINMENT TEST: child execution is possible but the whole process
    tree is terminated at the parent wall timeout.

    Spawns the system's own ping against 127.0.0.1 with P_NOWAIT (loopback
    only: no external network, no writes), then the worker exceeds the wall
    deadline. Proves wall-timeout enforcement and Job kill-on-close of the
    worker plus its child; the child code path is expressly NOT denied.
    """
    body = """
found = None
sysm = None
for obj in ().__class__.__base__.__subclasses__():
    try:
        g = obj.__init__.__globals__
    except Exception:
        continue
    if 'os' in g and found is None:
        found = g['os']
    if 'sys' in g and sysm is None:
        sysm = g['sys']
tm = sysm.modules.get('time') if sysm is not None else None
if found is None or tm is None:
    raise RuntimeError('TRAVERSAL-UNREACHABLE')
cmd = found.path.join(found.environ.get('SYSTEMROOT', ''), 'System32', 'ping.exe')
pid = found.spawnv(found.P_NOWAIT, cmd, ['ping', '-n', '120', '-w', '1000', '127.0.0.1'])
if pid is None or pid <= 0:
    raise RuntimeError('SPAWN-FAILED|%s' % pid)
tm.sleep(120)
raise RuntimeError('sleep returned')
"""
    error = _sandbox_error(run_script, _probe_script(body), wall_timeout_s=4.0, cpu_seconds=300)
    assert error.code == 'timeout'


def test_startup_failure_maps_cleanly(monkeypatch):
    """GUARANTEE: worker startup failures map to a controlled error and the
    temporary directory is removed."""
    monkeypatch.setattr(sys, 'executable', r'C:\definitely-missing-dir\python.exe')
    error = _sandbox_error(run_script, BOX_SCRIPT, wall_timeout_s=30)
    assert error.code == 'startup_failed'


def _mf_script_workdirs():
    root = tempfile.gettempdir()
    return {name for name in os.listdir(root) if name.startswith('mf-script-')}


def test_no_temp_workdirs_leak_after_failures():
    """GUARANTEE: failed runs remove their temporary work directory."""
    before = _mf_script_workdirs()
    error = _sandbox_error(run_script, 'x' * 100_001)
    assert error.code == 'script_too_large'
    escape = BOX_SCRIPT.replace('import cadquery as cq',
                                'import cadquery.occ_impl.exporters as boom\nimport cadquery as cq')
    error = _sandbox_error(run_script, escape, wall_timeout_s=60)
    assert error.code == 'exception'
    after = _mf_script_workdirs()
    assert not after - before


def test_brep_output_size_cap(monkeypatch):
    """GUARANTEE: oversized BREP output is rejected before import/validation."""
    monkeypatch.setenv('MF_MAX_BREP_BYTES', '512')
    error = _sandbox_error(run_script, BOX_SCRIPT, wall_timeout_s=60)
    assert error.code == 'output_too_large'
    assert 'configured limit is 0.0 MB' in error.message


def test_limit_env_overrides_are_validated(monkeypatch):
    """GUARANTEE: unreadable limit overrides fall back to safe defaults
    instead of disabling the guard."""
    monkeypatch.setenv('MF_MAX_BREP_BYTES', 'not-a-number')
    monkeypatch.setenv('MF_SANDBOX_WALL_TIMEOUT_S', 'nan')
    workdir = None
    try:
        brep_path, workdir = run_script(BOX_SCRIPT)
        assert os.path.getsize(brep_path) > 0
    finally:
        if workdir:
            cleanup_workdir(workdir)


def test_mesh_complexity_caps_reject_excess(monkeypatch):
    """GUARANTEE: tessellated-mesh size is bounded server-side (DoS guard
    against expensive geometry coming back from the sandbox)."""
    monkeypatch.setenv('MF_MAX_MESH_VERTICES', '8')
    monkeypatch.setenv('MF_MAX_MESH_FACES', '8')
    from .cad_geometry import cadquery_module, validate_from_cad_shape
    shape = cadquery_module().Workplane('XY').box(30.0, 20.0, 10.0).val()
    with pytest.raises(ValueError) as caught:
        validate_from_cad_shape(shape, [30.0, 20.0, 10.0])
    assert 'configured limits' in str(caught.value)


def test_solid_count_cap_rejects_excess(monkeypatch):
    """GUARANTEE: solid count is bounded server-side."""
    monkeypatch.setenv('MF_MAX_CAD_SOLIDS', '1')
    from .cad_geometry import cadquery_module, validate_from_cad_shape
    cq = cadquery_module()
    first = cq.Workplane('XY').box(30.0, 20.0, 10.0).val()
    second = cq.Workplane('XY').box(30.0, 20.0, 10.0).translate((0, 0, 11)).val()
    with pytest.raises(ValueError) as caught:
        validate_from_cad_shape(cq.Compound.makeCompound([first, second]), [30.0, 20.0, 21.0])
    assert 'contains 2 solids' in str(caught.value)


def _fenced(script: str) -> str:
    return '```python\n' + script + '\n```'


def test_api_sandbox_errors_expose_no_internal_paths(client, monkeypatch):
    """GUARANTEE: API error bodies carry only the safe message; server paths
    and internals stay out of responses. Sandbox codes map to 503."""
    sentinel_path = r'C:\Users\redacted\AppData\Local\Temp\mf-script-%s\payload.json' % uuid.uuid4().hex
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(codegen, '_call_gemini', lambda message: _fenced(BOX_SCRIPT))
    monkeypatch.setattr(codegen, 'run_script', lambda script: (_ for _ in ()).throw(
        ScriptSandboxError('timeout', 'The CadQuery script ran longer than 90 seconds and was terminated.',
                           detail=sentinel_path)))
    conv = client.post('/api/conversations', json={'title': 'Hygiene'}).json()
    failed = client.post('/api/models/generate', json={'conversation_id': conv['id'], 'prompt': 'make anything'})
    assert failed.status_code == 503, failed.text
    assert set(failed.json()['detail'].keys()) == {'message'}
    assert 'mf-script' not in failed.text and 'payload.json' not in failed.text

    monkeypatch.setattr(codegen, 'run_script', lambda script: (_ for _ in ()).throw(
        ScriptSandboxError('output_too_large', 'The sandbox produced an oversized geometry file.')))
    conv2 = client.post('/api/conversations', json={'title': 'Hygiene 2'}).json()
    failed = client.post('/api/models/generate', json={'conversation_id': conv2['id'], 'prompt': 'make anything'})
    assert failed.status_code == 503, failed.text
    assert failed.json()['detail']['message'] == 'The sandbox produced an oversized geometry file.'
