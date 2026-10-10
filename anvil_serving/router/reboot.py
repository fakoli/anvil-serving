"""Exact previous-boot recovery under native exclusion; never a drain waiver."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys

from . import container_owner as custody
from .keys import KeyStore, _private_json, _secure_database
from .usage_store import RunOwner, UsageStore, _validate_schema


def intent(value, auth):
    """A durable exact operation keeps its original acknowledgment on retry."""
    from .maintenance import authorization
    custody.require(type(value) is dict and set(value) == {'schema', 'authorization', 'preview', 'acknowledged_at'}
        and value['schema'] == 'router-reboot-pending/v1' and value['authorization'] == auth
        and type(value['acknowledged_at']) is int and value['acknowledged_at'] > 0
        and type(value['preview']) is dict and custody.digest(value['preview']) == auth['preview_sha256'])
    authorization(auth, at=value['acknowledged_at'])
    return value


def retained_intent(values, auth):
    path = Path(values['receipt_out'] + '.pending')
    return intent(_private_json(path), auth) if os.path.lexists(path) else None


def fenced_candidate(usage, auth, incumbent):
    """Correlate every retained row while this process owns both actual fences."""
    store = usage.key_store
    custody.require(getattr(store._writer_context, 'offline_custody', False))
    owner = RunOwner.observe(usage.owner_config.router_owner_id, managed=True)
    custody.require(owner.boot_id == auth['boot_id'])
    from ..router_recovery_status import _names
    folder = Path(str(store.path) + '.router-incarnations')
    names = _names(folder)
    with store._connect() as db:
        db.execute('PRAGMA query_only=ON')
        custody.require(db.execute('PRAGMA query_only').fetchone() == (1,))
        db.execute('BEGIN')
        _validate_schema(db)
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT * FROM usage_runs ORDER BY run_id LIMIT 1025').fetchall()
        domains = db.execute('SELECT * FROM usage_domains LIMIT 2').fetchall()
        custody.require(0 < len(rows) <= 1024 and len(domains) == 1
                        and domains[0]['domain_id'] == usage.owner_config.usage_domain_id
                        and domains[0]['configuration_revision'] == auth['expected_configuration_revision']
                        and names == sorted(row['run_id'] + '.json' for row in rows))
        snapshots, coverage, live, records = {}, [], [], {}
        for row in rows:
            first = db.execute('SELECT * FROM usage_coverage_segments WHERE run_id=? '
                'ORDER BY started_at,segment_id LIMIT 1', (row['run_id'],)).fetchone()
            custody.require(first is not None and row['domain_id'] == domains[0]['domain_id'] == first['domain_id'])
            record = custody.read(store, row, first['configuration_revision'])
            records[row['run_id']] = record
            coverage.append(dict(first))
            if row['state'] == 'live':
                custody.require(record['phase'] == 'live' and record['schema'] == 'router-managed-incarnation/v1'
                    or record['schema'] == 'router-managed-incarnation/v2' and record['phase'] in {'dead-pending', 'ready'})
                prior = {**record, 'schema': 'router-managed-incarnation/v1', 'phase': 'live',
                         'sequence': 0, 'death': None, 'transfer': None}
                anchor = prior['anchor']
                custody.require(anchor['run_owner']['boot_id'] != owner.boot_id
                    and usage._physical_owner(usage._run_owner(row)) != 'live'
                    and anchor['configuration_revision'] == auth['expected_configuration_revision']
                    and all(anchor['docker'][k] == v for k, v in incumbent.items()))
                live.append((record, prior))
                snapshots[row['run_id'] + '.json'] = custody.digest(prior)
            else:
                custody.require(row['state'] == 'dead' and record['phase'] == 'transferred')
                snapshots[row['run_id'] + '.json'] = custody.digest(record)
        for record in records.values():
            transfer = record['transfer']
            if transfer is not None:
                successor = records.get(transfer['successor_run_id'])
                custody.require(successor is not None and all(transfer['successor_' + k] == successor['anchor'][k]
                    for k in ('configuration_revision', 'roster_revision')))
        custody.require(len(live) == 1 and custody.digest(snapshots) == auth['sidecar_snapshot_sha256'])
        record, prior = live[0]
        state = custody.closure(store)
        if state['closure'] is not None:
            custody.require(all(state['closure'][k] == prior['anchor'][k]
                                for k in ('configuration_revision', 'roster_revision')))
        from .maintenance import legacy_frontier
        legacy = legacy_frontier(store)
        custody.require(legacy is not None and (auth['legacy_receipt_sha256'] is None or auth['legacy_receipt_sha256'] == legacy))
        observed = {'schema': 'router-reboot-fenced/v1', 'run_id': prior['anchor']['run_id'],
            'boot_id': owner.boot_id, 'prior_record_sha256': custody.digest(prior),
            'sidecar_snapshot_sha256': custody.digest(snapshots),
            'ledger_snapshot_sha256': custody.digest({'rows': [dict(r) for r in rows],
                'domain': dict(domains[0]), 'coverage': coverage}),
            'closure_sha256': custody.digest(state), 'store_binding_sha256': custody.digest(custody.binding(store)),
            'legacy_receipt_sha256': legacy}
        if record['schema'] == 'router-managed-incarnation/v2':
            custody.require(all(record['death']['reboot'][k] == observed[k] for k in
                ('boot_id', 'prior_record_sha256', 'sidecar_snapshot_sha256', 'ledger_snapshot_sha256'))
                and record['death']['closure_sha256'] == observed['closure_sha256'])
        db.execute('ROLLBACK')
    custody.require(RunOwner.observe(usage.owner_config.router_owner_id, managed=True) == owner)
    return observed, record, state


def recovered_record(record, state, observed, stopped, pending):
    intent(pending, pending['authorization'])
    custody.require(pending['preview']['native'] == observed)
    custody.docker_identity(stopped, stopped=True)
    # validate() binds the real new stopped incarnation to the immutable old anchor.
    c = state['closure']
    proposed = {**record, 'schema': 'router-managed-incarnation/v2', 'phase': 'dead-pending', 'sequence': 0,
        'death': {'anchor_sha256': custody.digest(record['anchor']), 'closure_sha256': custody.digest(state),
                  'closure_generation': c['generation'] if c else None,
                  'closure_consumed': c['consumed'] if c else None, 'stopped': stopped,
                  'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'reboot': {**{k: observed[k] for k in ('boot_id', 'prior_record_sha256',
                                                   'sidecar_snapshot_sha256', 'ledger_snapshot_sha256')},
                             'intent_sha256': custody.digest(pending), 'acknowledged_at': pending['acknowledged_at']}},
        'transfer': None}
    custody.validate(proposed)
    if record['schema'] == 'router-managed-incarnation/v2':
        custody.require(all(record['death'][k] == v for k, v in proposed['death'].items()
                            if k != 'implementation_sha256'))
        return record
    return proposed


def native_session():
    """Fenced -> actual stopped proof -> pending -> commit, on private stdio only."""
    from .config import load_server_config
    from .maintenance import authorization
    from ..observability.dashboard.contracts import strict_json
    from ..router_manage import DEFAULT_INSTALLED_CONFIG
    signal.alarm(120)
    def receive():
        custody.require(bool(select.select([sys.stdin], [], [], 40)[0]))
        raw = sys.stdin.buffer.readline(16385)
        custody.require(len(raw) <= 16384)
        return strict_json(raw)
    def send(value):
        print(json.dumps(value, sort_keys=True, allow_nan=False), flush=True)
    request = receive()
    custody.require(type(request) is dict and set(request) == {'authorization', 'incumbent', 'intent'})
    pending = intent(request['intent'], request['authorization']) if request['intent'] is not None else None
    auth = authorization(request['authorization'], at=pending['acknowledged_at'] if pending else None)
    custody.require(auth['phase'] == 'reboot-stop')
    incumbent = request['incumbent']
    custody.require(type(incumbent) is dict and set(incumbent) == {
        'daemon_id', 'container_id', 'image_id', 'compose_project', 'compose_service'}
        and incumbent['container_id'] == auth['expected_container_id']
        and incumbent['image_id'] == auth['expected_image_id'])
    settings = load_server_config(DEFAULT_INSTALLED_CONFIG)
    custody.require(settings.router_owner_backend == 'managed-container'
                    and settings.router_owner_roster == (settings.router_owner_id,))
    raw = Path(DEFAULT_INSTALLED_CONFIG).read_bytes()
    custody.require(hashlib.sha256(raw).hexdigest() == auth['expected_configuration_revision'])
    store = KeyStore(settings.api_keys_path, _defer_open=True)
    for suffix in ('.router-writers.lock', '.router-owner'):
        _secure_database(Path(str(store.path) + suffix), exists=True)
    _secure_database(Path(settings.admission_state_path + '.router.lock'), exists=True)
    usage = UsageStore(store); usage.owner_config = settings
    with store._offline_custody(settings, allow_retained=True):
        observed, record, state = fenced_candidate(usage, auth, incumbent)
        retained = record['schema'] == 'router-managed-incarnation/v2'
        custody.require(not retained or pending is not None and
                        record['death']['reboot']['intent_sha256'] == custody.digest(pending))
        send({'fenced': observed, 'stopped': record['death']['stopped'] if retained else None})
        command = receive()
        if command == {'release': True}:
            send({'released': True})
            return
        custody.require(type(command) is dict and set(command) == {'stopped', 'fenced_sha256', 'intent'}
                        and command['fenced_sha256'] == custody.digest(observed))
        acknowledged = intent(command['intent'], auth)
        custody.require((pending is None or pending == acknowledged)
                        and acknowledged['preview']['native'] == observed
                        and acknowledged['preview']['incumbent'] == incumbent
                        and acknowledged['preview']['successor'] == auth['successor'])
        custody.require(Path(DEFAULT_INSTALLED_CONFIG).read_bytes() == raw
                        and fenced_candidate(usage, auth, incumbent) == (observed, record, state))
        proposed = recovered_record(record, state, observed, command['stopped'], acknowledged)
        path = custody.location(store, record['anchor']['run_id'])
        if proposed != record:
            custody.write(store, proposed, custody.digest(record))
        pending = custody.digest(proposed)
        send({'pending_sha256': pending})
        custody.require(receive() == {'commit': pending})
        authorization(auth, at=acknowledged['acknowledged_at'])
        custody.require(Path(DEFAULT_INSTALLED_CONFIG).read_bytes() == raw
                        and fenced_candidate(usage, auth, incumbent) == (observed, proposed, state)
                        and custody.digest(_private_json(path)) == pending)
        ready = {**proposed, 'phase': 'ready', 'sequence': 1}
        if ready != proposed:
            custody.write(store, ready, pending)
        send({'finalized': 'reboot-dead', 'death_sha256': custody.digest(ready['death'])})


@contextmanager
def _session(identity, request, *, _popen=subprocess.Popen):
    from ..observability.dashboard.contracts import strict_json
    process = _popen(['docker', 'start', '--attach', '--interactive', identity],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    def exchange(value):
        custody.require(process.poll() is None)
        process.stdin.write(json.dumps(value).encode() + b'\n'); process.stdin.flush()
        custody.require(bool(select.select([process.stdout], [], [], 40)[0]))
        raw = process.stdout.readline(16385)
        custody.require(len(raw) <= 16384)
        return strict_json(raw)
    try:
        native = exchange(request)
        custody.require(type(native) is dict and set(native) == {'fenced', 'stopped'})
        yield native['fenced'], native['stopped'], exchange, lambda: custody.require(process.poll() is None)
        process.stdin.close()
        custody.require(process.wait(timeout=5) == 0)
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)


def run(values, auth, config, *, confirm, preview_out, _run):
    """Root-owned intent precedes the exact stop; both native fences span it."""
    from pathlib import PurePosixPath
    import re
    import time
    import tomllib
    from .. import operator_config, router_manage
    from ..control_plane.mcp.auth_file import read_private_auth_file
    from ..observability.dashboard.contracts import strict_json
    from ..router_recovery_status import _covering_mount, _native_container
    from .maintenance import _current_successor, _publish, authorization
    require, digest = custody.require, custody.digest
    container = router_manage.DEFAULT_CONTAINER
    receipt = Path(values['receipt_out']); pending_path = Path(str(receipt) + '.pending')
    require(not os.path.lexists(receipt))
    successor = auth['successor']
    pending = retained_intent(values, auth)
    require(pending is None or confirm)
    # Non-secret mounted configuration is hash-bound by the protected authorization.
    def read_config(path, *, max_bytes):
        require(Path(path).is_absolute() and Path(path).resolve() != Path.home() / '.env')
        return operator_config._read_bounded(Path(path), max_bytes=max_bytes)
    compose_raw = read_config(values['compose'], max_bytes=2*1024**2)
    def unchanged():
        require(strict_json(read_private_auth_file(str(config), max_bytes=16384)) == values
                and strict_json(read_private_auth_file(values['authorization'], max_bytes=16384)) == auth
                and read_config(values['compose'], max_bytes=2*1024**2) == compose_raw
                and hashlib.sha256(compose_raw).hexdigest() == values['compose_sha256']
                and hashlib.sha256(read_config(successor['compose'], max_bytes=2*1024**2)).hexdigest() == successor['compose_sha256']
                and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == auth['boot_id'])
        require(retained_intent(values, auth) == pending)
        authorization(auth, at=pending['acknowledged_at'] if pending else None)
    unchanged()
    before = router_manage._restart_custody(container, _run)
    require(before.get('available') is True and before['container_id'] == auth['expected_container_id']
            and before['image_id'] == auth['expected_image_id']
            and before['compose_project'] == router_manage.DEFAULT_COMPOSE_PROJECT
            and before['compose_service'] == router_manage.DEFAULT_SERVICE)
    observed = _run(['docker', 'info', '--format', '{{.ID}}'], capture_output=True, text=True, timeout=5)
    require(observed.returncode == 0 and len(observed.stdout or '') <= 129)
    daemon = observed.stdout.strip()
    require(re.fullmatch('[A-Za-z0-9:._-]{1,128}', daemon))
    stable = {k: v for k, v in before.items() if k not in {'started_at', 'restart_count'}}
    incumbent = {k: before[k] for k in ('container_id', 'image_id', 'compose_project', 'compose_service')}
    incumbent['daemon_id'] = daemon
    observed = _run(['docker', 'inspect', '--format', '{{json .Config.User}}', before['container_id']],
                    capture_output=True, text=True, timeout=5)
    require(observed.returncode == 0 and len(observed.stdout or '') <= 128)
    user = strict_json(observed.stdout)
    require(type(user) is str and re.fullmatch('[1-9][0-9]*:[1-9][0-9]*', user))
    mounted = [m for m in before['mounts'] if m['destination'] == router_manage.DEFAULT_INSTALLED_CONFIG]
    require(len(mounted) == 1 and mounted[0]['type'] == 'bind' and mounted[0]['read_only'])
    raw = read_config(mounted[0]['source'], max_bytes=1024**2)
    require(hashlib.sha256(raw).hexdigest() == auth['expected_configuration_revision'])
    settings = tomllib.loads(raw.decode())['server']
    key = PurePosixPath(settings['api_keys_path'])
    state = PurePosixPath(settings['admission_state_path'] + '.router')
    storage = _covering_mount(before['mounts'], key)
    # This recovery has one durable owner volume; separate state volumes remain HOLD.
    require(key.is_absolute() and state.is_absolute() and '..' not in key.parts + state.parts
            and storage['type'] == 'volume' and not storage['read_only']
            and _covering_mount(before['mounts'], state) == storage)
    label = _run(['docker', 'inspect', '--format',
        '{{json (index .Config.Labels "com.docker.compose.project.config_files")}}', before['container_id']],
        capture_output=True, text=True, timeout=5)
    require(label.returncode == 0 and len(label.stdout or '') <= 4096 and strict_json(label.stdout) == values['compose'])
    _current_successor(successor, before, _run=_run, _read_config=read_config)
    program = 'from anvil_serving.router.reboot import native_session; native_session()'
    with _native_container(successor['image_id'], user, [(mounted[0], True), (storage, False)],
                           program, _run=_run) as (helper, helper_name, observe):
        def roster(stopped=False):
            return router_manage._offline_compose_roster(values['compose'], router_manage.DEFAULT_SERVICE,
                container, _run=_run, live_target=not stopped, _metadata_only=True,
                _reboot_helper=(helper_name, helper, successor['image_id']), _owner_state_only=True)
        baseline = roster()
        def recheck(stopped=None):
            unchanged()
            current = router_manage._restart_custody(container, _run)
            require({k: v for k, v in current.items() if k not in {'started_at', 'restart_count'}} == stable
                    and read_config(mounted[0]['source'], max_bytes=1024**2) == raw)
            current_daemon = _run(['docker', 'info', '--format', '{{.ID}}'], capture_output=True, text=True, timeout=5)
            require(current_daemon.returncode == 0 and current_daemon.stdout.strip() == daemon)
            _current_successor(successor, before, _run=_run, _read_config=read_config)
            current_roster = roster(stopped=stopped is not None)
            # Only the selected target's running/restarting/stopped observation changes.
            def others(value):
                return {'rendered': value['rendered'], 'owner_storage': value['owner_storage'], 'roster': [r for r in value['roster']
                    if r['id'] != before['container_id']]}
            require(others(current_roster) == others(baseline))
            if stopped is not None:
                require(router_manage._container_incarnation(container, _run=_run, stopped=True) == stopped)
        request = {'authorization': auth, 'incumbent': incumbent, 'intent': pending}
        with _session(helper, request) as (native, retained_stopped, exchange, alive):
            require(type(native) is dict and set(native) == {'schema', 'run_id', 'boot_id', 'prior_record_sha256',
                'sidecar_snapshot_sha256', 'ledger_snapshot_sha256', 'closure_sha256', 'store_binding_sha256',
                'legacy_receipt_sha256'} and native['schema'] == 'router-reboot-fenced/v1'
                and native['boot_id'] == auth['boot_id'] and native['sidecar_snapshot_sha256'] == auth['sidecar_snapshot_sha256'])
            from .usage_store import _uuid
            _uuid(native['run_id'])
            for k in native:
                if k.endswith('_sha256'): custody.hex_id(native[k])
            preview = {'schema': 'router-reboot-preview/v1', 'incumbent': incumbent, 'native': native,
                'compose_path': values['compose'], 'compose_sha256': values['compose_sha256'],
                'successor': successor, 'local_frontier': 'previous-boot-owner',
                'remote_memory_terminal': 'UNKNOWN', 'drained': False}
            recheck(); alive()
            if not confirm:
                if preview_out: _publish(Path(preview_out), preview)
                require(exchange({'release': True}) == {'released': True})
            else:
                require(auth['preview_sha256'] == digest(preview)
                        and auth['legacy_receipt_sha256'] == native['legacy_receipt_sha256'])
                if pending is None:
                    pending = {'schema': 'router-reboot-pending/v1', 'authorization': auth,
                               'preview': preview, 'acknowledged_at': int(time.time())}
                    intent(pending, auth)
                    _publish(pending_path, pending)
                require(pending['preview'] == preview)
                recheck(); alive()
                if retained_stopped is not None:
                    require(router_manage._container_incarnation(container, _run=_run, stopped=True) == retained_stopped)
                else:
                    stopped = _run(['docker', 'stop', before['container_id']],
                                   capture_output=True, text=True, timeout=30)
                    require(stopped.returncode == 0)
                dead = router_manage._container_incarnation(container, _run=_run, stopped=True)
                recheck(dead); alive()
                staged = exchange({'stopped': dead, 'fenced_sha256': digest(native), 'intent': pending})
                require(type(staged) is dict and set(staged) == {'pending_sha256'})
                custody.hex_id(staged['pending_sha256'])
                recheck(dead); alive()
                final = exchange({'commit': staged['pending_sha256']})
                require(type(final) is dict and set(final) == {'finalized', 'death_sha256'}
                        and final['finalized'] == 'reboot-dead')
                custody.hex_id(final['death_sha256'])
                recheck(dead)
        observe('exited')
    if not confirm:
        return preview
    result = {'schema': 'router-reboot-stop/v1', 'authorization': auth, 'preview': preview,
              'acknowledged_at': pending['acknowledged_at'], 'stopped': dead,
              'death_sha256': final['death_sha256'], 'drained': False, 'remote_memory_terminal': 'UNKNOWN'}
    _publish(receipt, result)
    return {'applied': True, 'stopped': True, 'drained': False, 'remote_memory_terminal': 'UNKNOWN',
            'receipt_sha256': digest(result), 'death_sha256': final['death_sha256']}
