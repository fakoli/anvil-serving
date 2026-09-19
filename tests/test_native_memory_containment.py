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


def test_prepare_preview_does_not_create_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    result = containment.prepare(path, dry_run=True, runner=lambda *a, **k: pytest.fail('compiled'))
    assert result['outcome'] == 'preview'
    assert not path.exists()
    assert result['max_direct_allocation_bytes_per_child'] == 320 * 1024**2


def test_prepare_retains_identity_without_elevating(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    result = containment.prepare(path, confirm=True, runner=compile_runner)
    assert result['outcome'] == 'prepared'
    assert len(result['binary_sha256']) == 64
    assert (path / 'manifest.json').exists()
    assert result['privileged_execution_performed'] is False


def test_prepare_refuses_existing_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    result = containment.prepare(tmp_path, confirm=True, runner=lambda *a, **k: pytest.fail('compiled'))
    assert result['outcome'] == 'blocked'


def test_execution_requires_separate_privileged_authorization(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    result = containment.execute(tmp_path, confirm=True, runner=lambda *a, **k: pytest.fail('executed'))
    assert result['outcome'] == 'blocked'
    assert result['error'] == 'privileged_probe_authorization_required'


def test_modified_prepared_binary_refuses_sudo(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    containment.prepare(path, confirm=True, runner=compile_runner)
    binary = path / 'helper'
    binary.chmod(0o700)
    binary.write_bytes(b'changed')
    result = containment.execute(path, confirm=True, allow_privileged=True, expected_binary_sha256=APPROVED_HASH, runner=lambda *a, **k: pytest.fail('executed'))
    assert result['outcome'] == 'blocked'
    assert result['error'] == 'prepared_helper_identity_changed'


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


def test_manifest_rewrite_cannot_replace_approved_binary(monkeypatch, tmp_path):
    import hashlib
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    prepared = containment.prepare(path, confirm=True, runner=compile_runner)
    binary = path / 'helper'
    binary.chmod(0o700)
    binary.write_bytes(b'replacement')
    manifest = json.loads((path / 'manifest.json').read_text())
    manifest['binary_sha256'] = hashlib.sha256(b'replacement').hexdigest()
    (path / 'manifest.json').write_text(json.dumps(manifest))
    result = containment.execute(path, confirm=True, allow_privileged=True,
        expected_binary_sha256=prepared['binary_sha256'], runner=lambda *a, **k: pytest.fail('executed'))
    assert result['outcome'] == 'blocked'


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


def test_three_cells_stage_only_approved_bytes_and_still_require_review(monkeypatch, tmp_path):
    import base64
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    containment.prepare(path, confirm=True, runner=compile_runner)
    calls = []
    def runner(argv, **kwargs):
        if argv[0] == 'sysctl':
            return subprocess.CompletedProcess(argv, 0, 'used = 0.00M', '')
        calls.append(argv)
        assert argv[:6] == ['sudo', '-n', '--', '/bin/sh', '-c', containment.ROOT_STAGE]
        assert argv[-2] == APPROVED_HASH
        assert base64.b64decode(kwargs['input']) == b'test-helper'
        events = [
            {'event': 'limit', 'limit_mib': 256, 'uid': os.getuid(), 'gid': os.getgid(),
             'groups': 0, 'active_fatal': True, 'inactive_fatal': True, 'pid': 123, 'unix_time': 1},
            {'event': 'sample', 'allocated_bytes': 16*1024**2, 'footprint': 200*1024**2, 'metal_bytes': 16*1024**2},
            {'event': 'root_stage_cleanup', 'ok': True}]
        return subprocess.CompletedProcess(argv, 137, '\n'.join(json.dumps(e) for e in events), '')
    result = containment.execute(path, confirm=True, allow_privileged=True,
        expected_binary_sha256=APPROVED_HASH, runner=runner)
    assert [call[-1] for call in calls] == ['shared', 'private', 'mmap']
    assert result['outcome'] == 'needs_review'
    assert result['hard_memory_containment_proven'] is False
    assert result['target_model_trial_authorized'] is False
    assert result['swap_after_bytes'] == 0


def test_execute_preview_validates_identity_without_running(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    containment.prepare(path, confirm=True, runner=compile_runner)
    result = containment.execute(path, dry_run=True, expected_binary_sha256=APPROVED_HASH,
        runner=lambda *a, **k: pytest.fail('process started'))
    assert result['outcome'] == 'preview'
    assert result['binary_sha256'] == APPROVED_HASH


@pytest.mark.parametrize('action', ['--prepare', '--execute'])
def test_unwritable_evidence_prevents_any_action(monkeypatch, tmp_path, action):
    monkeypatch.setattr(containment, 'prepare', lambda *a, **k: pytest.fail('compiled'))
    monkeypatch.setattr(containment, 'execute', lambda *a, **k: pytest.fail('executed'))
    with pytest.raises(SystemExit):
        containment.main([action, 'helper', '--confirm', '--output', str(tmp_path / 'missing' / 'result.json')])


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


def test_interrupted_sample_retains_cleanup_and_stops_further_cells(monkeypatch, tmp_path):
    monkeypatch.setattr(containment.platform, 'system', lambda: 'Darwin')
    path = tmp_path / 'helper'
    containment.prepare(path, confirm=True, runner=compile_runner)
    cleanup = {'event': 'root_stage_cleanup', 'ok': True}
    def runner(argv, **kwargs):
        if argv[0] == 'sysctl':
            return subprocess.CompletedProcess(argv, 0, 'used = 0.00M', '')
        raw = '{"event":"limit","pid":123}\n{"event":"sample","footprint":\n' + json.dumps(cleanup) + '\n'
        return subprocess.CompletedProcess(argv, 137, raw, '')
    result = containment.execute(path, confirm=True, allow_privileged=True,
        expected_binary_sha256=APPROVED_HASH, runner=runner)
    assert result['cells'][0]['events'][-1] == cleanup
    assert result['cells'][0]['malformed_record_lines'] == [2]
    assert result['cells'][0]['root_stage_cleanup_confirmed'] is True
    assert result['error'] == 'malformed_helper_evidence'
    assert len(result['cells']) == 1
    assert result['hard_memory_containment_proven'] is False
