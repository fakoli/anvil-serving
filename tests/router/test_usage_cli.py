"""Supported operator accounting commands against real protected storage/HTTP."""
import json
import os

import pytest

from anvil_serving.cli import main
from anvil_serving import router_diagnostics as cli
from anvil_serving.router import keys
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
    worker=threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True);worker.start()
    monkeypatch.setenv('SYNTHETIC_USAGE',ADMIN)
    try:
        result=cli.dispatch_usage(['active','--router-url',f'http://127.0.0.1:{httpd.server_port}', '--auth-env','SYNTHETIC_USAGE'])
        assert result.error.code == 'router_http_error' and len(hits) == 1
        assert ADMIN not in result.human_stderr
    finally:
        httpd.shutdown();httpd.server_close();worker.join(5)


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
    worker = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}); worker.start()
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
