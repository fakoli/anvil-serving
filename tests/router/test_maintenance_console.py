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

    def acknowledge(value, phase, assume_yes=False):
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
                        lambda directory, config, phase, assume_yes=False: phases.append((phase, assume_yes)) or {'readmitted': True, 'state': 'admitting'})
    assert console._operate(plan, tmp_path)['readmitted'] is True
    assert phases == [('OPEN', False)]
    assert starts[0][1]['current_stop_receipt'] == str(directory / 'stop-receipt.json')
    # A resumed OPEN under --assume-yes carries the flag too.
    console.maintenance._publish(directory / 'readmit-current.json', {'operation_id': 'c' * 64},
                                 expected=digest(console._read(directory / 'readmit-current.json')))
    phases.clear()
    assert console._operate(plan, tmp_path, True)['readmitted'] is True
    assert phases == [('OPEN', True)]


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


def test_assume_yes_skips_only_the_typed_reply(monkeypatch):
    value = preview()
    monkeypatch.setattr(console.sys, 'stdin', io.StringIO())  # not a terminal, nothing typed
    monkeypatch.setattr(console.sys, 'stdout', Terminal())
    monkeypatch.setattr(console.time, 'time', lambda: 100)
    monkeypatch.setattr(console.os, 'read', lambda *_: pytest.fail('read a reply under --assume-yes'))
    record = console._acknowledge(value, 'OPEN', assume_yes=True)
    assert record['acknowledgement'] == 'ASSUMED-BY-FLAG' and record['preview_sha256'] == digest(value)
    assert record['accepted_remote_memory_completion'] == 'UNKNOWN'
    shown = console.sys.stdout.getvalue()
    assert 'Exact native preview SHA-256' in shown and 'acknowledged by --assume-yes' in shown
    monkeypatch.setattr(console.time, 'time', lambda: 160)  # an expired preview still refuses
    with pytest.raises(ValueError):
        console._acknowledge(value, 'OPEN', assume_yes=True)
    monkeypatch.setattr(console.time, 'time', lambda: 90)  # so does one longer than 60 seconds
    with pytest.raises(ValueError):
        console._acknowledge(value, 'OPEN', assume_yes=True)


def test_assume_yes_is_never_the_default(monkeypatch, tmp_path):
    monkeypatch.setattr(console.sys, 'stdin', io.StringIO())
    with pytest.raises(ValueError):
        console._acknowledge(preview(), 'STOP')
    reached = []
    monkeypatch.setattr(console, '_read', lambda *_: reached.append(True) or pytest.fail('stop here'))
    with open(tmp_path / 'cutover.log', 'w') as log:  # an unattended run logging to a file
        monkeypatch.setattr(console.sys, 'stdout', log)
        with pytest.raises(BaseException):
            console.run('/protected/plan.json', assume_yes=True)  # passes the terminal check
    assert reached == [True]
    args = []
    monkeypatch.setattr(console, 'run', lambda config, assume_yes=False: args.append(assume_yes) or {})
    console.dispatch(['--config', '/protected/plan.json'])
    console.dispatch(['--config', '/protected/plan.json', '--assume-yes'])
    assert args == [False, True]


def test_assume_yes_refuses_a_discarded_output_stream(monkeypatch, tmp_path):
    monkeypatch.setattr(console.sys, 'stdin', io.StringIO())
    monkeypatch.setattr(console, '_read', lambda *_: pytest.fail('read with nobody able to see the previews'))
    monkeypatch.setattr(console.time, 'time', lambda: 100)
    with open(os.devnull, 'w') as null:
        for sink in (io.StringIO(), null):  # --quiet's capture buffer, then > /dev/null
            monkeypatch.setattr(console.sys, 'stdout', sink)
            with pytest.raises(ValueError):
                console.run('/protected/plan.json', assume_yes=True)
            with pytest.raises(ValueError):
                console._acknowledge(preview(), 'OPEN', assume_yes=True)
    with open(tmp_path / 'cutover.log', 'w') as log:  # a log file or pipe is readable
        monkeypatch.setattr(console.sys, 'stdout', log)
        assert console._acknowledge(preview(), 'OPEN', assume_yes=True)['acknowledgement'] == 'ASSUMED-BY-FLAG'


