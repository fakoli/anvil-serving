"""Supported operator accounting commands against real protected storage/HTTP."""
import json
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from anvil_serving.cli import main
from anvil_serving import router_diagnostics as cli
from anvil_serving.router import keys, key_container
from anvil_serving.router.usage_store import UsageStore, RequestStart
from anvil_serving.router.identity import legacy_caller
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_usage_admin import policy as policy, server, ADMIN
from tests.router.test_usage_lifecycle import store as store, ident, terminal, AT, END


def local(tmp_path):
    path = tmp_path / 'private' / 'keys.sqlite3'
    store = keys.KeyStore.initialize(path)
    UsageStore(store).migrate()
    config = tmp_path / 'router.toml'
    config.write_text('[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path=' + json.dumps(str(path)) + '\n')
    return store, ['--config', str(config)]


def test_public_binding_preview_cas_readback_and_wide_grants(tmp_path, capsys):
    store, common = local(tmp_path)
    models = ['model_' + str(n) + '\u00e9' * 116 for n in range(64)]
    metadata, secret = store.create('fixture', models, ['/v1/chat/completions'])
    before = store.authenticate(secret)
    command = ['router', 'keys', 'bind', *common, '--key-id', metadata['key_id'], '--kind', 'human',
               '--owner-id', 'human_fixture', '--expected-revision', '0']
    assert main([*command, '--dry-run']) == 0
    assert json.loads(capsys.readouterr().out)['dry_run'] is True
    assert store.authenticate(secret) == before
    assert main(command) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['actor']['binding_revision'] == 1 and not data['dry_run']
    assert store.authenticate(secret).models == before.models
    assert main(command) != 0
    assert 'human_fixture' not in capsys.readouterr().err
    with store._connect() as db:
        assert db.execute('SELECT revision FROM key_owner_bindings').fetchall() == [(1,)]


def test_public_consistent_backup_restore_refuses_overwrite(tmp_path, capsys):
    store, common = local(tmp_path)
    store.create('fixture', ['llm.primary'], ['/v1/chat/completions'])
    backup, restored = tmp_path / 'snapshots' / 'backup.sqlite3', tmp_path / 'restore' / 'keys.sqlite3'
    assert main(['router', 'keys', 'backup', *common, '--out', str(backup)]) == 0
    assert json.loads(capsys.readouterr().out)['copied']
    assert main(['router', 'keys', 'restore', '--snapshot', str(backup), '--out', str(restored)]) == 0
    assert json.loads(capsys.readouterr().out)['restored']
    assert keys.KeyStore(restored).list_keys() == store.list_keys()
    for command in [['backup', *common], ['restore', '--snapshot', str(backup)]]:
        before = restored.read_bytes()
        assert keys.dispatch([*command, '--out', str(restored)]) == 2
        assert restored.read_bytes() == before
        capsys.readouterr()


def test_expired_binding_preview_has_no_writes(tmp_path):
    store, common = local(tmp_path)
    key, _ = store.create('fixture', ['llm.primary'], ['/v1/chat/completions'])
    with store._connect() as db:
        db.execute('UPDATE keys SET expires_at=0 WHERE key_id=?', (key['key_id'],))
    assert keys.dispatch(['bind', *common, '--key-id', key['key_id'], '--kind', 'service',
                          '--owner-id', 'service_fixture', '--expected-revision', '0', '--dry-run']) == 2
    with store._connect() as db:
        assert db.execute('SELECT * FROM key_owner_bindings').fetchall() == []


def test_real_http_exact_integer_identity_and_typed_refusal(policy, store, monkeypatch, capsys):
    usage, _, run, scope = store
    start = RequestStart(ident(), run, AT, legacy_caller(), 'chat', 'llm.primary', attempt_id=ident())
    usage.start(start, authority_scope=scope)
    usage.finalize(terminal(start))
    with usage.key_store._connect() as db:
        db.execute("UPDATE usage_cumulative SET measured_input=?", (10**16+7,))
    monkeypatch.setattr('anvil_serving.router.usage_store._now', lambda: END)
    monkeypatch.setenv('SYNTHETIC_USAGE', ADMIN)
    with server(policy, usage_store=usage, usage_domain_id='domain_fixture') as address:
        common = ['--router-url', f'http://127.0.0.1:{address[1]}', '--auth-env', 'SYNTHETIC_USAGE']
        assert main(['router', 'usage', 'query', *common, '--granularity', 'cumulative', '--group-by', '["credential_id"]']) == 0
        data = json.loads(capsys.readouterr().out)
        assert data['measured_input'] == 10**16+7 and data['coverage_complete'] is False
        assert data['groups'][0]['dimensions']['credential_id'] == '_legacy'
        assert main(['router', 'usage', 'query', *common, '--granularity', 'cumulative', '--group-by', '["credential_id"]', '--json']) == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope['data']['groups'][0]['dimensions']['credential_id'] == '<redacted>'
        assert envelope['data']['measured_input'] == 10**16+7
        result = cli.dispatch_usage(['query', *common, '--granularity', 'cumulative', '--require-complete'])
        assert result.error.code == 'usage_coverage_unavailable'
        assert 'owner_roster_unknown' in result.error.details['coverage']['gap_reasons']
        monkeypatch.setenv('SYNTHETIC_USAGE', 'synthetic-infer-token')
        assert cli.dispatch_usage(['query', *common, '--granularity', 'cumulative']).error.code == 'router_access_denied'


