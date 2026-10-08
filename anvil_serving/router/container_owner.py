"""Protected, native-Docker custody adjunct. Never a caller-supplied DEAD flag.

The bounded native helper holds its real fences while the host verifies Docker
after staging. Only the host's exact digest commit finalizes authority. Pending
records survive interruption but are deliberately ignored by owner readers.
"""
from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import select
import sqlite3
import sys
import tempfile

from .keys import (KeyStoreError, _private_created_descriptor, _private_json,
                   _secure_database, _secure_directory, _validate_store_binding)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def require(ok):
    if not ok:
        raise KeyStoreError('router container custody unavailable')


def hex_id(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None)


def timestamp(value):
    require(type(value) is str and 1 <= len(value) <= 64 and value.endswith('Z')
            and not value.startswith('0001-'))
    parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    return parsed


def docker_identity(value, *, stopped=False):
    names = {'daemon_id', 'container_id', 'image_id', 'started_at', 'restart_count',
             'compose_project', 'compose_service'}
    if stopped:
        names |= {'finished_at', 'status', 'running', 'paused', 'restarting', 'pid'}
    require(type(value) is dict and set(value) == names)
    hex_id(value['container_id'])
    require(type(value['image_id']) is str and value['image_id'].startswith('sha256:'))
    hex_id(value['image_id'][7:])
    require(type(value['daemon_id']) is str and re.fullmatch('[A-Za-z0-9:._-]{1,128}', value['daemon_id']) is not None)
    for name in ('compose_project', 'compose_service'):
        require(type(value[name]) is str and re.fullmatch('[A-Za-z0-9_.-]{1,128}', value[name]) is not None)
    require(type(value['restart_count']) is int and 0 <= value['restart_count'] < 2**53)
    start = timestamp(value['started_at'])
    if stopped:
        require(timestamp(value['finished_at']) >= start and value['status'] in {'exited', 'dead'}
                and all(type(value[k]) is bool and value[k] is False for k in ('running', 'paused', 'restarting'))
                and type(value['pid']) is int and value['pid'] == 0)
    return value


def location(store, run_id):
    from .usage_store import _uuid
    _uuid(run_id)
    return Path(str(store.path) + '.router-incarnations') / (run_id + '.json')


def binding(store):
    value = _private_json(Path(str(store.path) + '.router-owner'))
    _validate_store_binding(value, store.path)
    gate = Path(str(store.path) + '.router-writers.lock')
    _secure_database(gate, exists=True)
    info = gate.stat()
    require(value['gate_identity'] == [info.st_dev, info.st_ino])
    return value


def closure(store):
    b = binding(store)
    value = _private_json(Path(b['state_path']))
    require(type(value) is dict and set(value) == {'schema', 'owner_id', 'closure'}
            and value['schema'] == 'router-admission/v1' and value['owner_id'] == b['owner_id'])
    c = value['closure']
    if c is not None:
        require(type(c) is dict and set(c) == {'barrier_token', 'configuration_revision', 'roster_revision',
                                            'policy_revision', 'generation', 'consumed'})
        for k in ('barrier_token', 'configuration_revision', 'roster_revision', 'policy_revision'):
            hex_id(c[k])
        require(type(c['generation']) is int and 1 <= c['generation'] < 2**53
                and type(c['consumed']) is bool)
    return value


def roster(owner):
    return digest({'roster': (owner.host_domain_id,), 'owner': asdict(owner)})


