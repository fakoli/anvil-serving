"""Local TTY acknowledgement of existing native maintenance, without new authority."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import re
import secrets
import select
import subprocess
import sys
import time

if os.name == 'posix':
    import fcntl
    import termios

from ..control_plane.mcp.auth_file import read_private_auth_file
from ..observability.dashboard.contracts import strict_json
from .. import router_manage as lifecycle
from ..router_current_stop import completed_receipt
from . import maintenance
from .container_owner import digest, require
from .keys import _private_created_descriptor, _secure_directory


def _read(path):
    return strict_json(read_private_auth_file(str(path), max_bytes=32768))


def _absolute_path(value):
    require(type(value) is str and 0 < len(value) <= 4096
            and not any(ord(c) < 32 for c in value))
    path = Path(value)
    require(path.is_absolute() and '..' not in path.parts)
    return path


def _expiry(preview):
    return (preview['native']['observation']['expires_at']
            if preview['schema'] == 'router-current-stop-preview/v1'
            else preview['expires_at'])


def _acknowledge(preview, phase):
    """A fresh local human reply is required for each native preview."""
    require(os.name == 'posix' and sys.stdin.isatty() and sys.stdout.isatty())
    remaining = _expiry(preview) - int(time.time())
    require(0 < remaining <= 60)
    # Discard queued answers before showing this phase's exact fresh preview.
    # A reply to STOP must never be reused as the separate OPEN acknowledgement.
    termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    print('\nAction:', phase, flush=True)
    print('Exact native preview SHA-256:', digest(preview), flush=True)
    print('Preview expires (UTC):', time.strftime('%Y-%m-%d %H:%M:%S',
                                                time.gmtime(_expiry(preview))), flush=True)
    print('Remote memory completion remains UNKNOWN; this is not a drain.', flush=True)
    if phase == 'STOP':
        print('Stop container:', preview['incarnation']['container_id'], flush=True)
        print('Then start only the declared successor configuration:',
              preview['successor']['configuration_revision'], flush=True)
    else:
        print('Reopen configuration:', preview['closure']['configuration_revision'], flush=True)
        print('Owner generation:', preview['closure']['generation'], flush=True)
    print(f'To approve {phase} for this exact preview and accept UNKNOWN remote-memory '
          f'completion, type YES and press Enter within {remaining} seconds. '
          'Ctrl-C leaves the operation held:', flush=True)
    ready, _, _ = select.select([sys.stdin], [], [], max(0, _expiry(preview) - time.time()))
    require(bool(ready))
    answer = os.read(sys.stdin.fileno(), 128)
    require(answer in (b'YES\n', b'YES\r\n') and int(time.time()) < _expiry(preview))
    return {'phase': phase, 'preview_sha256': digest(preview),
            'acknowledged_at': int(time.time()), 'acknowledgement': 'YES',
            'accepted_remote_memory_completion': 'UNKNOWN'}


def _quiet_call(function, *args, **kwargs):
    # Native operations own lifecycle and fences. Never print secret-bearing errors.
    with open(os.devnull, 'w') as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
        return function(*args, **kwargs)


def _authorize_and_confirm(directory, config_path, phase):
    config = _read(config_path)
    auth_path = Path(config['authorization'])
    before = _read(auth_path)
    preview = _quiet_call(maintenance.run, str(config_path),
                          preview_out=str(directory / 'preview.json'))
    require(_read(directory / 'preview.json') == preview)
    print('Full protected preview:', directory / 'preview.json', flush=True)
    print('Exact target container:', before['expected_container_id'], flush=True)
    acknowledgement = _acknowledge(preview, phase)
    require(_read(config_path) == config and _read(auth_path) == before
            and _read(directory / 'preview.json') == preview)
    require(int(time.time()) < _expiry(preview))
    maintenance._publish(directory / 'human-acknowledgement.json', acknowledgement)
    auth = {**before, 'preview_sha256': digest(preview),
            'authorization_sha256': digest({'prepared_authority': before['authorization_sha256'],
                                            'human': acknowledgement})}
    # Persist uncertainty before publishing a grant the native guard can consume.
    maintenance._publish(directory / 'attempt.json', {'authorization': auth})
    maintenance._publish(auth_path, auth, expected=digest(before))
    result = _quiet_call(maintenance.run, str(config_path), confirm=True)
    maintenance._publish(directory / 'console-result.json', result)
    return result


def _prepare_stop(template, directory, binding):
    source_config = _read(template)
    source_auth = _read(source_config['authorization'])
    require(source_auth['phase'] == 'current-instrumented-stop')
    require(not os.path.lexists(source_config['receipt_out'])
            and not os.path.lexists(source_config['receipt_out'] + '.pending'))
    auth = {**source_auth, 'operation_id': secrets.token_hex(32),
            'authorization_sha256': digest({'source_authority': source_auth['authorization_sha256'],
                                           'console_plan_sha256': binding}),
            'preview_sha256': None, 'expires_at': int(time.time()) + 900}
    maintenance.authorization(auth)
    maintenance._publish(directory / 'authorization.json', auth)
    config = {**source_config, 'authorization': str(directory / 'authorization.json'),
              'receipt_out': str(directory / 'stop-receipt.json')}
    maintenance._publish(directory / 'config.json', config)
    return config


def _operate(plan, state):
    index_path = state / 'current.json'
    prior = _read(index_path) if os.path.lexists(index_path) else None
    binding = digest(plan)
    directory = None
    if prior is not None:
        require(set(prior) == {'plan_sha256', 'operation_id'}
                and prior['plan_sha256'] == binding
                and re.fullmatch('[a-f0-9]{64}', prior['operation_id']) is not None)
        directory = state / prior['operation_id']
        if not os.path.lexists(directory / 'stop-receipt.json'):
            require(not os.path.lexists(directory / 'attempt.json')
                    and not os.path.lexists(directory / 'stop-receipt.json.pending'))
            directory = None  # A fresh invocation gets a new preview and human reply.
    if directory is None:
        operation_id = secrets.token_hex(32)
        directory = state / operation_id
        _prepare_stop(plan['stop_config'], directory, binding)
        maintenance._publish(index_path, {'plan_sha256': binding, 'operation_id': operation_id},
                             expected=digest(prior) if prior else None)
        result = _authorize_and_confirm(directory, directory / 'config.json', 'STOP')
        require(result.get('stopped') is True)
    receipt_path = directory / 'stop-receipt.json'
    receipt = completed_receipt(str(receipt_path))
    successor = receipt['authorization']['successor']
    environment_file = receipt['preview'].get('environment', {}).get('file')

    readmit_index = directory / 'readmit-current.json'
    readmit_directory = None
    if os.path.lexists(readmit_index):
        record = _read(readmit_index)
        require(set(record) == {'operation_id'}
                and re.fullmatch('[a-f0-9]{64}', record['operation_id']) is not None)
        readmit_directory = directory / record['operation_id']
        # An attempted OPEN needs independent live reconciliation, never replay or
        # an inferred success from the presence of a result file.
        require(not os.path.lexists(readmit_directory / 'attempt.json'))
    print('Starting/verifying only the receipt-bound router successor...', flush=True)
    rc = _quiet_call(lifecycle.cmd_up, successor['compose'], lifecycle.DEFAULT_SERVICE,
                     env_file=environment_file, current_stop_receipt=str(receipt_path))
    require(rc == 0)
    incarnation = lifecycle._container_incarnation(lifecycle.DEFAULT_CONTAINER, _run=subprocess.run)
    require(incarnation['image_id'] == successor['image_id'])
    operation_id = secrets.token_hex(32)
    readmit_directory = directory / operation_id
    auth = {'schema': 'router-maintenance-authorization/v1', 'phase': 'successor-readmit',
            'operation_id': operation_id, 'authorization_sha256': digest(receipt),
            'expected_container_id': incarnation['container_id'],
            'expected_image_id': successor['image_id'],
            'expected_configuration_revision': successor['configuration_revision'],
            'preview_sha256': None, 'expires_at': int(time.time()) + 900,
            'legacy_receipt_sha256': receipt['legacy_receipt_sha256'],
            'acknowledge_uncertainty': 'remote-memory-terminal-only', 'ingress_barrier': 'closed-generation'}
    maintenance._publish(readmit_directory / 'authorization.json', maintenance.authorization(auth))
    maintenance._publish(readmit_directory / 'config.json', {
        'schema': 'router-maintenance-config/v1', 'compose': successor['compose'],
        'compose_sha256': successor['compose_sha256'],
        'authorization': str(readmit_directory / 'authorization.json'),
        'receipt_out': str(readmit_directory / 'native-result.json')})
    old = _read(readmit_index) if os.path.lexists(readmit_index) else None
    maintenance._publish(readmit_index, {'operation_id': operation_id},
                         expected=digest(old) if old else None)
    result = _authorize_and_confirm(readmit_directory, readmit_directory / 'config.json', 'OPEN')
    require(result.get('readmitted') is True and result.get('state') == 'admitting')
    # Tier holds remain explicit, separate managed operations after live readback.
    return {'readmitted': True, 'drained': False, 'remote_memory_terminal': 'UNKNOWN',
            'tier_holds_unchanged': True,
            'state_directory': str(directory)}


def run(config):
    require(os.name == 'posix' and sys.stdin.isatty() and sys.stdout.isatty())
    path = _absolute_path(config)
    plan = _read(path)
    require(type(plan) is dict and set(plan) == {'schema', 'stop_config', 'state_dir'}
            and plan['schema'] == 'router-maintenance-console/v1')
    _absolute_path(plan['stop_config'])
    state = _absolute_path(plan['state_dir'])
    _secure_directory(state, create=True)
    fd = os.open(state / 'console.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        _private_created_descriptor(fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print('Protected cutover plan SHA-256:', digest(plan), flush=True)
        print('This command preserves all individual tier holds.', flush=True)
        return _operate(plan, state)
    finally:
        os.close(fd)


def dispatch(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.config)))
        return 0
    except (Exception, KeyboardInterrupt):
        print('Maintenance console HOLD. Native guards remain intact; preserve the '
              'protected operation records. An attempted phase must not be replayed.', file=sys.stderr)
        return 1