def test_typed_query_wire_and_active_invalid_arguments(monkeypatch):
    calls = []
    monkeypatch.setenv('SYNTHETIC_USAGE', ADMIN)
    def fetch(base, path, token, timeout, opener, **kwargs):
        calls.append(parse_qs(urlsplit(path).query))
        return {'schema': 'router-usage/v1'}
    monkeypatch.setattr(cli, '_fetch', fetch)
    args = ['query', '--auth-env', 'SYNTHETIC_USAGE', '--granularity', 'cumulative',
            '--filters', '[["actor_id",null],["input_partial",false],["binding_revision",7]]', '--group-by', '["model"]']
    assert cli.dispatch_usage(args).error is None
    assert json.loads(calls[0]['filters'][0]) == [['actor_id', None], ['binding_revision', 7], ['input_partial', False]]
    assert json.loads(calls[0]['group_by'][0]) == ['model']
    for tail in [['--group-by', '[]'], ['--from-utc', AT], ['--cursor', 'abc'], ['--require-complete']]:
        assert cli.dispatch_usage(['active', '--auth-env', 'SYNTHETIC_USAGE', *tail]).error is not None
    assert len(calls) == 1


def test_protected_autoload_file_no_shared_fallback(tmp_path, monkeypatch):
    config = tmp_path / 'router-diagnostics.toml'
    secret = tmp_path / 'usage.token'
    secret.write_text(ADMIN + '\n'); secret.chmod(0o600)
    config.write_text('router_url="https://example.test"\ncredential_file="usage.token"\n')
    monkeypatch.setattr(cli, 'config_path', lambda name: str(config))
    monkeypatch.delenv('ANVIL_ROUTER_TOKEN', raising=False)
    captured = []
    monkeypatch.setattr(cli, '_fetch', lambda base, path, token, *a, **k: captured.append(token) or {'schema':'router-active-usage/v1'})
    assert cli.dispatch_usage(['active']).error is None and captured == [ADMIN]
    if os.name == 'nt':
        from tests.bootstrap_windows_fixtures import WindowsFixtureTree
        tree = WindowsFixtureTree(tmp_path)
        tree.owner_readonly_with_everyone_write(secret)
        try:
            assert cli.dispatch_usage(['active']).error.code == 'router_diagnostics_config_invalid'
        finally:
            tree.restore_full_control(secret)
    else:
        secret.chmod(0o644)
        assert cli.dispatch_usage(['active']).error.code == 'router_diagnostics_config_invalid'
        secret.chmod(0o600)
    target=tmp_path/'private-token'; secret.rename(target); secret.symlink_to(target)
    assert cli.dispatch_usage(['active']).error.code == 'router_diagnostics_config_invalid'
    config.write_text('credential_file=' + json.dumps(os.path.expanduser('~/.env')) + '\n')
    assert cli.dispatch_usage(['active']).error.code == 'router_diagnostics_config_invalid'


def test_container_inspect_is_narrow_and_existing_operations_remain_pipe_owned(monkeypatch):
    calls = []
    row = {'Id':'a'*64, 'State':{'Running':True}, 'Config':{'Labels':{
        'com.docker.compose.project':'anvil-serving','com.docker.compose.service':'router'}},
        'Mounts':[{'Destination':'/private','Type':'volume','RW':True}]}
    monkeypatch.setattr(key_container.subprocess, 'run', lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0, stdout=json.dumps(row)))
    assert key_container._container_id('router', '/private/keys.sqlite3') == 'a'*64
    template = calls[0][3]
    assert '{{json .}}' not in template and '.Env' not in template and '.Cmd' not in template
    assert '.Config.Labels' in template and '.Mounts' in template


