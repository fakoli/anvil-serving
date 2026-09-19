"""Safety contracts for the bounded native Metal accounting probe."""
import json
import subprocess
from pathlib import Path

import pytest

from anvil_serving import native_memory_probe as probe


class Runner:
    def __init__(self, *, child=None, swap_after=0, failure=None):
        self.calls = []
        self.swaps = 0
        self.child = child or {
            'allocated_bytes': 33554432, 'gpu_completed': True, 'metal_before': 0, 'metal_after': 33554432,
            'footprint_before': 10000000, 'footprint_after': 43554432, 'footprint_peak': 43554432,
            'accounting_observed': True, 'footprint_limit_permission_denied': True,
        }
        self.swap_after = swap_after
        self.failure = failure

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert kwargs['timeout'] <= 60
        if argv[:2] == ['sysctl', '-n']:
            self.swaps += 1
            used = self.swap_after if self.swaps > 1 else 0
            return subprocess.CompletedProcess(argv, 0, f'total = 0.00M used = {used:.2f}M free = 0.00M (encrypted)', '')
        if argv[0] == 'xcrun':
            Path(argv[-1]).write_bytes(b'fake-compiled-probe')
            return subprocess.CompletedProcess(argv, 0, '', '')
        if self.failure:
            raise subprocess.TimeoutExpired(argv, 10)
        return subprocess.CompletedProcess(argv, 0, json.dumps(self.child), '')


def test_preview_never_compiles_or_allocates(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    result = probe.run_probe(dry_run=True, runner=lambda *a, **k: pytest.fail('executed'))
    assert result['outcome'] == 'preview'
    assert result['allocation_bytes'] == 33554432
    assert result['target_model_trial_authorized'] is False


def test_non_mac_refuses_before_any_process(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Linux')
    result = probe.run_probe(confirm=True, runner=lambda *a, **k: pytest.fail('executed'))
    assert result['outcome'] == 'blocked'


def test_success_is_only_accounting_evidence(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    runner = Runner()
    result = probe.run_probe(confirm=True, runner=runner)
    assert result['outcome'] == 'accounting_observed'
    assert result['target_model_trial_authorized'] is False
    assert result['hard_memory_containment_proven'] is False
    assert result['swap_change_bytes'] == 0
    assert len(result['binary_sha256']) == 64
    assert runner.swaps == 2


def test_swap_growth_fails_even_when_child_accounting_passes(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    result = probe.run_probe(confirm=True, runner=Runner(swap_after=1))
    assert result['outcome'] == 'failed'
    assert result['swap_change_bytes'] == 1048576


def test_timeout_preserves_post_swap_measurement(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    runner = Runner(failure='timeout')
    result = probe.run_probe(confirm=True, runner=runner)
    assert result['outcome'] == 'failed'
    assert runner.swaps == 2
    assert result['error'] == 'probe_process_timeout'


def test_invalid_child_payload_cannot_claim_accounting(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    result = probe.run_probe(confirm=True, runner=Runner(child={'accounting_observed': True}))
    assert result['outcome'] == 'failed'


def test_unconfirmed_invocation_is_only_preview(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    assert probe.run_probe(runner=lambda *a, **k: pytest.fail('executed'))['outcome'] == 'preview'


def test_cli_confirmation_reaches_probe_and_retains_artifact(monkeypatch, tmp_path, capsys):
    from anvil_serving import cli
    seen = []
    def fake_probe(**kwargs):
        seen.append(kwargs)
        return {'outcome': 'accounting_observed', 'target_model_trial_authorized': False}
    monkeypatch.setattr(probe, 'run_probe', fake_probe)
    output = tmp_path / 'probe.json'
    assert cli.main(['host', 'native-memory-probe', '--confirm', '--output', str(output)]) == 0
    assert seen == [{'confirm': True, 'dry_run': False}]
    assert json.loads(output.read_text())['target_model_trial_authorized'] is False


def test_gpu_failure_does_not_pass_cpu_accounting(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    runner = Runner()
    runner.child['gpu_completed'] = False
    assert probe.run_probe(confirm=True, runner=runner)['outcome'] == 'failed'


def test_dry_run_preserves_existing_output(monkeypatch, tmp_path, capsys):
    from anvil_serving import cli
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    output = tmp_path / 'evidence.json'
    output.write_text('preserved')
    assert cli.main(['host', 'native-memory-probe', '--dry-run', '--output', str(output)]) == 0
    assert output.read_text() == 'preserved'


def test_root_refused_before_compilation_or_other_process(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(probe.os, 'geteuid', lambda: 0, raising=False)
    result = probe.run_probe(confirm=True, runner=lambda *a, **k: pytest.fail('executed'))
    assert result['outcome'] == 'blocked'
    assert result['error'] == 'unprivileged_user_required'


@pytest.mark.skipif(__import__('os').name != 'posix', reason='POSIX process groups')
def test_timeout_kills_compiler_descendant_before_it_can_write(tmp_path):
    import sys
    import time
    marker = tmp_path / 'descendant-survived'
    child = "import pathlib,time; time.sleep(1.5); pathlib.Path(%r).write_text('bad')" % str(marker)
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); print('ready',flush=True); time.sleep(30)" % child
    with pytest.raises(subprocess.TimeoutExpired):
        probe._bounded_run([sys.executable, '-c', parent], capture_output=True, text=True, timeout=0.5)
    time.sleep(1.6)
    assert not marker.exists()


@pytest.mark.parametrize('target', ['missing-parent', 'existing', 'directory'])
def test_unusable_output_prevents_probe(monkeypatch, tmp_path, target):
    output = tmp_path / 'result.json'
    if target == 'missing-parent':
        output = tmp_path / 'missing' / 'result.json'
    elif target == 'existing':
        output.write_text('preserve')
    else:
        output.mkdir()
    monkeypatch.setattr(probe, 'run_probe', lambda **k: pytest.fail('probe executed'))
    with pytest.raises(SystemExit):
        probe.main(['--confirm', '--output', str(output)])
    if target == 'existing':
        assert output.read_text() == 'preserve'


def test_evidence_is_reserved_before_probe(monkeypatch, tmp_path, capsys):
    output = tmp_path / 'result.json'
    def fake_probe(**kwargs):
        assert json.loads(output.read_text())['outcome'] == 'started'
        return {'outcome': 'accounting_observed'}
    monkeypatch.setattr(probe, 'run_probe', fake_probe)
    assert probe.main(['--confirm', '--output', str(output)]) == 0
    assert json.loads(output.read_text())['outcome'] == 'accounting_observed'


def test_timeout_and_post_swap_failure_are_both_retained(monkeypatch):
    monkeypatch.setattr(probe.platform, 'system', lambda: 'Darwin')
    base = Runner(failure='timeout')
    def runner(argv, **kwargs):
        if argv[0] == 'sysctl' and base.swaps:
            raise OSError('unavailable')
        return base(argv, **kwargs)
    result = probe.run_probe(confirm=True, runner=runner)
    assert result['error'] == 'probe_process_timeout'
    assert result['post_swap_error'] == 'post_swap_measurement_unavailable'
    assert result['outcome'] == 'failed'
