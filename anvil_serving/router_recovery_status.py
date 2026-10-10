"""Bounded native custody diagnostics; never a recovery authorization."""
import argparse
import hashlib
import inspect
from itertools import islice
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tomllib
import uuid

from . import operator_config, router_manage
from .observability.dashboard.contracts import strict_json
from .operator_output import CommandResult, UsageError
from .router.container_owner import digest, require, validate


def _metadata(path, uid, *, directory=False):
    operator_config._assert_no_link_components(path, label='custody')
    for parent in path.parents:
        info = parent.stat()
        require(info.st_uid in {0, uid} and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX))
    info = path.lstat()
    require(info.st_uid == uid and not info.st_mode & 0o077
            and (stat.S_ISDIR(info.st_mode) if directory else
                 stat.S_ISREG(info.st_mode) and info.st_nlink == 1))
    return info


def _private(path, uid):
    info = _metadata(path, uid)
    raw = operator_config._read_bounded(path, max_bytes=16384,
                                       expected_identity=operator_config._candidate_identity(info))
    require(_stable(_metadata(path, uid)) == _stable(info))
    return strict_json(raw)


def _stable(info):
    # Reading records/directories may advance atime without changing custody.
    return (*operator_config._candidate_identity(info), info.st_mode, info.st_uid, info.st_gid, info.st_nlink)


def _names(folder):
    with os.scandir(folder) as entries:
        names = sorted(e.name for e in islice(entries, 1025))
    require(len(names) <= 1024 and all(re.fullmatch(r'[a-f0-9-]{36}\.json', n) for n in names))
    return names


def _covering_mount(mounts, path):
    matches = [m for m in mounts if path.is_relative_to(m['destination'])]
    require(bool(matches))
    depth = max(len(PurePosixPath(m['destination']).parts) for m in matches)
    matches = [m for m in matches if len(PurePosixPath(m['destination']).parts) == depth]
    require(len(matches) == 1)
    return matches[0]