def validate(record):
    from .usage_store import RunOwner, _id, _uuid
    require(type(record) is dict and set(record) == {'schema', 'phase', 'anchor', 'sequence', 'death', 'transfer'}
            and record['schema'] == 'router-managed-incarnation/v1')
    a = record['anchor']
    require(type(a) is dict and set(a) == {'owner_backend', 'owner_id', 'domain_id', 'run_id', 'run_owner',
                                         'store_binding_sha256', 'configuration_revision', 'roster_revision',
                                         'docker', 'native_view'} and a['owner_backend'] == 'managed-container')
    _id(a['owner_id']); _id(a['domain_id']); _uuid(a['run_id'])
    require(type(a['run_owner']) is dict and set(a['run_owner']) == {f.name for f in fields(RunOwner)})
    require(all(type(v) is (str if k in {'host_domain_id', 'boot_id'} else int)
                and (bool(v) if type(v) is str else v >= 0) for k, v in a['run_owner'].items()))
    owner = RunOwner(**a['run_owner'])
    require(owner.host_domain_id == a['owner_id'] and roster(owner) == a['roster_revision'])
    for k in ('store_binding_sha256', 'configuration_revision', 'roster_revision'):
        hex_id(a[k])
    docker_identity(a['docker'])
    view = a['native_view']
    expected = {k: v for k, v in asdict(owner).items() if k not in {'host_domain_id', 'pid', 'start_ticks'}}
    require(type(view) is dict and set(view) == set(expected) | {'container_init_start_ticks'}
            and all(type(view[k]) is type(v) and view[k] == v for k, v in expected.items())
            and type(view['container_init_start_ticks']) is int and view['container_init_start_ticks'] > 0)
    phase, seq, dead, transfer = (record[k] for k in ('phase', 'sequence', 'death', 'transfer'))
    require(type(seq) is int and ((phase in {'live-pending', 'live'} and seq == 0 and dead is None and transfer is None)
            or (phase == 'dead-pending' and seq == 0 and dead is not None and transfer is None)
            or (phase == 'ready' and seq == 1 and dead is not None and transfer is None)
            or (phase == 'transferred' and seq == 2 and dead is not None and transfer is not None)))
    if dead is not None:
        require(type(dead) is dict and set(dead) == {'anchor_sha256', 'closure_sha256', 'closure_generation',
                                                   'closure_consumed', 'stopped', 'implementation_sha256'})
        require(dead['anchor_sha256'] == digest(a))
        for k in ('closure_sha256', 'implementation_sha256'):
            hex_id(dead[k])
        generation, consumed = dead['closure_generation'], dead['closure_consumed']
        require((generation is None and consumed is None) or
                (type(generation) is int and 1 <= generation < 2**53 and type(consumed) is bool))
        docker_identity(dead['stopped'], stopped=True)
        require(all(dead['stopped'][k] == v for k, v in a['docker'].items()))
    if transfer is not None:
        require(type(transfer) is dict and set(transfer) == {'successor_run_id', 'successor_roster_revision',
                                                           'successor_configuration_revision', 'successor_generation'})
        _uuid(transfer['successor_run_id'])
        require(transfer['successor_run_id'] != a['run_id'])
        hex_id(transfer['successor_roster_revision']); hex_id(transfer['successor_configuration_revision'])
        require(type(transfer['successor_generation']) is int and 1 <= transfer['successor_generation'] < 2**53)
    return record


def read(store, row, configuration_revision):
    record = validate(_private_json(location(store, row['run_id'])))
    a = record['anchor']
    from .usage_store import RunOwner
    owner = RunOwner(**{f.name: row[f.name] for f in fields(RunOwner)})
    b = binding(store)
    require(a['run_id'] == row['run_id'] and a['domain_id'] == row['domain_id']
            and a['run_owner'] == asdict(owner) and a['configuration_revision'] == configuration_revision
            and a['owner_id'] == b['owner_id'] and a['store_binding_sha256'] == digest(b))
    return record


def write(store, record, expected):
    validate(record)
    path = location(store, record['anchor']['run_id'])
    _secure_directory(path.parent, create=True)
    if expected is None:
        require(not os.path.lexists(path))
    else:
        require(digest(_private_json(path)) == expected)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        _private_created_descriptor(fd)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(record, out, sort_keys=True, separators=(',', ':'), allow_nan=False)
            out.flush(); os.fsync(out.fileno())
        # Every writer holds the same actual gate; this is a bounded CAS.
        if expected is None:
            require(not os.path.lexists(path))
        else:
            require(digest(_private_json(path)) == expected)
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ready_transfers(usage):
    """Current-closure CAS is checked before any ledger/config transfer writes."""
    result = []
    with usage.key_store._connect() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT * FROM usage_runs WHERE domain_id=? LIMIT 1025',
                          (usage.owner_config.usage_domain_id,)).fetchall()
        require(len(rows) <= 1024)
        for row in rows:
            revision = db.execute('SELECT configuration_revision FROM usage_coverage_segments WHERE run_id=? '
                                  'ORDER BY started_at,segment_id LIMIT 1', (row['run_id'],)).fetchone()[0]
            record = read(usage.key_store, row, revision)
            require(record['phase'] in {'ready', 'transferred'})
            if record['phase'] == 'ready':
                state = closure(usage.key_store)
                require(record['death']['closure_sha256'] == digest(state))
                c = state['closure']
                require(c is None or c['configuration_revision'] == record['anchor']['configuration_revision'])
                result.append((record, digest(record)))
    return result


