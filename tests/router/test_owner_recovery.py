"""Real locks/SQLite/owned child exit, with a controlled comparable-procfs view.

This host cannot read PID1 namespace metadata. The fixture supplies that view,
not a production override or a claim of live/native ownership qualification.
"""
import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from anvil_serving import router_manage
from anvil_serving.router.admission import managed_router_admission
from anvil_serving.router.config import load_server_config
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.serve import build_server
from anvil_serving.router import usage_store as ledger
from tests.router.helpers import StaticBackend
from tests.router.key_fixtures import tmp_path as tmp_path

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='native owner requires comparable Linux procfs')


def visible_fixture_owner(host_domain_id, target_pid=None):
    pid = os.getpid()
    ns = os.stat('/proc/self/ns/pid')
    user = os.stat('/proc/self/ns/user')
    owner = ledger.RunOwner(host_domain_id, Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        ns.st_dev, ns.st_ino, ns.st_dev, ns.st_ino, os.geteuid(), user.st_dev, user.st_ino,
        pid, ledger._stat_ticks(Path('/proc/self/stat').read_text(), pid))
    return owner, ()


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(tmp_path / 'operator-home'))
    monkeypatch.setattr(ledger, '_linux_owner', visible_fixture_owner)
    keys = KeyStore.initialize(tmp_path / 'owned' / 'keys.sqlite3')
    ledger.UsageStore(keys).migrate()
    path = tmp_path / 'router.toml'
    path.write_text(Path('configs/example.toml').read_text() + '\n[server]\nauth_env="SYNTHETIC_AUTH"\n' +
                    'api_keys_path=' + json.dumps(str(keys.path)) + '\nadmission_state_path=' +
                    json.dumps(str(keys.path.parent / 'intent.json')) + '\nrouter_owner_id="fixture"\n' +
                    'router_owner_roster=["fixture"]\nusage_domain_id="fixture"\nusage_enabled=true\n')
    return path


CHILD = '''
import json, os, sys, uuid
from anvil_serving.router.serve import build_server
from anvil_serving.router.config import load_server_config
from anvil_serving.router.keys import KeyStore
from anvil_serving.router import usage_store as ledger
from anvil_serving.router.identity import legacy_caller
from anvil_serving.router.decision_log import TokenUsage, TokenDirection
from tests.router.helpers import StaticBackend
from tests.router.test_owner_recovery import visible_fixture_owner
ledger._linux_owner = visible_fixture_owner
server = build_server(sys.argv[1], host='127.0.0.1', port=0,
    backends={'primary-local':StaticBackend([]),'omni-local':StaticBackend([])},env={'SYNTHETIC_AUTH':'synthetic'})
owner = server.anvil_router_admission
usage = ledger.UsageStore(KeyStore(load_server_config(sys.argv[1]).api_keys_path))
run = owner.usage_run_id
committed = ledger.RequestStart(str(uuid.uuid4()),run,ledger._now(),legacy_caller(),'chat','llm.primary')
usage.start(committed,authority_scope=owner.usage_scope())
usage.finalize(ledger.Terminal(committed.request_id,ledger._now(),True,'success','success','success',
    ledger.RouteAssociation(),TokenUsage(TokenDirection(7,'measured'),TokenDirection(5,'measured'))))
if sys.argv[2]=='crash':
    incomplete=ledger.RequestStart(str(uuid.uuid4()),run,ledger._now(),legacy_caller(),'chat','llm.primary')
    usage.start(incomplete,authority_scope=owner.usage_scope())
    usage.note_dispatch(incomplete.request_id,ledger.RouteAssociation())
else:
    token=owner.quiesce_router(confirm=True,dry_run=False)['barrier_token']
    owner.consume(token)
    server.server_close()
print(json.dumps({'run':run,'committed':committed.request_id}),flush=True)
if sys.argv[2]=='crash':os._exit(0)
'''


