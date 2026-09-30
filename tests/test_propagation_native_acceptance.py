"""Fixed native owner lifecycle: real protected evidence and shared validator."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import pytest

from anvil_serving.propagation_native_acceptance import (
    NativeAcceptanceError, NativeFixtureLifecycle, validate_native_runtime,
)


def fixture_profile(tmp_path):
    tmp_path.chmod(0o700)
    executable = tmp_path / 'hermes'
    executable.write_text('#!/bin/sh\nexit 1\n')
    executable.chmod(0o700)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    sessions = [dict(kind=kind, fixture_id=kind + '-fixture',
        fixture_file=str(tmp_path / (kind + '-fixture.json')),
        receipt_file=str(tmp_path / (kind + '-receipt.json')),
        provider='custom', model_id='llm.primary', context_tokens=8192,
        max_output_tokens=512, executable_digest=digest)
        for kind in ('existing_session', 'new_session')]
    target = dict(target_id='target-1', installation_id='install-1', profile_id='profile-1',
        runtime_id='runtime-1', client='hermes', native_sessions=sessions,
        native_runtime=dict(executable=str(executable), fixture_root=str(tmp_path), path=["/usr/bin"]),
        hermes=dict(bin=str(executable), home=str(tmp_path), profile='default'))
    profile = dict(expected_identity_digest='2'*64, targets=[target])
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
    contract = SimpleNamespace(digest='1'*64, value={'deadline_at': deadline})
    return profile, contract


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_owned_native_lifecycle_binds_resume_and_new_evidence(tmp_path):
    profile, contract = fixture_profile(tmp_path)
    calls, histories = [], {}
    def run(argv, timeout, environment):
        calls.append(argv)
        assert 0 < timeout <= 120
        assert argv[1:3] == ('-p', 'default')
        assert '--model' not in argv and '--provider' not in argv
        assert environment['HERMES_HOME'] == str(tmp_path)
        assert environment['PATH'] == str(Path('/usr/bin').resolve())
        prompt = argv[argv.index('-q') + 1]
        probe = Path(re.search(r'"([^"\n]+/probe-[a-f0-9]+)"', prompt)[1])
        marker = probe.read_text()
        resumed = '--resume' in argv
        native_id = argv[argv.index('--resume') + 1] if resumed else f'native-{len(calls)}'
        response = histories[native_id] + ' ' + marker if resumed else marker
        if resumed:
            assert histories[native_id] not in prompt
        histories[native_id] = marker
        Path(argv[argv.index('--usage-file') + 1]).write_text(json.dumps(dict(
            session_id=native_id, provider='custom', model='llm.primary',
            completed=True, failed=False, api_calls=1, context_length=8192,
            max_tokens=512, fallback_used=False)))
        return subprocess.CompletedProcess(argv, 0, response, '')
    lifecycle = NativeFixtureLifecycle(profile, contract, run=run)
    target = profile['targets'][0]
    validate_native_runtime(target)
    assert calls == []
    lifecycle.prepare()
    assert len(calls) == 1
    fixtures = [json.loads(Path(row['fixture_file']).read_text()) for row in target['native_sessions']]
    assert {row['fixture_id'] for row in fixtures} == {'existing_session-fixture', 'new_session-fixture'}
    assert {row['native_session_id'] for row in fixtures} == {'native-1'}
    assert all(not Path(row['receipt_file']).exists() for row in target['native_sessions'])
    completed = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    states = lifecycle.complete(completed, {'target-1': '3'*64})
    assert len(calls) == 3
    assert all(row['state'] == 'accepted' for row in states.values())
    receipts = [json.loads(Path(row['receipt_file']).read_text()) for row in target['native_sessions']]
    assert receipts[0]['native_session_id'] == 'native-1'
    assert receipts[1]['native_session_id'] != 'native-1'
    assert receipts[0]['continuity_kind'] == 'durable_resume'
    assert receipts[1]['continuity_kind'] == 'loaded_fixture'
    assert all(Path(row['receipt_file']).stat().st_mode & 0o077 == 0 for row in target['native_sessions'])
    assert not list(tmp_path.glob('probe-*'))
    with pytest.raises(NativeAcceptanceError, match='already exists'):
        NativeFixtureLifecycle(profile, contract, run=run).prepare()
    assert len(calls) == 3


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_expired_budget_and_changed_executable_never_start_turn(tmp_path):
    profile, contract = fixture_profile(tmp_path)
    def forbidden(*args):
        pytest.fail('invalid preflight started a turn')
    lifecycle = NativeFixtureLifecycle(profile, contract, run=forbidden, budget_seconds=0)
    with pytest.raises(NativeAcceptanceError, match='budget exhausted'):
        lifecycle.prepare()
    assert not list(tmp_path.glob('probe-*'))
    executable = Path(profile['targets'][0]['native_runtime']['executable'])
    executable.write_text('changed')
    with pytest.raises(NativeAcceptanceError, match='differs from its pin'):
        validate_native_runtime(profile['targets'][0])


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_pi_handle_survives_prepare_and_closes_on_catalog_failure(tmp_path):
    profile, contract = fixture_profile(tmp_path)
    target = profile['targets'][0]
    target['client'] = 'pi'
    target['native_runtime'].update(agent_dir=str(tmp_path), loaded_catalog_digest='4'*64)
    handles = []
    class Pi:
        def prepare_existing(self, selected, native_id, *, timeout_seconds):
            assert 0 < timeout_seconds <= 120
            handle = SimpleNamespace(native_session_id=native_id, history_marker='retained-history-marker',
                process_generation='generation-1', prepared_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                closed=False)
            handle.close = lambda: setattr(handle, 'closed', True)
            handles.append(handle)
            return handle
    lifecycle = NativeFixtureLifecycle(profile, contract, run=lambda *args: pytest.fail('unexpected CLI'), pi=Pi())
    lifecycle.prepare()
    assert len(handles) == 1 and handles[0].closed is False
    try:
        raise RuntimeError('synthetic catalog failure')
    except RuntimeError:
        pass
    finally:
        lifecycle.close()
    assert handles[0].closed is True
    assert all(not Path(row['receipt_file']).exists() for row in target['native_sessions'])


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_shared_prefixture_uses_existing_route_even_when_new_check_is_first(tmp_path):
    profile, contract = fixture_profile(tmp_path)
    target = profile['targets'][0]
    for item in target['native_sessions']:
        item['fixture_id'] = 'shared-fixture'
        item['fixture_file'] = str(tmp_path / 'shared-fixture.json')
    target['native_sessions'].reverse()
    lifecycle = NativeFixtureLifecycle(profile, contract, run=lambda *args: pytest.fail('unexpected runner'))
    selected = []
    def turn(_target, item):
        selected.append(item['kind'])
        return {'session_id': 'native-before'}, 'synthetic-history-marker'
    lifecycle._turn = turn
    lifecycle.prepare()
    assert selected == ['existing_session']
    assert json.loads((tmp_path / 'shared-fixture.json').read_text())['fixture_id'] == 'shared-fixture'
    lifecycle.close()


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_web_fixture_lifecycle_uses_owned_loopback_adapter_and_closes(tmp_path):
    profile, contract = fixture_profile(tmp_path)
    target = profile['targets'][0]
    target['client'] = 'pi'
    token = tmp_path / 'acceptance-token'
    token.write_text('t' * 40)
    token.chmod(0o600)
    target['native_runtime'] = dict(kind='pi_web', fixture_root=str(tmp_path), port=4100,
        acceptance_token_file=str(token), bridge_artifact_digest='5'*64, loaded_catalog_digest='6'*64)
    for item in target['native_sessions']:
        item['executable_digest'] = '5'*64
    calls = []
    def receipt(check, native_id, kind, generation):
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        return dict(schema='native-session-acceptance/v1', check_digest=check.digest,
            started_at=stamp, completed_at=stamp, native_session_id=native_id,
            continuity_kind=kind, configured_route=True, provider='custom', model='llm.primary',
            context_tokens=8192, max_output_tokens=512, turn_completed=True, tool_probe_passed=True,
            history_probe_passed=kind == 'same_process', fallback_used=False,
            physical_catalog_digest=check.physical_catalog_digest, process_generation=generation)
    class Web:
        def prepare_existing(self, selected, operation_id, native_id, *, timeout_seconds):
            calls.append('prepare')
            assert selected.acceptance_token == 't'*40
            assert native_id.startswith('propagation-')
            return SimpleNamespace(profile=selected, operation_id=operation_id, native_session_id=native_id,
                process_generation='web-generation', prepared_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                history_marker='PI-FIXTURE-' + 'a'*32, closed=False)
        def complete_existing(self, check, handle, *, timeout_seconds):
            calls.append('existing')
            return receipt(check, handle.native_session_id, 'same_process', handle.process_generation)
        def accept_new(self, check, selected, operation_id, native_id, *, timeout_seconds):
            calls.append('new')
            assert native_id != check.previous_session_id
            return receipt(check, native_id, 'loaded_fixture', 'new-web-generation')
        def close(self, handle, *, timeout_seconds):
            calls.append('close')
            handle.closed = True
    lifecycle = NativeFixtureLifecycle(profile, contract, run=lambda *args: pytest.fail('Web spawned CLI'), web=Web())
    validate_native_runtime(target)
    assert calls == []
    lifecycle.prepare()
    lifecycle.require_effect_budget(60)
    states = lifecycle.complete(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), {'target-1': '7'*64})
    assert calls == ['prepare', 'existing', 'new', 'close']
    assert all(state['state'] == 'accepted' for state in states.values())


def test_cleanup_failure_cannot_be_caught_as_pending_acceptance(tmp_path):
    from anvil_serving.propagation_native_acceptance import NativeQuiescenceError
    profile, contract = fixture_profile(tmp_path)
    lifecycle = NativeFixtureLifecycle(profile, contract, run=lambda *args: None)
    attempts = []
    def fail_close():
        attempts.append('failed')
        raise RuntimeError('owned process still alive')
    def good_close():
        attempts.append('closed')
    lifecycle.prepared = {'first': (SimpleNamespace(close=fail_close), None),
                          'second': (SimpleNamespace(close=good_close), None)}
    with pytest.raises(NativeQuiescenceError) as caught:
        lifecycle.close()
    assert not isinstance(caught.value, NativeAcceptanceError)
    assert attempts == ['failed', 'closed']
    assert set(lifecycle.prepared) == {'first', 'second'}


def test_pi_evidence_failure_normalizes_only_after_verified_cleanup(tmp_path):
    from anvil_serving.propagation_pi_acceptance import PiAcceptanceError, PiQuiescenceError
    from anvil_serving.propagation_native_acceptance import NativeQuiescenceError
    profile, contract = fixture_profile(tmp_path)
    target = profile['targets'][0]
    target['client'] = 'pi'
    target['native_runtime'].update(agent_dir=str(tmp_path), loaded_catalog_digest='4'*64)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for failure, expected in ((PiAcceptanceError, NativeAcceptanceError), (PiQuiescenceError, NativeQuiescenceError)):
        closed = []
        class Pi:
            def complete_existing(self, *args, **kwargs):
                raise failure('synthetic Pi failure')
        handle = SimpleNamespace(native_session_id='before', history_marker='prior-marker',
            process_generation='generation-1', prepared_at=stamp, close=lambda: closed.append(True))
        lifecycle = NativeFixtureLifecycle(profile, contract, run=lambda *args: None, pi=Pi())
        lifecycle.prepared['target-1'] = (handle, None)
        with pytest.raises(expected):
            lifecycle.complete(stamp, {'target-1': '3'*64})
        assert closed == [True]


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_cleanup_proof_is_exact_and_requires_completed_owned_cleanup(tmp_path):
    from anvil_serving.propagation_native_acceptance import NativeQuiescenceError, verify_native_cleanup
    profile, contract = fixture_profile(tmp_path)
    job = dict(job_id='job-1', operation_id='operation-1', intent_id='intent-1', contract_digest=contract.digest)
    lifecycle = NativeFixtureLifecycle(profile, contract, run=lambda *args: None)
    with pytest.raises(NativeQuiescenceError, match='unproven'):
        verify_native_cleanup(profile, contract, job, '7'*64)
    with pytest.raises(NativeQuiescenceError, match='incomplete'):
        lifecycle.record_cleanup(job, '7'*64)
    lifecycle.prepared['target-1'] = (None, ('native-before', 'marker', '2026-01-01T00:00:00Z'))
    lifecycle.record_cleanup(job, '7'*64)
    verify_native_cleanup(profile, contract, job, '7'*64)
    with pytest.raises(NativeQuiescenceError):
        verify_native_cleanup(profile, contract, {**job, 'operation_id': 'another-operation'}, '7'*64)
    with pytest.raises(NativeQuiescenceError):
        verify_native_cleanup(profile, contract, job, '8'*64)
    path = tmp_path / ('cleanup-' + '7'*64 + '.json')
    path.chmod(0o644)
    with pytest.raises(NativeQuiescenceError):
        verify_native_cleanup(profile, contract, job, '7'*64)


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_openclaw_lost_response_never_proves_gateway_quiescence(tmp_path):
    from anvil_serving.propagation_native_acceptance import NativeQuiescenceError
    profile, contract = fixture_profile(tmp_path)
    target = profile['targets'][0]
    target['client'] = 'openclaw'
    target['paths'] = {'openclaw': str(tmp_path / 'openclaw.json')}
    def lost(argv, timeout, env):
        raise subprocess.TimeoutExpired(argv, timeout)
    lifecycle = NativeFixtureLifecycle(profile, contract, run=lost)
    with pytest.raises(NativeQuiescenceError):
        lifecycle.prepare()
    with pytest.raises(NativeQuiescenceError):
        lifecycle.close()
    with pytest.raises(NativeQuiescenceError):
        lifecycle.record_cleanup(dict(job_id='job', operation_id='operation', intent_id='intent',
            contract_digest=contract.digest), '7'*64)
    assert not list(tmp_path.glob('cleanup-*'))


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_mac_admin_group_runtime_is_explicit_and_keeps_private_evidence(tmp_path, monkeypatch):
    from anvil_serving import propagation_native_acceptance as native
    profile, _ = fixture_profile(tmp_path)
    target = profile['targets'][0]
    runtime = target['native_runtime']
    brew = tmp_path / 'brew'
    brew.mkdir(mode=0o775)
    brew.chmod(0o775)
    old = Path(runtime['executable'])
    executable = brew / 'hermes'
    executable.write_bytes(old.read_bytes())
    executable.chmod(0o755)
    runtime['executable'] = target['hermes']['bin'] = str(executable)
    runtime['path'] = [str(brew)]
    gid = brew.stat().st_gid
    assert gid > 0
    monkeypatch.setattr(native.sys, 'platform', 'darwin')
    monkeypatch.setattr(native.os, 'getgroups', lambda: [gid])
    with pytest.raises(NativeAcceptanceError, match='custody differs'):
        validate_native_runtime(target)
    runtime['trusted_admin_group_ids'] = [gid]
    validate_native_runtime(target)
    monkeypatch.setattr(native.os, 'getgroups', lambda: [gid, gid + 1])
    runtime['trusted_admin_group_ids'] = [gid + 1]
    with pytest.raises(NativeAcceptanceError, match='custody differs'):
        validate_native_runtime(target)
    runtime['trusted_admin_group_ids'] = [gid]
    brew.chmod(0o777)
    with pytest.raises(NativeAcceptanceError, match='custody differs'):
        validate_native_runtime(target)
    brew.chmod(0o775)
    # Group trust covers runtime bytes, never the receipt/fixture directory.
    tmp_path.chmod(0o770)
    with pytest.raises(NativeAcceptanceError, match='custody differs'):
        validate_native_runtime(target)
    tmp_path.chmod(0o700)
    monkeypatch.setattr(native.os, 'getgroups', lambda: [])
    monkeypatch.setattr(native.os, 'getegid', lambda: gid + 1)
    with pytest.raises(NativeAcceptanceError, match='group approval differs'):
        validate_native_runtime(target)
    monkeypatch.setattr(native.os, 'getgroups', lambda: [gid])
    monkeypatch.setattr(native.sys, 'platform', 'linux')
    with pytest.raises(NativeAcceptanceError, match='group approval differs'):
        validate_native_runtime(target)


@pytest.mark.parametrize('groups', [[], [True], [0], [-1], ['80'], [80, 80], [81, 80]])
def test_invalid_admin_group_approval_fails_before_turn(tmp_path, monkeypatch, groups):
    from anvil_serving import propagation_native_acceptance as native
    profile, _ = fixture_profile(tmp_path)
    target = profile['targets'][0]
    target['native_runtime']['trusted_admin_group_ids'] = groups
    monkeypatch.setattr(native.sys, 'platform', 'darwin')
    with pytest.raises(NativeAcceptanceError, match='group approval differs'):
        validate_native_runtime(target)