def finish_transfers(usage, pending, run_id, actual_owner, revision, state):
    require(getattr(usage.key_store._writer_context, 'offline_custody', False))
    with usage.key_store._connect() as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM usage_runs WHERE run_id=?', (run_id,)).fetchone()
        require(row is not None and row['state'] == 'live' and usage._run_owner(row) == actual_owner
                and row['domain_id'] == usage.owner_config.usage_domain_id
                and db.execute('SELECT configuration_revision FROM usage_domains WHERE domain_id=?',
                               (row['domain_id'],)).fetchone()[0] == revision)
    from .usage_store import RunOwner
    require(RunOwner.observe(actual_owner.host_domain_id, managed=True) == actual_owner
            and closure(usage.key_store)['closure'] == state)
    for record, expected in pending:
        require(record['phase'] == 'ready' and state['generation'] == (record['death']['closure_generation'] or 0) + 1
                and state['roster_revision'] == roster(actual_owner) and state['configuration_revision'] == revision
                and state['consumed'] is False)
        record = {**record, 'phase': 'transferred', 'sequence': 2,
                  'transfer': {'successor_run_id': run_id, 'successor_roster_revision': roster(actual_owner),
                               'successor_configuration_revision': revision, 'successor_generation': state['generation']}}
        write(usage.key_store, record, expected)