def prior(config, mode):
    result = subprocess.run([sys.executable, '-c', CHILD, str(config), mode],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('mode', ['clean', 'crash'])
def test_supported_native_restart_recovers_and_readmits_new_generation(config, monkeypatch, mode):
    original = prior(config, mode)
    config.write_text(config.read_text() + '\n# reviewed successor configuration revision\n')
    settings = load_server_config(str(config))
    store = KeyStore(settings.api_keys_path)
    state = Path(settings.admission_state_path + '.router')
    previous = json.loads(state.read_text())['closure']
    with store._connect() as db:
        committed = db.execute('SELECT * FROM usage_details WHERE request_id=?', (original['committed'],)).fetchone()
    monkeypatch.setattr(router_manage, 'DEFAULT_INSTALLED_CONFIG', str(config))
    assert router_manage._offline_router_start()['offline'] is True
    # Read-only probe must not fabricate a new run or recovery event.
    with store._connect() as db:
        assert db.execute('SELECT state FROM usage_runs').fetchall() == [('live',)]

    server = build_server(str(config), host='127.0.0.1', port=0,
                         backends={'primary-local':StaticBackend([]),'omni-local':StaticBackend([])},
                         env={'SYNTHETIC_AUTH':'synthetic'})
    owner = server.anvil_router_admission
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        assert owner.status()['state'] == 'quiesced' and not owner.status()['cutover_pending']
        assert owner.status()['generation'] == (previous['generation'] if previous else 0) + 1
        assert owner.usage_run_id != original['run']
        with store._connect() as db:
            assert db.execute('SELECT state FROM usage_runs WHERE run_id=?', (original['run'],)).fetchone() == ('dead',)
            assert db.execute('SELECT * FROM usage_details WHERE request_id=?', (original['committed'],)).fetchone() == committed
            if mode == 'crash':
                payloads = [json.loads(row[0]) for row in db.execute('SELECT terminal_payload FROM usage_details WHERE request_id!=?', (original['committed'],))]
                assert len(payloads) == 1 and payloads[0]['outcome'] == 'interrupted'
                assert 'recovered_partial' in payloads[0]['coverage']
        def transition(action, **options):
            return router_manage.transition_request(action, scope='router',
                router_url='http://127.0.0.1:' + str(server.server_port),
                env={'ANVIL_ROUTER_TOKEN':'synthetic'}, confirm=True, dry_run=False, **options)['result']
        token = transition('quiesce')['barrier_token']
        assert transition('readmit', barrier_token=token)['applied']
        permit = owner.acquire('chat'); permit.release()
        token = owner.quiesce_router(confirm=True, dry_run=False)['barrier_token']
        assert owner.drain_router(token, 3)['drained']
        owner.consume(token)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        assert not thread.is_alive()


def test_successor_keeps_remote_unknown_closed_after_local_reconciliation(config):
    original = prior(config, 'crash')
    config.write_text(config.read_text() + '\n[[router.memory_routes]]\nalias="memory.fixture"\n'
        'principal="fixture-key"\nbackend="hindsight"\nbank="fixture"\n'
        'base_url="http://127.0.0.1:65530"\nauth_env="SYNTHETIC_MEMORY"\n')
    settings = load_server_config(str(config))
    server = build_server(str(config), host='127.0.0.1', port=0,
                         backends={'primary-local':StaticBackend([]),'omni-local':StaticBackend([])},
                         env={'SYNTHETIC_AUTH':'synthetic','SYNTHETIC_MEMORY':'synthetic'})
    owner = server.anvil_router_admission
    try:
        with KeyStore(settings.api_keys_path)._connect() as db:
            assert db.execute('SELECT state FROM usage_runs WHERE run_id=?', (original['run'],)).fetchone() == ('dead',)
        token = owner.quiesce_router(confirm=True, dry_run=False)['barrier_token']
        assert owner.drain_router(token, 1)['unknown'] == ['memory']
        with pytest.raises(ValueError, match='router_owner_roster_unknown'):
            owner.readmit_router(token, confirm=True, dry_run=False)
        assert owner.status()['state'] == 'quiesced'
        with pytest.raises(ValueError, match='router_owned_work_not_drained'):
            server.server_close()
    finally:
        # Fixture teardown only: production close correctly refuses UNKNOWN.
        server.anvil_routing.close()
        server.socket.close()
        os.close(owner._owner_descriptor)
        owner._owner_descriptor = None


def test_retained_owner_transfer_refuses_actual_competing_writer(config):
    import fcntl
    prior(config, 'clean')
    settings = load_server_config(str(config))
    state = Path(settings.admission_state_path + '.router')
    before = state.read_bytes()
    with open(settings.api_keys_path + '.router-writers.lock', 'r+') as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='custody is busy'):
            managed_router_admission(settings, 'new-revision')
    assert state.read_bytes() == before


def test_successor_refuses_changed_pid_namespace_even_after_real_child_exit(config, monkeypatch):
    prior(config, 'clean')
    settings = load_server_config(str(config))
    state = Path(settings.admission_state_path + '.router')
    before = state.read_bytes()
    def different_namespace(host_domain_id, target_pid=None):
        owner, mounts = visible_fixture_owner(host_domain_id, target_pid)
        return replace(owner, pid_namespace_inode=owner.pid_namespace_inode + 1,
                       procfs_pid_namespace_inode=owner.procfs_pid_namespace_inode + 1), mounts
    monkeypatch.setattr(ledger, '_linux_owner', different_namespace)
    with pytest.raises(ValueError):
        managed_router_admission(settings, 'new-revision')
    assert state.read_bytes() == before
    with KeyStore(settings.api_keys_path)._connect() as db:
        assert db.execute('SELECT state FROM usage_runs').fetchall() == [('live',)]


@pytest.mark.parametrize('observation', ['live', 'unknown'])
def test_successor_refuses_unverified_previous_owner_without_mutation(config, monkeypatch, observation):
    prior(config, 'clean')
    settings = load_server_config(str(config))
    state = Path(settings.admission_state_path + '.router')
    before = state.read_bytes()
    monkeypatch.setattr(ledger, 'observe_run', lambda old: observation)
    with pytest.raises(ValueError):
        managed_router_admission(settings, 'new-revision')
    assert state.read_bytes() == before
    with KeyStore(settings.api_keys_path)._connect() as db:
        assert db.execute('SELECT state FROM usage_runs').fetchall() == [('live',)]
