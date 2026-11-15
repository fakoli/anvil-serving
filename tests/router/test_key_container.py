"""Container-owned key operations: durable mounts, pipe secrecy, and delivery."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from anvil_serving.router import key_container, keys
from tests.router.key_fixtures import tmp_path as tmp_path


@pytest.fixture
def container(tmp_path, monkeypatch):
    config = tmp_path / "router.toml"
    config.write_text('[server]\nauth_env="TEST_MASTER"\napi_keys_path="/var/lib/anvil-serving/router-keys/keys.sqlite3"\n')
    row = {"Id": "a" * 64, "State": {"Running": True}, "Config": {"Labels": {
        "com.docker.compose.project": "anvil-serving", "com.docker.compose.service": "router",
    }}, "Mounts": [{"Destination": "/var/lib/anvil-serving/router-keys", "Type": "volume", "RW": True}]}
    calls = []
    original = subprocess.run

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout=json.dumps(row))
        assert argv == ["docker", "exec", "-i", "a" * 64, "python", "-m", key_container.__name__]
        payload = json.loads(kwargs["input"])
        payload["store_path"] = str(tmp_path / "store" / "keys.sqlite3")
        return original([sys.executable, "-m", key_container.__name__],
                        input=json.dumps(payload), capture_output=True, text=True, timeout=30)

    monkeypatch.setattr(key_container.subprocess, "run", run)
    return config, row, calls


def test_container_cli_lifecycle_and_secret_delivery(container, tmp_path, capsys):
    config, _, calls = container
    common = ["--container", "anvil-router", "--config", str(config)]
    assert keys.dispatch(["init", *common]) == 0
    output = tmp_path / "secrets" / "device.key"
    assert keys.dispatch(["create", *common, "--name", "laptop", "--model", "llm.primary",
                          "--path", "/v1/chat/completions", "--out", str(output)]) == 0
    captured = capsys.readouterr()
    metadata = json.loads(captured.out.splitlines()[-1])
    secret = output.read_text().strip()
    assert secret.startswith("ask_") and secret not in captured.out + captured.err
    assert secret not in json.dumps(calls) and str(output) not in json.dumps(calls)
    assert keys.dispatch(["list", *common]) == 0
    assert keys.dispatch(["usage", *common, "--key-id", metadata["key_id"]]) == 0
    assert keys.dispatch(["revoke", *common, "--key-id", metadata["key_id"]]) == 0
    assert keys.KeyStore(tmp_path / "store" / "keys.sqlite3").authenticate(secret) is None


def test_memory_key_requires_the_complete_bank_grant(tmp_path, monkeypatch, capsys):
    import io
    from anvil_serving import memory_access
    from anvil_serving.router import config, connect_keys
    path = tmp_path / 'store' / 'keys.sqlite3'
    store = keys.KeyStore.initialize(path)
    connect_keys.migrate(store)
    grant = {'default_bank': 'personal', 'admin': False, 'banks': {'personal': ['recall', 'retain']}}
    monkeypatch.setattr(config, 'load_server_config', lambda *a, **kw: SimpleNamespace(
        api_keys_path=str(path), connect_check_env='SYNTHETIC_CHECK', connect_home_url='https://home.example.test'))
    monkeypatch.setattr(config, 'load', lambda *_: SimpleNamespace(memory_routes=[SimpleNamespace(alias='memory.user', access_file='/policy')]))
    monkeypatch.setattr(memory_access, 'resolve', lambda *_: ('personal', grant))
    monkeypatch.setenv('SYNTHETIC_CHECK', 'synthetic-check-only')
    monkeypatch.setattr(connect_keys, 'principal_checker', lambda *_: lambda *a: True)
    payload = {'action': 'memory-key', 'store_path': str(path), 'principal': 'human:'+'a'*64,
        'generation': '1', 'epoch': 'f'*64, 'grant': {**grant, 'admin': True},
        'secret': 'ask_'+'a'*43, 'expected_key_id': None, 'apply': True}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert key_container.main() == 2
    assert not store.list_keys()
    assert payload['secret'] not in capsys.readouterr().err
    payload['grant'] = grant
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert key_container.main() == 0
    captured = capsys.readouterr()
    assert payload['secret'] not in captured.out + captured.err
    assert len(store.list_keys()) == 1