def _ledger_status(request, config_path='/etc/anvil/config.toml'):
    """Runs inside the incumbent image with its existing native reader policy."""
    import hashlib
    from itertools import islice
    import os
    from pathlib import Path
    import sqlite3
    import tomllib
    from anvil_serving.router import container_owner as custody, usage_store
    from anvil_serving.router.keys import KeyStore, KeyStoreError, _secure_directory

    raw = Path(config_path).read_bytes()
    revision = hashlib.sha256(raw).hexdigest()
    custody.require(revision == request['configuration_revision']
                    and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == request['boot_id'])
    settings = tomllib.loads(raw.decode())['server']
    store = KeyStore(settings['api_keys_path'], _defer_open=True)
    folder = Path(str(store.path) + '.router-incarnations')
    _secure_directory(folder, create=False)
    with store._connect() as db:
        db.execute('PRAGMA query_only=ON')
        custody.require(db.execute('PRAGMA query_only').fetchone() == (1,))
        db.execute('BEGIN')
        def metadata_only(action, table, *unused):
            if action == sqlite3.SQLITE_READ:
                return (sqlite3.SQLITE_OK if table in {'sqlite_master', 'usage_runs', 'usage_domains',
                        'usage_coverage_segments'} else sqlite3.SQLITE_DENY)
            return (sqlite3.SQLITE_OK if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION}
                    else sqlite3.SQLITE_DENY)
        db.set_authorizer(metadata_only)
        usage_store._validate_schema(db)
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT * FROM usage_runs LIMIT 1025').fetchall()
        domains = db.execute('SELECT domain_id,configuration_revision FROM usage_domains LIMIT 1025').fetchall()
        custody.require(len(rows) <= 1024 and len(domains) <= 1024)
        with os.scandir(folder) as entries:
            names = {e.name for e in islice(entries, 1025)}
        custody.require(len(names) <= 1024)
        binding = custody.binding(store)
        state = custody.closure(store)
        custody.require(binding['owner_id'] == settings['router_owner_id']
                        and binding['state_path'] == settings['admission_state_path'] + '.router')
        result = dict(ledger_rows=len(rows), live_rows=0, missing_sidecars=0,
                      extra_sidecars=len(names - {r['run_id'] + '.json' for r in rows}),
                      invalid_records=0, phase_mismatches=0, partial_records=0,
                      transfer_mismatches=0, previous_boot_live_candidates=0,
                      domain_matches=len(domains) == 1 and domains[0]['domain_id'] == settings['usage_domain_id']
                      and domains[0]['configuration_revision'] == revision,
                      binding_matches=True, closure_matches=True)
        records = {}
        for row in rows:
            result['live_rows'] += row['state'] == 'live'
            name = row['run_id'] + '.json'
            if name not in names:
                result['missing_sidecars'] += 1
                continue
            coverage = db.execute('SELECT domain_id,configuration_revision FROM usage_coverage_segments '
                                  'WHERE run_id=? ORDER BY started_at,segment_id LIMIT 1', (row['run_id'],)).fetchone()
            try:
                custody.require(coverage is not None and coverage['domain_id'] == row['domain_id']
                                and row['domain_id'] == settings['usage_domain_id'])
                record = custody.read(store, row, coverage['configuration_revision'])
            except (OSError, ValueError, KeyStoreError):
                result['invalid_records'] += 1
                continue
            records[row['run_id']] = record
            phase = record['phase']
            result['partial_records'] += phase in {'live-pending', 'dead-pending', 'ready'}
            result['phase_mismatches'] += (row['state'], phase) not in {('live', 'live'), ('dead', 'transferred')}
            anchor = record['anchor']
            candidate = (row['state'] == 'live' and phase == 'live'
                         and anchor['run_owner']['boot_id'] != request['boot_id']
                         and anchor['configuration_revision'] == revision
                         and all(anchor['docker'][k] == v for k, v in request['incumbent'].items()))
            result['previous_boot_live_candidates'] += candidate
            if row['state'] == 'live' and state['closure'] is not None:
                result['closure_matches'] &= all(state['closure'][k] == anchor[k]
                    for k in ('configuration_revision', 'roster_revision'))
        for record in records.values():
            transfer = record['transfer']
            if transfer is not None:
                successor = records.get(transfer['successor_run_id'])
                result['transfer_mismatches'] += successor is None or any(
                    transfer['successor_' + k] != successor['anchor'][k]
                    for k in ('configuration_revision', 'roster_revision'))
        snapshot = custody.digest({run + '.json': custody.digest(record) for run, record in records.items()})
        result['snapshot_matches'] = snapshot == request['sidecar_snapshot_sha256']
        custody.require(custody.binding(store) == binding and custody.closure(store) == state)
        db.execute('ROLLBACK')
    return result


