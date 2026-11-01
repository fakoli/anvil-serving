"""Synthetic admission snapshots, authority races and closed privacy projection."""
import json
import secrets

import pytest

from anvil_serving.control_plane.authorization import (
    AuthorizationDecision, INFERENCE_USE, WORKLOADS_READ, check_scope, load_authorization_policy,
)
from anvil_serving.router import keys, connect_keys
from anvil_serving.router.identity import Actor, CallerSnapshot, IdentityError, configured_scope_caller, legacy_caller
from anvil_serving.router.usage_store import UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path

CHAT = "/v1/chat/completions"
OWNER, ADMIN = ("human:" + c * 64 for c in "ab")
EPOCH = "c" * 64


def ordinary(tmp_path, rpm=60):
    store = keys.KeyStore.initialize(tmp_path / "private" / "keys.sqlite3")
    UsageStore(store).migrate()
    metadata, secret = store.create("display label", ["llm.primary"], [CHAT], rpm=rpm)
    return store, metadata["key_id"], secret


def connect(tmp_path, rpm=60):
    store, _, _ = ordinary(tmp_path)
    store.owner_check = lambda *identity: identity in {(OWNER, "1", EPOCH), (ADMIN, "1", EPOCH)}
    portal = connect_keys.ConnectKeys(store, ["llm.primary", "llm.heavy"])
    def call(action, **operation):
        return portal.dispatch({"principal": OWNER, "generation": "1", "epoch": EPOCH,
            "administrator": False, "operation": {"action": action, **operation}})
    call("request")
    pending = call("view")["account"]
    portal.approve((ADMIN, "1", EPOCH), {"owner": OWNER, "revision": pending["revision"], "status": "approved",
        "models": ["llm.primary", "llm.heavy"], "paths": [CHAT, "/v1/embeddings"], "rpm": rpm, "expires_days": 30})
    account = call("view")["account"]
    result = call("create", name="untrusted label", models=["llm.primary"], paths=[CHAT],
                  rpm=rpm, expires_days=7, revision=account["revision"])
    return store, portal, result["key"]["key_id"], result["secret"], account


def buckets(store):
    with store._connect() as db:
        return db.execute("SELECT * FROM buckets ORDER BY key_id").fetchall()


def wide_key(tmp_path, owned, migrated):
    store = keys.KeyStore.initialize(tmp_path / "private" / "keys.sqlite3")
    if migrated:
        UsageStore(store).migrate()
    models = [f"m{i:02d}_" + ("x" if owned else "é") * 124 for i in range(61 if owned else 64)]
    assert all(len(model) == 128 for model in models)
    store.owner_check = lambda *owner: True
    binding = None
    if owned:
        portal = connect_keys.ConnectKeys(store, models)
        who = (OWNER, "1", EPOCH)
        portal.dispatch({"principal": OWNER, "generation": "1", "epoch": EPOCH, "administrator": False,
                         "operation": {"action": "request"}})
        revision = portal.view(who, False)["account"]["revision"]
        portal.approve((ADMIN, "1", EPOCH), {"owner": OWNER, "revision": revision, "status": "approved",
            "models": models, "paths": [CHAT], "rpm": 60, "expires_days": 7})
        binding = (*who, portal.view(who, False)["account"]["revision"])
    metadata, token = store.create("synthetic wide grants", models, [CHAT], expires_days=1, owner=binding)
    return store, metadata, token, models


@pytest.mark.parametrize("owned,migrated,schema", [(False, False, 1), (False, True, 3),
    (True, False, 2), (True, True, 3)], ids=["ordinary-v1", "ordinary-v3", "connect-v2", "connect-v3"])
