import io
import os
import select

import pytest

if os.name != 'posix':
    pytest.skip('Local maintenance console requires a POSIX terminal', allow_module_level=True)

import pty

from anvil_serving.router import maintenance_console as console
from anvil_serving.router.container_owner import digest


class Terminal(io.StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 0


def preview():
    return {'schema': 'router-maintenance-preview/v1', 'expires_at': 160,
            'closure': {'configuration_revision': 'b' * 64, 'generation': 2}}


@pytest.mark.parametrize('response,now,accepted', [('correct', 100, True), ('no\n', 100, False),
                                                ('yes\n', 100, False), (' YES\n', 100, False),
                                                ('YES \n', 100, False),
                                                ('', 100, False), ('correct', 160, False)])
def test_local_acknowledgement_requires_tty_exact_fresh_reply(monkeypatch, response, now, accepted):
    value = preview()
    phrase = 'YES\n'
    monkeypatch.setattr(console.sys, 'stdin', Terminal(phrase if response == 'correct' else response))
    monkeypatch.setattr(console.sys, 'stdout', Terminal())
    monkeypatch.setattr(console.time, 'time', lambda: now)
    cleared = []
    def clear(fd, queue):
        assert console.sys.stdout.getvalue() == ''
        assert (fd, queue) == (0, console.termios.TCIFLUSH)
        cleared.append(True)
    monkeypatch.setattr(console.termios, 'tcflush', clear)
    monkeypatch.setattr(console.os, 'read', lambda fd, limit: console.sys.stdin.readline(limit).encode())
    monkeypatch.setattr(console.select, 'select', lambda *args: ([console.sys.stdin], [], []))
    if accepted:
        assert console._acknowledge(value, 'OPEN')['preview_sha256'] == digest(value)
        assert cleared == [True]
        assert 'approve OPEN for this exact preview and accept UNKNOWN' in console.sys.stdout.getvalue()
    else:
        with pytest.raises(ValueError):
            console._acknowledge(value, 'OPEN')


def test_noninteractive_invocation_cannot_read_or_mutate(monkeypatch):
    monkeypatch.setattr(console.sys, 'stdin', io.StringIO())
    monkeypatch.setattr(console, '_read', lambda *_: pytest.fail('read before terminal check'))
    with pytest.raises(ValueError):
        console.run('/protected/plan.json')


@pytest.mark.parametrize('flags,exit_code', [([], 1), (['--confirm'], 2)])
def test_cli_confirmation_cannot_replace_local_terminal(monkeypatch, flags, exit_code):
    from anvil_serving import cli

    monkeypatch.setattr(console.sys, 'stdin', io.StringIO())
    monkeypatch.setattr(console, '_read', lambda *_: pytest.fail('read before terminal check'))
    assert cli.main(['router', 'maintenance-console', '--config', '/protected/plan.json', *flags]) == exit_code


def test_terminal_queue_cannot_preapprove_open(monkeypatch):
    master, slave = pty.openpty()
    terminal = os.fdopen(os.dup(slave), 'r')
    native_select = select.select
    try:
        monkeypatch.setattr(console.sys, 'stdin', terminal)
        monkeypatch.setattr(console.sys, 'stdout', Terminal())
        monkeypatch.setattr(console.time, 'time', lambda: 100)
        # Answers queued before rendering a new preview are discarded.
        os.write(master, b'YES\nYES\n')
        assert native_select([slave], [], [], 1)[0]
        monkeypatch.setattr(console.select, 'select',
                            lambda readers, *args: native_select(readers, [], [], 0))
        with pytest.raises(ValueError):
            console._acknowledge(preview(), 'OPEN')
        # Human input after STOP appears may acknowledge STOP only. A second
        # queued line must be flushed before OPEN, without Python read buffering.
        def after_stop_rendered(readers, *args):
            assert 'approve STOP for this exact preview' in console.sys.stdout.getvalue()
            os.write(master, b'YES\nYES\n')
            return native_select(readers, [], [], 1)
        monkeypatch.setattr(console.select, 'select', after_stop_rendered)
        stop = {'schema': 'router-current-stop-preview/v1',
                'native': {'observation': {'expires_at': 160}},
                'incarnation': {'container_id': 'a' * 64},
                'successor': {'configuration_revision': 'b' * 64}}
        assert console._acknowledge(stop, 'STOP')['phase'] == 'STOP'
        monkeypatch.setattr(console.select, 'select',
                            lambda readers, *args: native_select(readers, [], [], 0))
        with pytest.raises(ValueError):
            console._acknowledge(preview(), 'OPEN')
    finally:
        terminal.close()
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize('failure', ['decline', 'expired', 'drift', 'native-confirm'])
def test_acknowledgement_failure_never_refreshes_or_reuses_permission(tmp_path, monkeypatch, failure):
    auth = {'authorization_sha256': 'a' * 64, 'preview_sha256': None,
            'expected_container_id': 'b' * 64}
    config = {'authorization': str(tmp_path / 'authorization.json')}
    console.maintenance._publish(tmp_path / 'authorization.json', auth)
    console.maintenance._publish(tmp_path / 'config.json', config)
    calls = []

    def native(*args, confirm=False, **kwargs):
        calls.append(confirm)
        if confirm:
            raise ValueError('native refusal')
        console.maintenance._publish(tmp_path / 'preview.json', preview())
        return preview()

    def acknowledge(value, phase):
        if failure == 'decline':
            raise ValueError('declined')
        if failure == 'expired':
            monkeypatch.setattr(console.time, 'time', lambda: 160)
        if failure == 'drift':
            console.maintenance._publish(tmp_path / 'authorization.json',
                                         {**auth, 'changed': True}, expected=digest(auth))
        return {'phase': phase, 'preview_sha256': digest(value), 'acknowledged_at': 100}

    monkeypatch.setattr(console.time, 'time', lambda: 100)
    monkeypatch.setattr(console.maintenance, 'run', native)
    monkeypatch.setattr(console, '_acknowledge', acknowledge)
    with pytest.raises(ValueError):
        console._authorize_and_confirm(tmp_path, tmp_path / 'config.json', 'OPEN')
    assert calls == ([False, True] if failure == 'native-confirm' else [False])
    assert (tmp_path / 'attempt.json').exists() == (failure == 'native-confirm')


def test_post_stop_resume_starts_receipt_bound_successor_without_replaying_stop(tmp_path, monkeypatch):
    plan = {'stop_config': '/protected/stop.json'}
    directory = tmp_path / ('a' * 64)
    console.maintenance._publish(tmp_path / 'current.json',
                                 {'plan_sha256': digest(plan), 'operation_id': 'a' * 64})
    console.maintenance._publish(directory / 'stop-receipt.json', {'retained': True})
    receipt = {'authorization': {'successor': {'compose': '/protected/successor.json',
                'compose_sha256': 'c' * 64, 'image_id': 'sha256:' + 'd' * 64,
                'configuration_revision': 'b' * 64}}, 'preview': {}, 'legacy_receipt_sha256': 'e' * 64}
    monkeypatch.setattr(console, 'completed_receipt', lambda path: receipt)
    monkeypatch.setattr(console, '_prepare_stop', lambda *args: pytest.fail('STOP replay'))
    starts = []
    monkeypatch.setattr(console.lifecycle, 'cmd_up', lambda *args, **kwargs: starts.append((args, kwargs)) or 0)
    monkeypatch.setattr(console.lifecycle, '_container_incarnation',
                        lambda *args, **kwargs: {'container_id': 'f' * 64, 'image_id': 'sha256:' + 'd' * 64})
    phases = []
    monkeypatch.setattr(console, '_authorize_and_confirm',
                        lambda directory, config, phase: phases.append(phase) or {'readmitted': True, 'state': 'admitting'})
    assert console._operate(plan, tmp_path)['readmitted'] is True
    assert phases == ['OPEN']
    assert starts[0][1]['current_stop_receipt'] == str(directory / 'stop-receipt.json')


def test_uncertain_stop_attempt_is_not_replayed(tmp_path, monkeypatch):
    plan = {'stop_config': '/protected/stop.json'}
    directory = tmp_path / ('a' * 64)
    console.maintenance._publish(tmp_path / 'current.json',
                                 {'plan_sha256': digest(plan), 'operation_id': 'a' * 64})
    console.maintenance._publish(directory / 'attempt.json', {'uncertain': True})
    monkeypatch.setattr(console, '_prepare_stop', lambda *args: pytest.fail('new grant after uncertainty'))
    with pytest.raises(ValueError):
        console._operate(plan, tmp_path)


@pytest.mark.parametrize('result', [None, {'readmitted': False}, {'readmitted': True, 'state': 'admitting'}])
def test_attempted_open_always_requires_independent_reconciliation(tmp_path, monkeypatch, result):
    plan = {'stop_config': '/protected/stop.json'}
    directory = tmp_path / ('a' * 64)
    readmit = directory / ('b' * 64)
    console.maintenance._publish(tmp_path / 'current.json',
        {'plan_sha256': digest(plan), 'operation_id': 'a' * 64})
    console.maintenance._publish(directory / 'stop-receipt.json', {})
    console.maintenance._publish(directory / 'readmit-current.json', {'operation_id': 'b' * 64})
    console.maintenance._publish(readmit / 'attempt.json', {})
    if result is not None:
        console.maintenance._publish(readmit / 'console-result.json', result)
    monkeypatch.setattr(console, 'completed_receipt',
                        lambda path: {'authorization': {'successor': {}}, 'preview': {}})
    monkeypatch.setattr(console.lifecycle, 'cmd_up', lambda *a, **kw: pytest.fail('replayed OPEN'))
    with pytest.raises(ValueError):
        console._operate(plan, tmp_path)


@pytest.mark.parametrize('failed_publish', ['human-acknowledgement.json', 'attempt.json',
                                         'authorization.json', 'console-result.json'])
def test_durable_attempt_precedes_consumable_authority(tmp_path, monkeypatch, failed_publish):
    auth = {'authorization_sha256': 'a' * 64, 'preview_sha256': None,
            'expected_container_id': 'b' * 64}
    config = {'authorization': str(tmp_path / 'authorization.json')}
    publish = console.maintenance._publish
    publish(tmp_path / 'authorization.json', auth)
    publish(tmp_path / 'config.json', config)
    confirms = []
    monkeypatch.setattr(console.time, 'time', lambda: 100)
    monkeypatch.setattr(console, '_acknowledge', lambda *a: {'acknowledged_at': 100})
    def native(*args, confirm=False, **kwargs):
        confirms.append(confirm)
        if not confirm:
            publish(tmp_path / 'preview.json', preview())
        return {} if confirm else preview()
    monkeypatch.setattr(console.maintenance, 'run', native)

    def crash_after_publish(path, value, **kwargs):
        publish(path, value, **kwargs)
        if path.name == failed_publish:
            raise RuntimeError('simulated process exit')

    monkeypatch.setattr(console.maintenance, '_publish', crash_after_publish)
    with pytest.raises(RuntimeError):
        console._authorize_and_confirm(tmp_path, tmp_path / 'config.json', 'OPEN')
    if console._read(tmp_path / 'authorization.json')['preview_sha256'] is not None:
        assert (tmp_path / 'attempt.json').exists()
    assert confirms == ([False, True] if failed_publish == 'console-result.json' else [False])
