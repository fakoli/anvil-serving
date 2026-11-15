"""The privileged canary is inert until preparation and explicit execution gates."""
import json
import os
import hashlib
from types import SimpleNamespace
from pathlib import Path
import subprocess

import pytest

from anvil_serving import native_memory_containment as containment

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native macOS ownership boundary")
APPROVED_HASH = hashlib.sha256(b"test-helper").hexdigest()

@pytest.fixture(autouse=True)
def scratch_space(monkeypatch):
    monkeypatch.setattr(containment.shutil, "disk_usage", lambda p: SimpleNamespace(free=2*1024**3))


def compile_runner(argv, **kwargs):
    assert argv[:2] == ['xcrun', 'clang']
    Path(argv[-1]).write_bytes(b'test-helper')
    return subprocess.CompletedProcess(argv, 0, '', '')


def test_execution_requires_separate_privileged_authorization(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    result = containment.execute(tmp_path, confirm=True, runner=lambda *a, **k: pytest.fail('executed'))
    assert result['outcome'] == 'blocked'
    assert result['error'] == 'privileged_probe_authorization_required'


def test_unprivileged_sudo_failure_never_authorizes_model(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    containment.prepare(path, confirm=True, runner=compile_runner)
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[0] == 'sysctl':
            return subprocess.CompletedProcess(argv, 0, 'total = 0.00M used = 0.00M free = 0.00M', '')
        return subprocess.CompletedProcess(argv, 1, '', 'password required')
    result = containment.execute(path, confirm=True, allow_privileged=True, expected_binary_sha256=APPROVED_HASH, runner=runner)
    assert result['outcome'] == 'failed'
    assert result['target_model_trial_authorized'] is False
    assert len([x for x in calls if x[0] == 'sudo']) == 1
    assert 'password' not in json.dumps(result)


def test_timeout_still_records_post_swap(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    containment.prepare(path, confirm=True, runner=compile_runner)
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[0] == 'sysctl':
            return subprocess.CompletedProcess(argv, 0, 'used = 0.00M', '')
        raise subprocess.TimeoutExpired(argv, 15)
    result = containment.execute(path, confirm=True, allow_privileged=True, expected_binary_sha256=APPROVED_HASH, runner=runner)
    assert result['swap_after_bytes'] == 0
    assert len([x for x in calls if x[0] == 'sysctl']) == 2


def test_real_cli_forwards_separate_privilege_gate(monkeypatch, tmp_path, capsys):
    from anvil_serving import cli
    seen = []
    def fake_execute(path, **kwargs):
        seen.append((path, kwargs))
        return {'outcome': 'needs_review', 'target_model_trial_authorized': False}
    monkeypatch.setattr(containment, 'execute', fake_execute)
    output = tmp_path / 'result.json'
    assert cli.main(['host', 'native-memory-containment', '--execute', 'prepared',
                     '--confirm', '--allow-privileged-probe', '--expected-binary-sha256', APPROVED_HASH, '--output', str(output)]) == 1
    assert seen == [('prepared', {'confirm': True, 'dry_run': False, 'allow_privileged': True, 'expected_binary_sha256': APPROVED_HASH})]
    assert json.loads(output.read_text())['target_model_trial_authorized'] is False


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='macOS staging utilities')
@pytest.mark.parametrize('matching', [True, False])
def test_staging_verifies_stdin_and_cleans_without_root(tmp_path, matching):
    import base64
    payload = b'#!/bin/sh\nexit 7\n'
    expected = hashlib.sha256(payload).hexdigest() if matching else '0' * 64
    script = containment.ROOT_STAGE.replace('/private/tmp/anvil-memory-canary.', str(tmp_path / 'canary.'))
    result = containment._bounded_run(['/bin/sh', '-c', script, 'canary', expected, 'shared'],
        input=base64.b64encode(payload).decode(), capture_output=True, text=True, timeout=5)
    assert result.returncode == (7 if matching else 31)
    assert not list(tmp_path.iterdir())
    if matching:
        assert json.loads(result.stdout.splitlines()[-1]) == {'event': 'root_stage_cleanup', 'ok': True}


def test_canary_timeout_retains_stage_and_primary_error_when_swap_query_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    containment.prepare(path, confirm=True, runner=compile_runner)
    swaps = 0
    stage = {'event': 'root_stage', 'path': '/private/tmp/anvil-memory-canary.EXAMPLE'}
    def runner(argv, **kwargs):
        nonlocal swaps
        if argv[0] == 'sysctl':
            swaps += 1
            if swaps > 1:
                raise OSError('unavailable')
            return subprocess.CompletedProcess(argv, 0, 'used = 0.00M', '')
        raise subprocess.TimeoutExpired(argv, 15, output=(json.dumps(stage) + '\n').encode())
    result = containment.execute(path, confirm=True, allow_privileged=True,
        expected_binary_sha256=APPROVED_HASH, runner=runner)
    assert result['error'] == 'canary_timeout_cleanup_unverified'
    assert result['post_swap_error'] == 'post_swap_measurement_unavailable'
    assert result['cells'][0]['partial_events'] == [stage]
    assert len(result['cells']) == 1
