from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os

import pytest

from anvil_serving.router import keys
from anvil_serving.router.keys import KeyStore, KeyStoreError, dispatch


def _store(tmp_path):
    return KeyStore.initialize(tmp_path / "keys.sqlite3")


def _key(store, **kwargs):
    return store.create("phone", ["LLM.Primary"], ["/v1/chat/completions"], **kwargs)


def test_authentication_is_digest_only_revocable_and_closed(tmp_path):
    store = _store(tmp_path)
    metadata, secret = _key(store)
    assert secret not in repr(store.list_keys())
    assert secret.encode() not in (tmp_path / "keys.sqlite3").read_bytes()
    principal = store.authenticate(secret)
    assert principal and principal.key_id == metadata["key_id"]
    assert principal.allows_model("llm.primary")
    assert principal.allows_model("LLM.Primary", normalize=False)
    assert not principal.allows_model("llm.primary", normalize=False)
    assert principal.allows_path("GET", "/v1/models")
    assert not principal.allows_path("POST", "/v1/audio/speech")
    assert store.authenticate("wrong" * 20) is None
    assert store.revoke(metadata["key_id"])
    assert store.authenticate(secret) is None


def test_expiry_and_missing_store_fail_closed(tmp_path):
    with pytest.raises(KeyStoreError):
        KeyStore(tmp_path / "missing.sqlite3")
    store = _store(tmp_path)
    metadata, secret = _key(store, expires_days=1)
    with store._connect() as connection:
        connection.execute("UPDATE keys SET expires_at = 0 WHERE key_id = ?", (metadata["key_id"],))
    assert store.authenticate(secret) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and symlink contract")
def test_store_refuses_unsafe_permissions_and_symlinks(tmp_path):
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o700)
    unsafe.chmod(0o755)
    with pytest.raises(KeyStoreError, match="owner-only"):
        KeyStore.initialize(unsafe / "keys.sqlite3")
    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    target = safe / "target.sqlite3"
    target.write_bytes(b"not a database")
    target.chmod(0o600)
    link = safe / "keys.sqlite3"
    link.symlink_to(target)
    with pytest.raises(KeyStoreError, match="unsafe"):
        KeyStore(link)


def test_store_path_is_absolute_and_model_grants_are_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = KeyStore.initialize("relative.sqlite3")
    assert store.path.is_absolute()
    with pytest.raises(KeyStoreError):
        store.create("phone", ["llm.*"], ["/v1/chat/completions"])
    with pytest.raises(KeyStoreError):
        store.create("phone", ["llm\nprimary"], ["/v1/chat/completions"])


def test_rate_limit_is_atomic_and_persists(tmp_path):
    store = _store(tmp_path)
    metadata, _ = _key(store, rpm=2)
    with ThreadPoolExecutor(max_workers=8) as workers:
        answers = list(workers.map(lambda _: store.admit(metadata["key_id"]), range(8)))
    assert answers.count(0) == 2
    assert all(answer >= 1 for answer in answers if answer)
    assert KeyStore(tmp_path / "keys.sqlite3").admit(metadata["key_id"]) >= 1


def test_rate_limit_clock_bounce_cannot_mint_tokens(tmp_path, monkeypatch):
    store = _store(tmp_path)
    metadata, _ = _key(store, rpm=2)
    with store._connect() as connection:
        connection.execute("INSERT INTO buckets VALUES (?, ?, ?)", (metadata["key_id"], 0.0, 100.0))
    monkeypatch.setattr(keys.time, "time", lambda: 50.0)
    assert store.admit(metadata["key_id"]) == 80
    with store._connect() as connection:
        assert connection.execute("SELECT updated_at FROM buckets").fetchone()[0] == 100.0
    monkeypatch.setattr(keys.time, "time", lambda: 130.0)
    assert store.admit(metadata["key_id"]) == 0


def test_audit_is_bounded_and_supports_legacy_and_unknown_principals(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(keys, "_MAX_AUDIT", 5)
    for number in range(7):
        store.record("_legacy", "req_%d" % number, "POST", "/v1/chat/completions", 200, 1)
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 5
    rows = store.usage("_legacy", 2)
    assert [row["request_id"] for row in rows] == ["req_6", "req_5"]
    store.record(None, None, "ATTACKER\nMETHOD", "/secret?token=bad", 401, 0)
    assert store.usage(None, 1)[0]["key_id"] is None
    assert store.usage(None, 1)[0]["path"] == "[other]"


def test_cli_writes_secret_only_to_exclusive_output(tmp_path, capsys):
    config = tmp_path / "router.toml"
    database = tmp_path / "keys.sqlite3"
    config.write_text("[server]\nauth_env = 'TEST_ROUTER_TOKEN'\napi_keys_path = %s\n" % json.dumps(str(database)), encoding="utf-8")
    assert dispatch(["init", "--config", str(config)]) == 0
    output = tmp_path / "device.json"
    assert dispatch(["create", "--config", str(config), "--name", "phone", "--model", "llm.primary", "--path", "/v1/chat/completions", "--out", str(output)]) == 0
    public = capsys.readouterr().out
    delivered = output.read_text().strip()
    assert delivered not in public
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0
    assert dispatch(["create", "--config", str(config), "--name", "phone", "--model", "llm.primary", "--path", "/v1/chat/completions", "--out", str(output)]) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX open-file replacement semantics")
def test_failed_secret_write_never_removes_a_replaced_output(tmp_path, monkeypatch):
    output = tmp_path / "device.key"

    class ReplacingWriter:
        def __enter__(self):
            return self

        def write(self, _value):
            output.unlink()
            output.write_text("competing\n", encoding="utf-8")
            raise OSError("write failed")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(keys.os, "fdopen", lambda *_args, **_kwargs: ReplacingWriter())
    with pytest.raises(OSError):
        keys._write_secret(str(output), "secret")
    assert output.read_text(encoding="utf-8") == "competing\n"


def test_cli_unknown_revocation_fails_but_known_revocation_is_idempotent(tmp_path, capsys):
    config = tmp_path / "router.toml"
    config.write_text("[server]\nauth_env='TEST_MASTER'\napi_keys_path=%s\n" % json.dumps(str(tmp_path / "keys.sqlite3")))
    store = _store(tmp_path)
    meta, _ = _key(store)
    assert dispatch(["revoke", "--config", str(config), "--key-id", "key_missing"]) == 2
    for _ in range(2):
        assert dispatch(["revoke", "--config", str(config), "--key-id", meta["key_id"]]) == 0
