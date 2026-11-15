"""The host startup-abort wrapper resumes an exact stopped intent safely."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from anvil_serving import router_manage, router_startup_abort as host
from anvil_serving.router import container_owner as custody


def test_stopped_pending_retry_reuses_intent_and_repeats_namespace_proof(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    compose = tmp_path / 'compose.json'
    compose.write_text('{}')
    compose.chmod(0o600)
    source = tmp_path / 'router.toml'
    source.write_text('[server]\napi_keys_path="/keys/keys.sqlite"\n'
                      'admission_state_path="/keys/admission.json"\n')
    source.chmod(0o600)
    revision = hashlib.sha256(source.read_bytes()).hexdigest()
    compose_sha = hashlib.sha256(compose.read_bytes()).hexdigest()
    container_id, image_id, helper_image = 'b' * 64, 'sha256:' + 'c' * 64, 'sha256:' + 'd' * 64
    run_id = str(uuid.uuid4())
    owner = dict(host_domain_id='fixture', boot_id=str(uuid.uuid4()), pid=1,
                 start_ticks=100, pid_namespace_device=5, pid_namespace_inode=99,
                 procfs_pid_namespace_device=5, procfs_pid_namespace_inode=1, uid=1000,
                 user_namespace_device=5, user_namespace_inode=1)
    native = dict(schema='router-startup-abort-preview/v1', run_id=run_id,
                  configuration_revision=revision, roster_revision='1' * 64, generation=1,
                  closure_sha256='2' * 64, sidecar_snapshot_sha256='3' * 64,
                  run_owner=owner, registration_sha256='4' * 64,
                  predecessor_transfers_sha256='5' * 64)
    preview = dict(schema='router-startup-abort-preview/v1', native=native,
                   container_id=container_id, image_id=image_id,
                   daemon_id='daemon', helper_image_id=helper_image,
                   compose_sha256=compose_sha)
    receipt = tmp_path / 'receipt.json'
    declaration = tmp_path / 'declaration.json'
    values = dict(schema='router-startup-abort-config/v1', compose=str(compose),
                  compose_sha256=compose_sha, expected_container_id=container_id,
                  expected_image_id=image_id, helper_image_id=helper_image, run_id=run_id,
                  configuration_revision=revision, preview_sha256=custody.digest(preview),
                  acknowledgement=host.ACK, receipt_out=str(receipt))
    raw = json.dumps(values, sort_keys=True).encode()
    declaration.write_bytes(raw)
    declaration.chmod(0o600)
    stopped = dict(daemon_id='daemon', container_id=container_id, image_id=image_id,
                   compose_project=router_manage.DEFAULT_COMPOSE_PROJECT,
                   compose_service=router_manage.DEFAULT_SERVICE,
                   started_at='2026-10-10T00:00:00Z', restart_count=7,
                   finished_at='2026-10-10T00:01:00Z', status='exited', running=False,
                   paused=False, restarting=False, pid=0)
    authorization = dict(schema='router-startup-abort-authorization/v1',
                         operation_id='6' * 64, run_id=run_id,
                         configuration_revision=revision, roster_revision='1' * 64,
                         generation=1, closure_sha256='2' * 64,
                         sidecar_snapshot_sha256='3' * 64,
                         acknowledged_at=1, expires_at=901)
    pending = dict(config_sha256=hashlib.sha256(raw).hexdigest(), preview=preview,
                   authorization=authorization, stopped=stopped)
    pending_path = Path(str(receipt) + '.pending')
    pending_path.write_text(json.dumps(pending, sort_keys=True))
    pending_path.chmod(0o600)

    config_mount = dict(type='bind', source=str(source),
                        destination=router_manage.DEFAULT_INSTALLED_CONFIG, read_only=True)
    storage_mount = dict(type='volume', source='fixture-owner-state',
                         destination='/keys', read_only=False)
    observed = dict(available=True, container_id=container_id, image_id=image_id,
                    image_reference='fixture:router', started_at=stopped['started_at'],
                    restart_count=stopped['restart_count'],
                    compose_project=router_manage.DEFAULT_COMPOSE_PROJECT,
                    compose_service=router_manage.DEFAULT_SERVICE,
                    mounts=[config_mount, storage_mount])
    monkeypatch.setattr(host, 'os', SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(router_manage, '_restart_custody', lambda *_args: observed)
    monkeypatch.setattr(router_manage, 'docker_state', lambda *_args, **_kwargs: 'exited')
    monkeypatch.setattr(router_manage, '_container_incarnation',
                        lambda *_args, **_kwargs: stopped)
    roster = dict(rendered={'services': {}}, owner_storage={'configuration_revision': revision},
                  roster=[{'id': container_id}])
    monkeypatch.setattr(router_manage, '_offline_compose_roster',
                        lambda *_args, **_kwargs: roster)

    def protected(path, **_kwargs):
        path = Path(path)
        if path == declaration:
            return raw
        return path.read_bytes()

    monkeypatch.setattr(host, 'read_private_auth_file', protected)
    calls = []

    def execute(argv, **_kwargs):
        calls.append(argv)
        if argv[:2] == ['docker', 'info']:
            return SimpleNamespace(returncode=0, stdout='daemon\n')
        assert argv[:4] == ['docker', 'inspect', '--format', '{{json .Config.User}}']
        return SimpleNamespace(returncode=0, stdout=json.dumps('1000:1000'))

    @contextmanager
    def helper(*_args, **_kwargs):
        yield 'e' * 64, 'startup-abort-helper', lambda state: calls.append(['observe', state])

    scans = []

    def scan(run_owner):
        scans.append(run_owner)
        return dict(boot_id=run_owner.boot_id,
                    pid_namespace_device=run_owner.pid_namespace_device,
                    pid_namespace_inode=run_owner.pid_namespace_inode,
                    processes_scanned=929, inaccessible=0, matches=0)

    @contextmanager
    def session(_identity, request):
        assert request == {'run_id': run_id, 'configuration_revision': revision}
        exchanges = []

        def exchange(value):
            exchanges.append(value)
            if len(exchanges) == 1:
                assert value == {'authorization': authorization}
                return {'namespace_scan': owner}
            if len(exchanges) == 2:
                assert set(value) == {'proof'} and value['proof']['matches'] == 0
                return {'namespace_scan': owner}
            assert len(exchanges) == 3 and set(value) == {'proof'}
            return {'finalized': {'schema': 'router-managed-startup-abort/v1',
                                  'phase': 'aborted'}}

        yield native, None, exchange, lambda: None

    monkeypatch.setattr(host, '_native_container', helper)
    monkeypatch.setattr(host, '_session', session)
    monkeypatch.setattr(host, 'namespace_scan', scan)

    result = host.run.__wrapped__(declaration, confirm=True, _run=execute)
    assert result['applied'] and result['stopped'] and not result['readmitted']
    assert len(scans) == 3  # pre-stop proof plus two native requests under both fences
    assert all(command[:2] != ['docker', 'stop'] for command in calls)
    assert json.loads(receipt.read_text())['pending'] == pending
    before = list(calls)
    assert host.run.__wrapped__(declaration, confirm=True, _run=execute) == result
    assert calls == before