def _native_ledger(before, user, mounts, request, *, _run):
    """A bounded native reader; never inherit the router's network or environment."""
    name = 'router-recovery-reader-' + uuid.uuid4().hex
    label = 'anvil.recovery-reader=' + name
    argv = ['docker', 'create', '--name', name, '--label', label, '--pull=never',
            '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
            '--user', user, '--cpus=1', '--memory=256m', '--memory-swap=256m', '--pids-limit=32',
            '--restart=no', '--log-driver=none', '--interactive', '--entrypoint=python3']
    expected = []
    for mount, readonly in mounts:
        source = mount['name'] if mount['type'] == 'volume' else mount['source']
        require(mount['type'] in {'volume', 'bind'} and source and ',' not in source
                and ',' not in mount['destination'])
        argv += ['--mount', f"type={mount['type']},source={source},target={mount['destination']}"
                 + (',readonly' if readonly else '')]
        expected.append((mount['type'], source, mount['destination'], readonly))
    program = inspect.getsource(_ledger_status) + '''
import json, signal, sys
signal.alarm(25)
request = json.loads(sys.stdin.buffer.read(16385))
result = _ledger_status(request)
output = json.dumps(result, sort_keys=True, allow_nan=False)
assert len(output) <= 16384
print(output)
'''
    argv += [before['image_id'], '-I', '-c', program]
    identity = None
    def owned():
        result = _run(['docker', 'inspect', '--format',
            '{{.Id}} {{index .Config.Labels "anvil.recovery-reader"}}', name],
            capture_output=True, text=True, timeout=5)
        require(result.returncode == 0 and len(result.stdout or '') <= 160)
        parts = result.stdout.strip().split()
        require(len(parts) == 2 and re.fullmatch('[a-f0-9]{64}', parts[0]) and parts[1] == name
                and (identity is None or parts[0] == identity))
        return parts[0]
    def observe(status):
        require(owned() == identity)
        observed = router_manage._restart_custody(name, _run)
        require(observed.get('available') is True and observed['container_id'] == identity
                and observed['image_id'] == before['image_id']
                and sorted((m['type'], m['name'] if m['type'] == 'volume' else m['source'],
                            m['destination'], m['read_only']) for m in observed['mounts']) == sorted(expected))
        fields = {'user': '.Config.User', 'network': '.HostConfig.NetworkMode',
                  'readonly': '.HostConfig.ReadonlyRootfs', 'caps': '.HostConfig.CapDrop',
                  'security': '.HostConfig.SecurityOpt', 'cpu': '.HostConfig.NanoCpus',
                  'memory': '.HostConfig.Memory', 'swap': '.HostConfig.MemorySwap', 'pids': '.HostConfig.PidsLimit',
                  'restart': '.HostConfig.RestartPolicy.Name', 'privileged': '.HostConfig.Privileged',
                  'pid_mode': '.HostConfig.PidMode', 'ports': '.HostConfig.PortBindings',
                  'devices': '.HostConfig.Devices', 'status': '.State.Status', 'exit': '.State.ExitCode'}
        projection = '{' + ','.join(json.dumps(k) + ':{{json ' + v + '}}' for k, v in fields.items()) + '}'
        result = _run(['docker', 'inspect', '--format',
                      projection, identity], capture_output=True, text=True, timeout=5)
        require(result.returncode == 0 and len(result.stdout or '') <= 4096)
        value = strict_json(result.stdout)
        require(type(value) is dict and value.pop('ports', 'invalid') in (None, {})
                and value.pop('devices', 'invalid') in (None, [])
                and value == dict(user=user, network='none', readonly=True, caps=['ALL'],
                    security=['no-new-privileges:true'], cpu=10**9, memory=256*1024**2, swap=256*1024**2,
                    pids=32, restart='no', privileged=False, pid_mode='', status=status, exit=0))
    try:
        created = _run(argv, capture_output=True, text=True, timeout=15)
        require(created.returncode == 0 and re.fullmatch('[a-f0-9]{64}', created.stdout.strip()))
        identity = created.stdout.strip()
        observe('created')
        result = _run(['docker', 'start', '--attach', '--interactive', identity],
                      input=json.dumps(request), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                      text=True, timeout=35)
        require(result.returncode == 0 and len(result.stdout or '') <= 16384)
        observe('exited')
        value = strict_json(result.stdout)
        counts = {'ledger_rows', 'live_rows', 'missing_sidecars', 'extra_sidecars', 'invalid_records',
                  'phase_mismatches', 'partial_records', 'transfer_mismatches', 'previous_boot_live_candidates'}
        flags = {'domain_matches', 'binding_matches', 'closure_matches', 'snapshot_matches'}
        require(type(value) is dict and set(value) == counts | flags
                and all(type(value[k]) is int and 0 <= value[k] <= 1024 for k in counts)
                and all(type(value[k]) is bool for k in flags))
        return value
    finally:
        # Even a lost create response is recoverable only through this unique label.
        actual = owned()
        if identity is None:
            observed = router_manage._restart_custody(name, _run)
            require(observed.get('available') is True and observed['container_id'] == actual
                    and observed['image_id'] == before['image_id'])
        removed = _run(['docker', 'rm', '--force', actual], capture_output=True, text=True, timeout=15)
        require(removed.returncode == 0)
        for selector in ('id=' + actual, 'name=^/' + name + '$'):
            absent = _run(['docker', 'container', 'ls', '--all', '--no-trunc', '--filter', selector,
                           '--format', '{{.ID}}'], capture_output=True, text=True, timeout=5)
            require(absent.returncode == 0 and not absent.stdout.strip())