def test_wide_default_grants_remain_complete_but_tracked_metadata_refuses(tmp_path, owned, migrated, schema):
    store, metadata, token, models = wide_key(tmp_path, owned, migrated)
    with store._connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema
    assert store.list_keys()[0]["models"] == models
    checked = []
    store.owner_check = lambda *owner: checked.append(owner) or True
    principal = store.authenticate(token)
    assert principal is not None and principal.caller_snapshot is None
    assert principal.models == tuple(models) and principal.paths == tuple(metadata["paths"])
    assert all(principal.allows_model(model, normalize=False) for model in models)
    assert principal.allows_path("POST", CHAT) and not principal.allows_model("llm.ungranted")
    assert principal.owner == ((OWNER, "1", EPOCH) if owned else None)
    assert checked == ([(OWNER, "1", EPOCH)] if owned else [])
    assert store.authenticate(token, check_owner=False) == principal
    assert buckets(store) == []
    with pytest.raises(IdentityError, match="^invalid caller policy metadata$"):
        store.authenticate(token, snapshot=True, check_owner=False)
    assert buckets(store) == []
    assert store.admit(principal.key_id) == 0
    assert len(buckets(store)) == (2 if owned else 1)
    if owned:
        store.owner_check = lambda *owner: False
        assert store.authenticate(token) is None


def test_default_principal_requires_explicit_tracked_snapshot(tmp_path):
    store, _, token = ordinary(tmp_path)
    legacy = store.authenticate(token)
    assert legacy.caller_snapshot is None
    with pytest.raises(keys.KeyStoreError, match="admission_policy_changed"):
        store.admit(legacy, CHAT, "llm.primary")
    assert buckets(store) == []
    tracked = store.authenticate(token, snapshot=True)
    assert (legacy.key_id, legacy.models, legacy.paths, legacy.owner) == (
        tracked.key_id, tracked.models, tracked.paths, tracked.owner)
    decision = store.admit(tracked, CHAT, "llm.primary")
    assert decision.retry_after == 0 and decision.caller_snapshot == tracked.caller_snapshot


def test_authentication_reads_key_and_binding_in_one_transaction(tmp_path, monkeypatch):
    store, key_id, token = ordinary(tmp_path)
    original = store._caller_candidate
    def inspect_transaction(db, *args, **kwargs):
        assert db.in_transaction
        return original(db, *args, **kwargs)
    monkeypatch.setattr(store, "_caller_candidate", inspect_transaction)
    assert store.authenticate(token, snapshot=True).key_id == key_id


def test_external_check_holds_no_sqlite_transaction_and_final_cas_wins(tmp_path):
    store, _, key_id, token, _ = connect(tmp_path)
    principal = store.authenticate(token, check_owner=False, snapshot=True)
    checked = []
    def checker(*owner):
        checked.append(owner)
        with store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE keys SET rpm=1 WHERE key_id=?", (key_id,))
            db.execute("COMMIT")
        return True
    store.owner_check = checker
    with pytest.raises(keys.KeyStoreError, match="admission_policy_changed"): store.admit(principal, CHAT, "llm.primary")
    assert checked == [(OWNER, "1", EPOCH)] and buckets(store) == []


def configured(tmp_path, scopes, token, reference="SYNTHETIC_SERVICE"):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"schema_version": 1, "clients": [
        {"id": "service_client", "scopes": scopes, "credential_env": reference}]}))
    policy = load_authorization_policy(path, env={reference: token})
    return configured_scope_caller(check_scope(policy, token, INFERENCE_USE))


def test_configured_actual_decision_digest_excludes_rotated_secret_refs(tmp_path):
    first, second = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    caller = configured(tmp_path, [INFERENCE_USE, WORKLOADS_READ], first)
    rotated = configured(tmp_path, [WORKLOADS_READ, INFERENCE_USE], second, "ROTATED_SERVICE")
    assert caller == rotated
    narrower = configured(tmp_path, [INFERENCE_USE], second)
    assert narrower.grant.policy_digest != caller.grant.policy_digest
    other = configured_scope_caller(AuthorizationDecision(True, "authorized", "different_client", frozenset([INFERENCE_USE])))
    assert other.grant.policy_digest != narrower.grant.policy_digest
    raw = caller.to_json()
    assert all(text not in raw for text in (first, second, "SYNTHETIC_SERVICE", "ROTATED_SERVICE", "credential_env"))
    assert CallerSnapshot.from_json(raw) == caller
    assert legacy_caller().actor == Actor("unattributed")