def test_real_active_and_recent_preserve_closed_samples(policy, store, monkeypatch, capsys):
    from anvil_serving.router.workloads import RouterWorkloadRegistry
    from anvil_serving.router.decision_log import DecisionLog
    from anvil_serving.router.internal import UsageInvocation
    from tests.router.test_usage_retention import forwarded_caller
    from tests.router.test_usage_admin import CLOCK
    usage, _, run, scope = store
    monkeypatch.setattr('anvil_serving.router.usage_store._now', lambda: END)
    registry = RouterWorkloadRegistry(DecisionLog(), clock=CLOCK)
    invocation = UsageInvocation(usage, run, scope, forwarded_caller(), 'chat', 'llm.primary',
                                 registry=registry, clock=CLOCK, gateway_request_id='req_'+ident().replace('-',''))
    monkeypatch.setenv('SYNTHETIC_USAGE', ADMIN)
    with server(policy, usage_store=usage, usage_authority=lambda: scope, workload_registry=registry) as address:
        common = ['--router-url', f'http://127.0.0.1:{address[1]}', '--auth-env', 'SYNTHETIC_USAGE']
        assert main(['router', 'usage', 'active', *common]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data['schema'] == 'router-active-usage/v1'
        assert data['records'][0]['caller'] == invocation.start.caller.to_dict()
        assert data['records'][0]['accounting_status'] == 'in_progress'
        assert data['records'][0]['last_activity_ms'] is None
    invocation.finish('success')
    reads=[]
    monkeypatch.setattr(cli, '_fetch', lambda base,path,*a,**k: reads.append(parse_qs(urlsplit(path).query)) or {'schema':'router-usage/v1'})
    assert cli.dispatch_usage(['recent', '--auth-env', 'SYNTHETIC_USAGE']).error is None
    from datetime import datetime, timedelta
    lower, upper = (datetime.fromisoformat(reads[0][k][0].replace('Z','+00:00')) for k in ('from_utc','to_utc'))
    assert upper-lower == timedelta(hours=24) and reads[0]['granularity'] == ['detail']


def test_real_redirect_never_forwards_scoped_credential(monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    hits=[]
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302); self.send_header('Location','/stolen'); self.end_headers()
    httpd=HTTPServer(('127.0.0.1',0),Redirect)
    worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
    monkeypatch.setenv('SYNTHETIC_USAGE',ADMIN)
    try:
        result=cli.dispatch_usage(['active','--router-url',f'http://127.0.0.1:{httpd.server_port}', '--auth-env','SYNTHETIC_USAGE'])
        assert result.error.code == 'router_http_error' and len(hits) == 1
        assert ADMIN not in result.human_stderr
    finally:
        httpd.shutdown();httpd.server_close();worker.join(5)


def test_container_binding_and_snapshot_commands_use_actual_pipe(tmp_path, monkeypatch, capsys):
    import subprocess
    import sys
    store, common = local(tmp_path)
    metadata, _ = store.create('fixture', ['llm.primary'], ['/v1/chat/completions'])
    # Docker paths are POSIX even when the real pipe child runs on Windows.
    (tmp_path/'router.toml').write_text('[server]\nauth_env="SYNTHETIC_MASTER"\n'
        'api_keys_path="/fixture/private/keys.sqlite3"\n')
    backup=tmp_path/'snapshots'/'backup.sqlite3'; restored=tmp_path/'restore'/'keys.sqlite3'
    native_paths={'/fixture/private/keys.sqlite3':str(store.path),
        '/fixture/snapshots/backup.sqlite3':str(backup),'/fixture/restore/keys.sqlite3':str(restored)}
    calls=[]; original=subprocess.run
    row={'Id':'a'*64,'State':{'Running':True},'Config':{'Labels':{
        'com.docker.compose.project':'anvil-serving','com.docker.compose.service':'router'}},
        'Mounts':[{'Destination':'/fixture','Type':'volume','RW':True}]}
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1]=='inspect':
            return SimpleNamespace(returncode=0,stdout=json.dumps(row))
        assert argv[:4]==['docker','exec','-i','a'*64]
        assert len(kwargs['input'])<=16_384
        payload=json.loads(kwargs['input'])
        for field in ('store_path','out','snapshot'):
            if payload.get(field) is not None:
                assert payload[field] in native_paths
                payload[field]=native_paths[payload[field]]
        return original([sys.executable,'-m',key_container.__name__],
                        **{**kwargs,'input':json.dumps(payload)})
    monkeypatch.setattr(key_container.subprocess,'run',run)
    options=[*common,'--container','synthetic-router']
    binding=['bind',*options,'--key-id',metadata['key_id'],'--kind','service',
             '--owner-id','service_fixture','--expected-revision','0']
    assert keys.dispatch([*binding,'--dry-run'])==0
    assert keys.dispatch(binding)==0
    assert keys.dispatch(['backup',*options,'--out','/fixture/snapshots/backup.sqlite3'])==0
    assert keys.dispatch(['restore',*options,'--snapshot','/fixture/snapshots/backup.sqlite3',
                          '--out','/fixture/restore/keys.sqlite3'])==0
    assert keys.KeyStore(restored).list_keys()==store.list_keys()
    assert keys.dispatch(['restore',*options,'--snapshot','/fixture/snapshots/backup.sqlite3',
                          '--out','/fixture/restore/keys.sqlite3'])==2
    assert 'service_fixture' in capsys.readouterr().out