@pytest.mark.parametrize('flag', ['--a', '--assume', '--assume-y'])
def test_assume_yes_cannot_be_abbreviated(monkeypatch, flag):
    monkeypatch.setattr(console, 'run', lambda *a, **k: pytest.fail('ran with an abbreviated flag'))
    with pytest.raises(SystemExit):
        console.dispatch(['--config', '/protected/plan.json', flag])


def test_assume_yes_reaches_stop_and_open_through_run(tmp_path, monkeypatch):
    plan = {'schema': 'router-maintenance-console/v1', 'stop_config': str(tmp_path / 'stop.json'),
            'state_dir': str(tmp_path / 'state')}
    console.maintenance._publish(tmp_path / 'plan.json', plan)
    receipt = {'authorization': {'successor': {'compose': '/protected/successor.json',
                'compose_sha256': 'c' * 64, 'image_id': 'sha256:' + 'd' * 64,
                'configuration_revision': 'b' * 64}}, 'preview': {}, 'legacy_receipt_sha256': 'e' * 64}
    monkeypatch.setattr(console.sys, 'stdin', io.StringIO())
    # Owner-only state checks have their own tests; pytest's tmp_path is not 0700.
    monkeypatch.setattr(console, '_secure_directory', lambda *args, **kwargs: None)
    (tmp_path / 'state').mkdir(mode=0o700)
    monkeypatch.setattr(console, '_private_created_descriptor', lambda *args, **kwargs: None)
    monkeypatch.setattr(console, '_prepare_stop',
                        lambda template, directory, binding: console.maintenance._publish(directory / 'config.json', {}))
    monkeypatch.setattr(console, 'completed_receipt', lambda path: receipt)
    monkeypatch.setattr(console.lifecycle, 'cmd_up', lambda *args, **kwargs: 0)
    monkeypatch.setattr(console.lifecycle, '_container_incarnation',
                        lambda *args, **kwargs: {'container_id': 'f' * 64, 'image_id': 'sha256:' + 'd' * 64})
    phases = []

    def confirm(directory, config, phase, assume_yes=False):
        phases.append((phase, assume_yes))
        return {'stopped': True} if phase == 'STOP' else {'readmitted': True, 'state': 'admitting'}

    monkeypatch.setattr(console, '_authorize_and_confirm', confirm)
    with open(tmp_path / 'cutover.log', 'w') as log:
        monkeypatch.setattr(console.sys, 'stdout', log)
        assert console.run(str(tmp_path / 'plan.json'), assume_yes=True)['readmitted'] is True
    assert phases == [('STOP', True), ('OPEN', True)]


def test_authorize_and_confirm_forwards_assume_yes(tmp_path, monkeypatch):
    auth = {'authorization_sha256': 'a' * 64, 'preview_sha256': None, 'expected_container_id': 'b' * 64}
    console.maintenance._publish(tmp_path / 'authorization.json', auth)
    console.maintenance._publish(tmp_path / 'config.json', {'authorization': str(tmp_path / 'authorization.json')})
    monkeypatch.setattr(console.time, 'time', lambda: 100)
    seen = []
    monkeypatch.setattr(console, '_acknowledge',
                        lambda value, phase, assume_yes=False: seen.append(assume_yes) or {'acknowledged_at': 100})

    def native(*args, confirm=False, **kwargs):
        if confirm:
            return {'readmitted': True}
        console.maintenance._publish(tmp_path / 'preview.json', preview())
        return preview()

    monkeypatch.setattr(console.maintenance, 'run', native)
    assert console._authorize_and_confirm(tmp_path, tmp_path / 'config.json', 'OPEN', True) == {'readmitted': True}
    assert seen == [True]