@contextmanager
def live_writer(store):
    """Join writer exclusion; the verified live producer retains its own fd."""
    import fcntl
    b = binding(store)
    gate = Path(str(store.path) + '.router-writers.lock')
    fd = os.open(gate, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    producer = os.open(b['state_path'] + '.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _private_created_descriptor(fd); _private_created_descriptor(producer)
        _secure_database(Path(b['state_path'] + '.lock'), exists=True, identity=os.fstat(producer))
        _secure_database(gate, exists=True, identity=os.fstat(fd))
        require([os.fstat(fd).st_dev, os.fstat(fd).st_ino] == b['gate_identity'])
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(producer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise KeyStoreError('router native producer unavailable')
        yield
    finally:
        os.close(producer); os.close(fd)


def live_record(usage, docker):
    from .usage_store import RunOwner, observe_run, _stat_ticks
    from ..router_manage import DEFAULT_INSTALLED_CONFIG, _transition_request
    settings = usage.owner_config
    with usage.key_store._connect() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM usage_runs WHERE domain_id=? AND state!='dead' LIMIT 1025",
                          (settings.usage_domain_id,)).fetchall()
    require(len(rows) == 1 and rows[0]['state'] == 'live')
    row = rows[0]; owner = usage._run_owner(row)
    require(observe_run(owner, managed=True) == 'live')
    view = RunOwner.observe(settings.router_owner_id, managed=True)
    names = [f.name for f in fields(RunOwner) if f.name not in {'pid', 'start_ticks'}]
    require(all(getattr(view, k) == getattr(owner, k) for k in names))
    revision = hashlib.sha256(Path(DEFAULT_INSTALLED_CONFIG).read_bytes()).hexdigest()
    token = os.environ.get(settings.auth_env, '')
    require(bool(token))
    status = _transition_request('status', scope='router', router_url='http://127.0.0.1:8000',
                                 env={'ANVIL_ROUTER_TOKEN': token}).get('result', {})
    require(status.get('durable') is True and status.get('configuration_revision') == revision
            and status.get('roster_revision') == roster(owner) and status.get('state') == 'quiesced')
    native = {k: v for k, v in asdict(owner).items() if k not in {'host_domain_id', 'pid', 'start_ticks'}}
    native['container_init_start_ticks'] = _stat_ticks(Path('/proc/1/stat').read_text(), 1)
    if docker is None:
        return None  # Readiness metadata only, never an anchor or receipt.
    anchor = {'owner_backend': 'managed-container', 'owner_id': settings.router_owner_id,
              'domain_id': settings.usage_domain_id, 'run_id': row['run_id'], 'run_owner': asdict(owner),
              'store_binding_sha256': digest(binding(usage.key_store)), 'configuration_revision': revision,
              'roster_revision': roster(owner), 'docker': docker_identity(docker), 'native_view': native}
    return {'schema': 'router-managed-incarnation/v1', 'phase': 'live-pending', 'anchor': anchor,
            'sequence': 0, 'death': None, 'transfer': None}


def stopped_record(usage, stopped):
    docker_identity(stopped, stopped=True)
    with usage.key_store._connect() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM usage_runs WHERE domain_id=? AND state!='dead' LIMIT 1025",
                          (usage.owner_config.usage_domain_id,)).fetchall()
        require(len(rows) == 1)
        row = rows[0]
        revision = db.execute('SELECT configuration_revision FROM usage_domains WHERE domain_id=?',
                              (row['domain_id'],)).fetchone()[0]
    record = read(usage.key_store, row, revision)
    require(record['phase'] in {'live', 'dead-pending', 'ready'})
    require(all(stopped[k] == v for k, v in record['anchor']['docker'].items()))
    from .usage_store import observe_run
    require(observe_run(usage._run_owner(row), managed=True) != 'live')
    state = closure(usage.key_store); c = state['closure']
    require(c is None or (c['configuration_revision'] == revision and c['roster_revision'] == record['anchor']['roster_revision']))
    dead = {'anchor_sha256': digest(record['anchor']), 'closure_sha256': digest(state),
            'closure_generation': c['generation'] if c else None, 'closure_consumed': c['consumed'] if c else None,
            'stopped': stopped, 'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if record['phase'] == 'ready':
        # The implementation hash belongs to the original proof. Revalidate
        # its exact death and current closure without replacing that history.
        require(all(record['death'][k] == v for k, v in dead.items() if k != 'implementation_sha256'))
        return record
    return {**record, 'phase': 'dead-pending', 'death': dead}


def native_ready():
    from .config import load_server_config
    from .keys import KeyStore
    from .usage_store import UsageStore
    from ..router_manage import DEFAULT_INSTALLED_CONFIG
    settings = load_server_config(DEFAULT_INSTALLED_CONFIG)
    require(settings.router_owner_backend == 'managed-container' and settings.router_owner_id)
    usage = UsageStore(KeyStore(settings.api_keys_path)); usage.owner_config = settings
    # Readback only; the actual stage/commit repeats all checks under writerEX.
    live_record(usage, None)
    print('ready', flush=True)


def native_transaction(kind, docker):
    """Bounded stdin commit protocol, invoked only by trusted native Docker CLI.

    No HTTP route exposes this operation. Pending is non-authoritative even
    after host/helper failure. EOF/timeout aborts without finalizing custody.
    """
    from .config import load_server_config
    from .keys import KeyStore
    from .usage_store import UsageStore
    from ..router_manage import DEFAULT_INSTALLED_CONFIG
    settings = load_server_config(DEFAULT_INSTALLED_CONFIG)
    require(settings.router_owner_backend == 'managed-container' and settings.router_owner_id
            and settings.router_owner_roster == (settings.router_owner_id,))
    store = KeyStore(settings.api_keys_path)
    usage = UsageStore(store); usage.owner_config = settings
    context = live_writer(store) if kind == 'live' else store._offline_custody(settings, allow_retained=True)
    require(kind in {'live', 'dead'})
    with context:
        proposed = live_record(usage, docker) if kind == 'live' else stopped_record(usage, docker)
        path = location(store, proposed['anchor']['run_id'])
        old = validate(_private_json(path)) if os.path.lexists(path) else None
        if old is not None:
            require(old['anchor'] == proposed['anchor'])
            if kind == 'live':
                require(old['phase'] in {'live-pending', 'live'})
            else:
                require(old['phase'] in {'live', 'dead-pending', 'ready'})
                require(old['death'] is None or old['death'] == proposed['death'])
        repeated_ready = kind == 'dead' and old is not None and old['phase'] == 'ready'
        if not repeated_ready and (old is None or old['phase'] != 'live' or kind != 'live'):
            write(store, proposed, digest(old) if old else None)
        else:
            proposed = old
        pending = digest(proposed)
        print(json.dumps({'pending_sha256': pending, 'run_id': proposed['anchor']['run_id']}), flush=True)
        require(bool(select.select([sys.stdin], [], [], 30)[0]))
        raw = sys.stdin.buffer.readline(1025)
        from ..observability.dashboard.contracts import strict_json
        require(strict_json(raw) == {'commit': pending} and len(raw) <= 1024)
        # Revalidate actual producer/row and current closure under held fences.
        check = live_record(usage, docker) if kind == 'live' else stopped_record(usage, docker)
        require(check['anchor'] == proposed['anchor'] and (kind == 'live' or check['death'] == proposed['death']))
        finalized = {**proposed, 'phase': 'live' if kind == 'live' else 'ready', 'sequence': 0 if kind == 'live' else 1}
        if repeated_ready:
            require(finalized == proposed and digest(validate(_private_json(path))) == pending)
        else:
            write(store, finalized, pending)
        print(json.dumps({'finalized': kind, 'run_id': proposed['anchor']['run_id']}), flush=True)