@pytest.mark.parametrize("status", [400, 422, 503])
def test_stalled_error_body_is_closed_typed_and_secret_free(monkeypatch, capsys, status):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    import urllib.error
    release = threading.Event()
    closed = []
    original_close = urllib.error.HTTPError.close
    def close(response):
        closed.append(response.code)
        return original_close(response)
    monkeypatch.setattr(urllib.error.HTTPError, "close", close)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(status); self.send_header("Content-Length", "100")
            self.end_headers(); self.wfile.flush(); release.wait(2)
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=httpd.serve_forever); worker.start()
    monkeypatch.setenv("SYNTHETIC_USAGE", ADMIN)
    try:
        result = main(["router", "usage", "active", "--router-url", f"http://127.0.0.1:{httpd.server_port}",
                       "--auth-env", "SYNTHETIC_USAGE", "--timeout", "0.05"])
        assert result != 0 and status in closed
        output = capsys.readouterr()
        assert json.loads(output.err)["error"]["type"] == "router_unreachable"
        assert ADMIN not in output.out + output.err
    finally:
        release.set(); httpd.shutdown(); httpd.server_close(); worker.join(3)
        assert not worker.is_alive()


@pytest.mark.parametrize("raw", [b"invalid JSON", b'{"error":{"type":[]}}', b"["*1200])
def test_malformed_error_decoder_is_closed_and_typed(raw):
    import io
    import urllib.error
    from anvil_serving.operator_output import TransportError
    body = io.BytesIO(raw)
    def opener(*args, **kwargs):
        raise urllib.error.HTTPError("http://127.0.0.1", 503, "unavailable", {}, body)
    with pytest.raises(TransportError) as caught:
        cli._fetch("http://127.0.0.1", "/v1/admin/usage", ADMIN, 1, opener, usage=True)
    assert caught.value.code == "router_response_invalid" and body.closed


@pytest.mark.parametrize("status", [200, 422])
@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity", b"1e400"])
def test_nonfinite_http_json_is_typed_at_shared_boundary(monkeypatch, capsys, status, number):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    body = (b'{"schema":"router-active-usage/v1","records":[{"elapsed_ms":' + number + b'}]}'
            if status == 200 else b'{"error":{"type":"usage_coverage_unavailable"},"coverage":{"available":false,"limitations":["usage_coverage_unavailable"],"available_granularities":["detail"],"snapshot_revision":' + number + b'}}')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(status); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=httpd.serve_forever); worker.start()
    monkeypatch.setenv("SYNTHETIC_USAGE", ADMIN)
    try:
        rc = main(["router", "usage", "active", "--router-url", f"http://127.0.0.1:{httpd.server_port}", "--auth-env", "SYNTHETIC_USAGE"])
        assert rc != 0
        output = capsys.readouterr()
        assert json.loads(output.err)["error"]["type"] == "router_response_invalid"
        assert ADMIN not in output.out + output.err
    finally:
        httpd.shutdown(); httpd.server_close(); worker.join(3)
        assert not worker.is_alive()


def test_strict_shared_decoder_preserves_finite_values_exact_ints_and_error_format(monkeypatch):
    import io
    from anvil_serving.operator_output import OperatorError
    raw = b'{"schema":"router-active-usage/v1","records":[{"elapsed_ms":0.125,"count":10000000000000007,"missing":null}]}'
    class Response(io.BytesIO):
        status = 200
    assert cli._fetch("http://127.0.0.1", "/v1/admin/usage", ADMIN, 1, lambda *a,**k: Response(raw), usage=True)["records"] == [
        {"elapsed_ms":0.125,"count":10**16+7,"missing":None}]
    monkeypatch.setenv("SYNTHETIC_USAGE", ADMIN)
    def failed(*a, **k):
        raise OperatorError("fixed", code="usage_coverage_unavailable", details={"coverage":{"value":float("inf")}})
    monkeypatch.setattr(cli, "_fetch", failed)
    result = cli.dispatch_usage(["active", "--auth-env", "SYNTHETIC_USAGE"])
    assert result.error.code == "router_response_invalid"
    assert json.loads(result.human_stderr)["error"]["type"] == "router_response_invalid"
