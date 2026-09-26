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


@pytest.mark.parametrize("bad", ["readonly", "file", "ephemeral", "foreign", "stopped", "shadowed"])
def test_container_rejects_unowned_or_incomplete_storage(container, bad):
    _, row, calls = container
    if bad == "readonly": row["Mounts"][0]["RW"] = False
    if bad == "file": row["Mounts"][0]["Destination"] = "/var/lib/anvil-serving/router-keys/keys.sqlite3"
    if bad == "ephemeral": row["Mounts"] = []
    if bad == "foreign": row["Config"]["Labels"]["com.docker.compose.service"] = "other"
    if bad == "stopped": row["State"]["Running"] = False
    if bad == "shadowed": row["Mounts"].append({"Destination": "/var/lib/anvil-serving/router-keys", "Type": "tmpfs", "RW": True})
    with pytest.raises(keys.KeyStoreError):
        key_container._container_id("anvil-router", "/var/lib/anvil-serving/router-keys/keys.sqlite3")
    assert all(argv[1] == "inspect" for argv, _ in calls)


def test_failed_host_delivery_revokes_container_key(container, tmp_path, monkeypatch):
    config, _, _ = container
    common = ["--container", "anvil-router", "--config", str(config)]
    assert keys.dispatch(["init", *common]) == 0
    monkeypatch.setattr(key_container, "_write_secret", lambda *args: (_ for _ in ()).throw(OSError()))
    assert keys.dispatch(["create", *common, "--name", "device", "--model", "llm.primary",
                          "--path", "/v1/chat/completions", "--out", str(tmp_path / "key")]) == 2
    assert keys.KeyStore(tmp_path / "store" / "keys.sqlite3").list_keys()[0]["revoked_at"] is not None


def test_standard_compose_covers_documented_store():
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[2]
    for relative in ("examples/primary-node/docker-compose.yml", "anvil_serving/_scaffold_templates/docker-compose.yml"):
        compose = yaml.safe_load((root / relative).read_text())
        assert "anvil-router-keys:/var/lib/anvil-serving/router-keys" in compose["services"]["router"]["volumes"]
        assert "anvil-router-keys" in compose["volumes"]

    dockerfile = (root / "Dockerfile").read_text()
    assert "mkdir -p /etc/anvil /var/lib/anvil-serving/router-keys" in dockerfile
    assert "chmod 700 /var/lib/anvil-serving/router-keys" in dockerfile
    assert "chown -R anvil:anvil /etc/anvil /var/lib/anvil-serving" in dockerfile


def test_failed_delivery_and_revoke_reports_recoverable_public_id(container, tmp_path, monkeypatch, capsys):
    config, _, _ = container
    common = ["--container", "anvil-router", "--config", str(config)]
    assert keys.dispatch(["init", *common]) == 0
    capsys.readouterr()
    original = key_container._invoke

    def invoke(container_id, payload):
        if payload["action"] == "revoke":
            raise keys.KeyStoreError("unavailable")
        return original(container_id, payload)

    monkeypatch.setattr(key_container, "_invoke", invoke)
    monkeypatch.setattr(key_container, "_write_secret", lambda *args: (_ for _ in ()).throw(OSError()))
    assert keys.dispatch(["create", *common, "--name", "device", "--model", "llm.primary",
                          "--path", "/v1/chat/completions", "--out", str(tmp_path / "key")]) == 2
    output = capsys.readouterr()
    recovery = json.loads(output.out)
    assert recovery["cleanup_required"] is True
    assert recovery["key_id"] == keys.KeyStore(tmp_path / "store" / "keys.sqlite3").list_keys()[0]["key_id"]
    assert "ask_" not in output.out + output.err
