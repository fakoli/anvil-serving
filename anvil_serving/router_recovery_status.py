"""Bounded native custody diagnostics; never a recovery authorization."""
import argparse
import hashlib
from itertools import islice
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tomllib

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


def recovery_status(container=router_manage.DEFAULT_CONTAINER, *, _run=subprocess.run):
    """Inspect sidecars only, including when the router cannot stay running.

    The ledger is deliberately unopened. Missing/partial successor rows cannot
    be excluded here; native reader correlation remains a separate requirement.
    """
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
    storage = [m for m in mounts if key.is_relative_to(m['destination'])]
    require(bool(storage))
    depth = max(len(PurePosixPath(m['destination']).parts) for m in storage)
    storage = [m for m in storage if len(PurePosixPath(m['destination']).parts) == depth]
    require(len(storage) == 1 and not storage[0]['read_only'] and storage[0]['type'] == 'volume')
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
    require(_names(folder) == names and _stable(_metadata(folder, uid, directory=True)) == _stable(folder_identity)
            and all(digest(_private(folder / n, uid)) == snapshots[n] for n in names)
            and _private(Path(str(store) + '.router-owner'), uid) == binding
            and (_metadata(store, uid).st_dev, _metadata(store, uid).st_ino) == (identity.st_dev, identity.st_ino)
            and _stable(_metadata(Path(str(store) + '.router-writers.lock'), uid)) == _stable(gate)
            and operator_config._read_bounded(source, max_bytes=1024**2) == raw
            and daemon_id() == daemon
            and router_manage._restart_custody(container, _run) == before)
    return {'schema': 'router-recovery-status/v1', 'read_only': True, 'phases': phases,
            'ledger_correlation': 'UNKNOWN', 'unanchored_successor': 'UNKNOWN',
            'recovery_eligible': False, 'sidecar_snapshot_sha256': digest(snapshots)}


def dispatch(argv=None):
    parser = argparse.ArgumentParser(prog='anvil-serving router recovery-status')
    parser.add_argument('--container', default=router_manage.DEFAULT_CONTAINER)
    args = parser.parse_args(argv)
    try:
        data = recovery_status(args.container)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return CommandResult(error=UsageError('Protected router custody diagnostic unavailable.',
            code='router_recovery_status_unavailable'), human_stderr='Router recovery status unavailable.\n')
    return CommandResult(data=data, human_stdout=json.dumps(data, sort_keys=True) + '\n')
