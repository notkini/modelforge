import pytest

from .script_runner import ScriptSandboxError, cleanup_workdir, run_script


BOX_SCRIPT = '''# plan-dimensions: 30,20,10
# explanation: A simple box used by the sandbox test.
import cadquery as cq
import math

result = cq.Workplane("XY").box(30.0, 20.0, 10.0)
'''


def _sandbox_error(function, *args, **kwargs):
    with pytest.raises(ScriptSandboxError) as caught:
        function(*args, **kwargs)
    return caught.value


def test_run_script_executes_and_exports_brep():
    workdir = None
    try:
        brep_path, workdir = run_script(BOX_SCRIPT)
        assert brep_path.endswith('.brep')
        with open(brep_path, 'r', encoding='utf-8', errors='ignore') as handle:
            assert len(handle.read()) > 0
    finally:
        if workdir:
            cleanup_workdir(workdir)


def test_run_script_rejects_invalid_payload():
    with pytest.raises(ScriptSandboxError) as caught:
        run_script('')
    assert caught.value.code == 'invalid_payload'
    with pytest.raises(ScriptSandboxError) as caught:
        run_script('x' * (100_001))
    assert caught.value.code == 'script_too_large'


def test_run_script_blocks_direct_stdlib_import():
    script = BOX_SCRIPT.replace('import cadquery as cq', 'import os\nimport cadquery as cq')
    error = _sandbox_error(run_script, script, wall_timeout_s=60, cpu_seconds=30)
    assert error.code == 'exception'
    assert 'not allowed to import os' in error.detail


def test_run_script_blocks_dotted_import_escape():
    script = BOX_SCRIPT.replace('import cadquery as cq',
                                'import cadquery as cq\nfrom cadquery.occ_impl.exporters import os')
    error = _sandbox_error(run_script, script, wall_timeout_s=60, cpu_seconds=30)
    assert error.code == 'exception'
    assert 'not allowed to import cadquery.occ_impl.exporters' in error.detail


def test_run_script_blocks_dangerous_builtins():
    for call in ("open('probe.txt', 'w')", 'eval("1 + 1")', 'exec("1")', 'compile("1", "x", "eval")'):
        script = BOX_SCRIPT.replace('result = ', f'{call}\nresult = ')
        error = _sandbox_error(run_script, script, wall_timeout_s=60, cpu_seconds=30)
        assert error.code == 'exception', call
        assert 'not allowed to call' in error.detail, call


def test_run_script_blocks_cq_attribute_escape():
    script = BOX_SCRIPT.replace('result = cq.Workplane("XY").box(30.0, 20.0, 10.0)',
                                'cwd = cq.os.getcwd()\nresult = cq.Workplane("XY").box(30.0, 20.0, 10.0)')
    error = _sandbox_error(run_script, script, wall_timeout_s=60, cpu_seconds=30)
    assert error.code == 'exception'
    assert 'AttributeError' in error.detail


def test_run_script_reports_missing_result():
    script = BOX_SCRIPT.replace('result = ', 'part = ')
    error = _sandbox_error(run_script, script, wall_timeout_s=60, cpu_seconds=30)
    assert error.code == 'no_result'
    assert error.message.startswith('The CadQuery script never assigned a geometry object')


def test_run_script_enforces_cpu_limit():
    script = BOX_SCRIPT.replace('result = ', 'counter = 0\nwhile True:\n    counter += 1\nresult = ')
    error = _sandbox_error(run_script, script, wall_timeout_s=30, cpu_seconds=2.0)
    assert error.code == 'cpu_limit'
    assert 'CPU time budget' in error.message


def test_run_script_enforces_wall_timeout():
    script = BOX_SCRIPT.replace('result = ', 'counter = 0\nwhile True:\n    counter += 1\nresult = ')
    error = _sandbox_error(run_script, script, wall_timeout_s=2.0, cpu_seconds=120.0)
    assert error.code == 'timeout'
    assert 'ran longer than 2 seconds' in error.message
