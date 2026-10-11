"""Acknowledged recovery of one never-admitted, physically extinct startup."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import time
import tomllib

from . import router_manage
from .control_plane.mcp.auth_file import read_private_auth_file
from .observability.dashboard.contracts import strict_json
from .operator_config import _read_bounded
from .router.container_owner import digest, require
from .router.maintenance import _publish
from .router.reboot import _session
from .router.usage_store import RunOwner, _linux_owner
from .router_recovery_status import _covering_mount, _native_container

ACK = 'Abort only this never-admitted startup after physical namespace extinction; preserve all keys, requests and accounting. Do not start or readmit a successor.'


def namespace_scan(owner):
    """An unrestricted root host observation, not a container-local missing PID."""
    require(os.geteuid() == 0 and owner.pid == 1)
    # SO_PEERCRED reports PID zero for a daemon outside the caller's PID view.
    # Require the local native daemon's own namespace, not a container's /proc.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(3)
        peer.connect('/var/run/docker.sock')
        pid, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    require(pid >= 1 and uid == 0 and Path(f'/proc/{pid}/exe').resolve().name in {'dockerd', 'systemd'})
    daemon_ns, caller_ns = os.stat(f'/proc/{pid}/ns/pid'), os.stat('/proc/self/ns/pid')
    require((daemon_ns.st_dev, daemon_ns.st_ino) == (caller_ns.st_dev, caller_ns.st_ino))
    before, view = _linux_owner(owner.host_domain_id)
    require(before.boot_id == owner.boot_id
            and (before.pid_namespace_device, before.pid_namespace_inode)
                != (owner.pid_namespace_device, owner.pid_namespace_inode))
    count = matches = 0
    with os.scandir('/proc') as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            require(count < 65536)
            try:
                info = os.stat('/proc/' + entry.name + '/ns/pid')
            except FileNotFoundError:
                continue
            count += 1
            matches += (info.st_dev, info.st_ino) == (owner.pid_namespace_device, owner.pid_namespace_inode)
    after, after_view = _linux_owner(owner.host_domain_id)
    require(before == after and view == after_view and count > 0 and matches == 0)
    return dict(boot_id=owner.boot_id, pid_namespace_device=owner.pid_namespace_device,
                pid_namespace_inode=owner.pid_namespace_inode, processes_scanned=count,
                inaccessible=0, matches=0)


def native_session():
    from .router.config import load_server_config
    from .router.keys import KeyStore
    from .router.usage_store import UsageStore
    from .router.startup_abort import preview_startup_abort, abort_startup
    signal.alarm(180)

    def receive():
        require(bool(select.select([sys.stdin], [], [], 60)[0]))
        raw = sys.stdin.buffer.readline(32769)
        require(len(raw) <= 32768)
        return strict_json(raw)

    def send(value):
        print(json.dumps(value, sort_keys=True), flush=True)

    request = receive()
    require(type(request) is dict and set(request) == {'run_id', 'configuration_revision'})
    config = router_manage.DEFAULT_INSTALLED_CONFIG
    require(hashlib.sha256(Path(config).read_bytes()).hexdigest() == request['configuration_revision'])
    settings = load_server_config(config)
    store = KeyStore(settings.api_keys_path)
    usage = UsageStore(store)
    usage.owner_config = settings
    with store._offline_custody(settings, allow_retained=True):
        preview = preview_startup_abort(usage, request['run_id'])
        send({'fenced': preview, 'stopped': None})
        command = receive()
        if command == {'release': True}:
            send({'released': True})
            return
        require(type(command) is dict and set(command) == {'authorization'})

        def scan(owner):
            send({'namespace_scan': asdict(owner)})
            response = receive()
            require(type(response) is dict and set(response) == {'proof'})
            return response['proof']

        result = abort_startup(usage, command['authorization'], namespace_scan=scan)
        send({'finalized': result})


@router_manage._serving_authority_mutation
def run(config, *, confirm=False, preview_out=None, _run=subprocess.run):
    require(os.geteuid() == 0)
    config = Path(config).absolute()
    raw = read_private_auth_file(config, max_bytes=16384)
    value = strict_json(raw)
    require(type(value) is dict and set(value) == {'schema', 'compose', 'compose_sha256',
        'expected_container_id', 'expected_image_id', 'helper_image_id', 'run_id',
        'configuration_revision', 'preview_sha256', 'acknowledgement', 'receipt_out'}
        and value['schema'] == 'router-startup-abort-config/v1' and value['acknowledgement'] == ACK)
    for key in ('compose', 'receipt_out'):
        require(type(value[key]) is str and Path(value[key]).is_absolute() and '..' not in Path(value[key]).parts)
    for key in ('compose_sha256', 'expected_container_id', 'configuration_revision'):
        require(type(value[key]) is str and re.fullmatch('[0-9a-f]{64}', value[key]))
    for key in ('expected_image_id', 'helper_image_id'):
        require(type(value[key]) is str and re.fullmatch('sha256:[0-9a-f]{64}', value[key]))
    compose = read_private_auth_file(value['compose'], max_bytes=2*1024**2)
    require(hashlib.sha256(compose).hexdigest() == value['compose_sha256'])
    receipt = Path(value['receipt_out'])
    pending_path = Path(str(receipt) + '.pending')
    if receipt.exists():
        saved = strict_json(read_private_auth_file(receipt, max_bytes=32768))
        require(saved['pending']['config_sha256'] == hashlib.sha256(raw).hexdigest())
        return dict(applied=True, stopped=True, aborted_startup=saved['result'], readmitted=False)
    pending = strict_json(read_private_auth_file(pending_path, max_bytes=32768)) if pending_path.exists() else None
    require(pending is None or confirm and pending['config_sha256'] == hashlib.sha256(raw).hexdigest())
    before = router_manage._restart_custody(router_manage.DEFAULT_CONTAINER, _run)
    require(before.get('available') is True and before['container_id'] == value['expected_container_id']
            and before['image_id'] == value['expected_image_id']
            and before['compose_project'] == router_manage.DEFAULT_COMPOSE_PROJECT
            and before['compose_service'] == router_manage.DEFAULT_SERVICE)
    def daemon_id():
        observed = _run(['docker', 'info', '--format', '{{.ID}}'], capture_output=True, text=True, timeout=5)
        require(observed.returncode == 0 and 0 < len(observed.stdout.strip()) <= 256)
        return observed.stdout.strip()
    daemon = daemon_id()
    observed = _run(['docker', 'inspect', '--format', '{{json .Config.User}}', before['container_id']],
                    capture_output=True, text=True, timeout=5)
    require(observed.returncode == 0 and len(observed.stdout) < 128)
    user = strict_json(observed.stdout)
    require(type(user) is str and re.fullmatch('[1-9][0-9]*:[1-9][0-9]*', user))
    mounts = [m for m in before['mounts'] if m['destination'] == router_manage.DEFAULT_INSTALLED_CONFIG]
    require(len(mounts) == 1 and mounts[0]['type'] == 'bind' and mounts[0]['read_only'])
    mounted = mounts[0]
    source = Path(mounted['source'])
    require(source.name != '.env')
    config_raw = _read_bounded(source, max_bytes=1024**2)
    require(len(config_raw) <= 1024**2 and hashlib.sha256(config_raw).hexdigest() == value['configuration_revision'])
    settings = tomllib.loads(config_raw.decode())['server']
    storage = _covering_mount(before['mounts'], PurePosixPath(settings['api_keys_path']))
    require(storage['type'] == 'volume' and not storage['read_only']
        and _covering_mount(before['mounts'], PurePosixPath(settings['admission_state_path']+'.router')) == storage)
    stable = {k: v for k, v in before.items() if k not in {'started_at', 'restart_count'}}
    already_stopped = router_manage.docker_state(router_manage.DEFAULT_CONTAINER, _run=_run) == 'exited'
    if pending is not None and pending.get('stopped') is not None:
        require(already_stopped and router_manage._container_incarnation(router_manage.DEFAULT_CONTAINER,
            _run=_run, stopped=True) == pending['stopped'])
    program = 'from anvil_serving.router_startup_abort import native_session; native_session()'
    with _native_container(value['helper_image_id'], user, [(mounted, True), (storage, False)],
                           program, _run=_run) as (helper, name, observe):
        def roster(stopped=False):
            return router_manage._offline_compose_roster(value['compose'], router_manage.DEFAULT_SERVICE,
                router_manage.DEFAULT_CONTAINER, _run=_run, live_target=not stopped, _metadata_only=True,
                _reboot_helper=(name, helper, value['helper_image_id']), _owner_state_only=True)

        baseline = roster(stopped=already_stopped)

        def recheck(stopped=None):
            stopped = already_stopped if stopped is None else stopped
            require(read_private_auth_file(config, max_bytes=16384) == raw
                and read_private_auth_file(value['compose'], max_bytes=2*1024**2) == compose
                and _read_bounded(source, max_bytes=1024**2) == config_raw and daemon_id() == daemon)
            current = router_manage._restart_custody(router_manage.DEFAULT_CONTAINER, _run)
            require({k: v for k, v in current.items() if k not in {'started_at', 'restart_count'}} == stable)

            def others(report):
                return dict(rendered=report['rendered'], owner_storage=report['owner_storage'],
                            roster=[r for r in report['roster'] if r['id'] != before['container_id']])

            require(others(roster(stopped)) == others(baseline))

        request = {k: value[k] for k in ('run_id', 'configuration_revision')}
        with _session(helper, request) as (native, _, exchange, alive):
            owner = RunOwner(**native['run_owner'])
            require(native['run_id'] == value['run_id'] and native['configuration_revision'] == value['configuration_revision'])
            namespace_scan(owner)
            preview = dict(schema='router-startup-abort-preview/v1', native=native,
                container_id=before['container_id'], image_id=before['image_id'], daemon_id=daemon,
                helper_image_id=value['helper_image_id'], compose_sha256=value['compose_sha256'])
            recheck(); alive()
            if not confirm:
                if preview_out:
                    _publish(Path(preview_out), preview)
                require(exchange({'release': True}) == {'released': True})
                return preview
            require(value['preview_sha256'] == digest(preview))
            if pending is None:
                now = int(time.time())
                auth = {k: native[k] for k in ('run_id', 'configuration_revision', 'roster_revision',
                    'generation', 'closure_sha256', 'sidecar_snapshot_sha256')}
                import secrets
                auth.update(schema='router-startup-abort-authorization/v1', operation_id=secrets.token_hex(32),
                            acknowledged_at=now, expires_at=now+900)
                pending = dict(config_sha256=hashlib.sha256(raw).hexdigest(), preview=preview, authorization=auth, stopped=None)
                _publish(pending_path, pending)
            require(pending['preview'] == preview)
            recheck(); alive()
            if not already_stopped:
                stopped = _run(['docker', 'stop', before['container_id']], capture_output=True, text=True, timeout=30)
                require(stopped.returncode == 0)
            dead = router_manage._container_incarnation(router_manage.DEFAULT_CONTAINER, _run=_run, stopped=True)
            recheck(True); alive()
            if pending['stopped'] is None:
                revised = {**pending, 'stopped': dead}
                _publish(pending_path, revised, expected=digest(pending))
                pending = revised
            require(pending['stopped'] == dead)
            result = exchange({'authorization': pending['authorization']})
            for _ in range(4):
                if type(result) is dict and set(result) == {'finalized'}:
                    break
                require(type(result) is dict and set(result) == {'namespace_scan'} and result['namespace_scan'] == asdict(owner))
                recheck(True); alive()
                result = exchange({'proof': namespace_scan(owner)})
            require(type(result) is dict and set(result) == {'finalized'})
            recheck(True)
        observe('exited')
    _publish(receipt, dict(pending=pending, stopped=dead, result=result['finalized']))
    return dict(applied=True, stopped=True, aborted_startup=result['finalized'], readmitted=False)


def dispatch(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--preview-out')
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.config, confirm=args.confirm, preview_out=args.preview_out)))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError):
        print('Startup abort HOLD; protected custody or physical proof unavailable.', file=sys.stderr)
        return 1