def recovery_status(container=router_manage.DEFAULT_CONTAINER, *, ledger=False, _run=subprocess.run):
    """Inspect retained custody without granting recovery or changing application state."""
    require(os.name == 'posix')
    before = router_manage._restart_custody(container, _run)
    require(before.get('available') is True
            and before['compose_project'] == router_manage.DEFAULT_COMPOSE_PROJECT
            and before['compose_service'] == router_manage.DEFAULT_SERVICE)
    result = _run(['docker', 'inspect', '--format', '{{json .Config.User}}', before['container_id']],
                  capture_output=True, text=True, timeout=5)
    require(result.returncode == 0 and len(result.stdout or '') <= 128)
    user = strict_json(result.stdout)
    require(type(user) is str and re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*', user))
    uid = int(user.split(':')[0])
    def daemon_id():
        result = _run(['docker', 'info', '--format', '{{.ID}}'], capture_output=True, text=True, timeout=5)
        require(result.returncode == 0 and len(result.stdout or '') <= 129)
        value = result.stdout.strip()
        require(re.fullmatch(r'[A-Za-z0-9:._-]{1,128}', value))
        return value
    daemon = daemon_id()
    mounts = before['mounts']
    config = [m for m in mounts if m['destination'] == router_manage.DEFAULT_INSTALLED_CONFIG]
    require(len(config) == 1 and config[0]['type'] == 'bind' and config[0]['read_only'])
    source = Path(config[0]['source'])
    require(source.is_absolute() and source.name == 'router.toml' and '..' not in source.parts)
    raw = operator_config._read_bounded(source, max_bytes=1024**2)
    settings = tomllib.loads(raw.decode())['server']
    require(settings['router_owner_backend'] == 'managed-container')
    key = PurePosixPath(settings['api_keys_path'])
    require(key.is_absolute() and '..' not in key.parts)
    storage = [_covering_mount(mounts, key)]
    require(not storage[0]['read_only'] and storage[0]['type'] == 'volume')
    require(Path(storage[0]['source']).is_absolute() and '..' not in Path(storage[0]['source']).parts
            and PurePosixPath(storage[0]['destination']).is_absolute())
    store = Path(storage[0]['source']) / key.relative_to(storage[0]['destination'])
    identity = _metadata(store, uid)  # Metadata only; never open the credential database.
    gate = _metadata(Path(str(store) + '.router-writers.lock'), uid)
    binding = _private(Path(str(store) + '.router-owner'), uid)
    require(type(binding) is dict and set(binding) == {
        'schema', 'store_identity', 'gate_identity', 'state_path', 'owner_id'}
        and binding['schema'] == 'router-store-owner/v1'
        and all(type(binding[k]) is list and len(binding[k]) == 2
                and all(type(v) is int and v >= 0 for v in binding[k])
                for k in ('store_identity', 'gate_identity'))
        and binding['store_identity'] == [identity.st_dev, identity.st_ino]
        and binding['gate_identity'] == [gate.st_dev, gate.st_ino]
        and binding['state_path'] == settings['admission_state_path'] + '.router'
        and binding['owner_id'] == settings['router_owner_id'])
    folder = Path(str(store) + '.router-incarnations')
    folder_identity = _metadata(folder, uid, directory=True)
    names = _names(folder)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    revision = hashlib.sha256(raw).hexdigest()
    phases = {}
    snapshots = {}
    for name in names:
        record = validate(_private(folder / name, uid))
        anchor = record['anchor']
        require(name == anchor['run_id'] + '.json' and anchor['store_binding_sha256'] == digest(binding)
                and anchor['owner_id'] == binding['owner_id'])
        counts = phases.setdefault(record['phase'], dict(records=0, same_boot=0, same_container=0,
            same_image=0, same_configuration=0, same_daemon=0, same_compose=0,
            same_started_at=0, same_restart_count=0, same_incarnation=0))
        counts['records'] += 1
        counts['same_boot'] += anchor['run_owner']['boot_id'] == boot
        counts['same_container'] += anchor['docker']['container_id'] == before['container_id']
        counts['same_image'] += anchor['docker']['image_id'] == before['image_id']
        counts['same_configuration'] += anchor['configuration_revision'] == revision
        counts['same_daemon'] += anchor['docker']['daemon_id'] == daemon
        counts['same_compose'] += all(anchor['docker'][k] == before[k] for k in ('compose_project', 'compose_service'))
        counts['same_started_at'] += anchor['docker']['started_at'] == before['started_at']
        counts['same_restart_count'] += anchor['docker']['restart_count'] == before['restart_count']
        counts['same_incarnation'] += anchor['docker']['daemon_id'] == daemon and all(anchor['docker'][k] == before[k]
            for k in ('container_id', 'image_id', 'started_at', 'restart_count', 'compose_project', 'compose_service'))
        snapshots[name] = digest(record)
    correlated = None
    state_source = state_identity = None
    if ledger:
        state_mount = _covering_mount(mounts, PurePosixPath(binding['state_path']))
        require(state_mount['type'] in {'bind', 'volume'} and Path(state_mount['source']).is_absolute())
        state_source = Path(state_mount['source']) / PurePosixPath(binding['state_path']).relative_to(state_mount['destination'])
        state_identity = _stable(_metadata(state_source, uid))
        native_mounts = [(config[0], True), (storage[0], False)]
        if state_mount != storage[0]:
            # Only the bound admission record is needed, not adjacent operator state.
            native_mounts.append((dict(type='bind', source=str(state_source), destination=binding['state_path']), True))
        correlated = _native_ledger(before, user, native_mounts, {
            'configuration_revision': revision, 'sidecar_snapshot_sha256': digest(snapshots), 'boot_id': boot,
            'incumbent': {'daemon_id': daemon, **{k: before[k] for k in
                ('container_id', 'image_id', 'compose_project', 'compose_service')}}}, _run=_run)
    after = router_manage._restart_custody(container, _run)
    ignored = {'started_at', 'restart_count'} if ledger else set()
    require(_names(folder) == names and _stable(_metadata(folder, uid, directory=True)) == _stable(folder_identity)
            and all(digest(_private(folder / n, uid)) == snapshots[n] for n in names)
            and _private(Path(str(store) + '.router-owner'), uid) == binding
            and (_metadata(store, uid).st_dev, _metadata(store, uid).st_ino) == (identity.st_dev, identity.st_ino)
            and _stable(_metadata(Path(str(store) + '.router-writers.lock'), uid)) == _stable(gate)
            and operator_config._read_bounded(source, max_bytes=1024**2) == raw
            and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == boot
            and (not ledger or _stable(_metadata(state_source, uid)) == state_identity)
            and daemon_id() == daemon
            and {k: v for k, v in after.items() if k not in ignored}
            == {k: v for k, v in before.items() if k not in ignored})
    report = {'schema': 'router-recovery-status/v1', 'read_only': True, 'phases': phases,
            'ledger_correlation': 'UNKNOWN', 'unanchored_successor': 'UNKNOWN',
            'recovery_eligible': False, 'sidecar_snapshot_sha256': digest(snapshots)}
    if correlated is not None:
        consistent = all(correlated[k] for k in ('domain_matches', 'binding_matches', 'closure_matches', 'snapshot_matches'))
        consistent &= not any(correlated[k] for k in ('missing_sidecars', 'extra_sidecars', 'invalid_records',
                                                      'phase_mismatches', 'partial_records', 'transfer_mismatches'))
        report.update(ledger=correlated, ledger_correlation='MATCH' if consistent else 'MISMATCH',
                      unanchored_successor='ABSENT' if consistent else 'UNKNOWN')
    return report


def dispatch(argv=None):
    parser = argparse.ArgumentParser(prog='anvil-serving router recovery-status')
    parser.add_argument('--container', default=router_manage.DEFAULT_CONTAINER)
    parser.add_argument('--ledger', action='store_true', help='Correlate ledger metadata using an isolated native reader.')
    args = parser.parse_args(argv)
    try:
        data = recovery_status(args.container, ledger=args.ledger)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return CommandResult(error=UsageError('Protected router custody diagnostic unavailable.',
            code='router_recovery_status_unavailable'), human_stderr='Router recovery status unavailable.\n')
    return CommandResult(data=data, human_stdout=json.dumps(data, sort_keys=True) + '\n')
