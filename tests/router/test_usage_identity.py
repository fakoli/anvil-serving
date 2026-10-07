"""Synthetic admission snapshots, authority races and closed privacy projection."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import json
import secrets
import sqlite3
from threading import Event
import time

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


def test_binding_cas_and_immutable_history_without_labels(tmp_path):
    store, key_id, token = ordinary(tmp_path)
    before = store.authenticate(token)
    assert before.caller_snapshot.attribution_state == "unbound"
    human = store.bind_owner(key_id, "human", "human:synthetic", 0)
    principal = store.authenticate(token)
    admitted = store.admit(principal, CHAT, "LLM.Primary").caller_snapshot
    saved = admitted.to_json()
    assert admitted.actor == human and admitted.credential_id == key_id
    assert token not in saved and "display label" not in saved
    with pytest.raises(FrozenInstanceError): admitted.actor.id = "other"
    service = store.bind_owner(key_id, "service", "service:synthetic", 1)
    assert service.binding_revision == 2
    assert CallerSnapshot.from_json(saved) == admitted
    assert store.authenticate(token).caller_snapshot.actor == service
    with pytest.raises(keys.KeyStoreError, match="admission_policy_changed"):
        store.bind_owner(key_id, "human", "stale", 1)
    assert store.authenticate(token).caller_snapshot.actor == service
    assert store.revoke(key_id)
    assert store.authenticate(token) is None and admitted.to_json() == saved


def test_two_binding_writers_have_one_cas_winner(tmp_path):
    store, key_id, _ = ordinary(tmp_path)
    def bind(owner):
        try: return store.bind_owner(key_id, "human", owner, 0)
        except keys.KeyStoreError: return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(bind, ["human:first", "human:second"]))
    assert sum(r is not None for r in results) == 1


@pytest.mark.parametrize("kind,owner,revision", [("label", "name", 0), ("human", "https://example.test", 0),
    ("service", "x" * 129, 0), ("human", "valid", True), ("human", "valid", 2**53-1)])
def test_invalid_binding_refuses(tmp_path, kind, owner, revision):
    store, key_id, _ = ordinary(tmp_path)
    with pytest.raises(keys.KeyStoreError): store.bind_owner(key_id, kind, owner, revision)
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM key_owner_bindings").fetchone()[0] == 0


def test_binding_requires_migration_live_ordinary_key(tmp_path):
    store = keys.KeyStore.initialize(tmp_path / "private" / "keys.sqlite3")
    metadata, _ = store.create("label", ["llm.primary"], [CHAT])
    with pytest.raises(keys.KeyStoreError): store.bind_owner(metadata["key_id"], "human", "human:test", 0)
    UsageStore(store).migrate()
    with pytest.raises(keys.KeyStoreError): store.bind_owner("key_missing", "human", "human:test", 0)
    store.revoke(metadata["key_id"])
    with pytest.raises(keys.KeyStoreError): store.bind_owner(metadata["key_id"], "human", "human:test", 0)


@pytest.mark.parametrize("change", ["binding", "models", "paths", "rpm", "expires", "revoked"])
def test_policy_races_refuse_before_any_bucket_write(tmp_path, change):
    store, key_id, token = ordinary(tmp_path)
    principal = store.authenticate(token)
    if change == "binding": store.bind_owner(key_id, "human", "human:changed", 0)
    else:
        field, value = {"models": ("models", '["llm.other"]'), "paths": ("paths", '["/v1/models","/v1/embeddings"]'),
            "rpm": ("rpm", 1), "expires": ("expires_at", 0), "revoked": ("revoked_at", 1)}[change]
        with store._connect() as db: db.execute(f"UPDATE keys SET {field}=? WHERE key_id=?", (value, key_id))
    with pytest.raises(keys.KeyStoreError, match="admission_policy_changed"): store.admit(principal, CHAT, "llm.primary")
    assert buckets(store) == []


@pytest.mark.parametrize("owned", [False, True], ids=["ordinary", "connect"])
@pytest.mark.parametrize("tracked", [False, True], ids=["legacy", "tracked"])
@pytest.mark.parametrize("populated", [False, True], ids=["empty", "existing"])
def test_expiry_during_sqlite_writer_wait_consumes_no_buckets(tmp_path, monkeypatch, owned, tracked, populated):
    if owned:
        store, _, key_id, token, _ = connect(tmp_path)
    else:
        store, key_id, token = ordinary(tmp_path)
    if populated:
        assert store.admit(key_id) == 0
    before = buckets(store)
    expiry = int(time.time()) + 2
    original = store._connect
    with original() as db:
        db.execute("UPDATE keys SET expires_at=? WHERE key_id=?", (expiry, key_id))
    principal = store.authenticate(token, check_owner=False)
    assert principal is not None
    checked = []
    store.owner_check = lambda *owner: checked.append(owner) or True
    attempting = Event()
    attempted_at = []

    @contextmanager
    def observed_connection():
        with original() as db:
            def observe(sql):
                if sql == "BEGIN IMMEDIATE":
                    attempted_at.append(time.time())
                    attempting.set()
            db.set_trace_callback(observe)
            yield db

    monkeypatch.setattr(store, "_connect", observed_connection)
    # The real SQLite writer remains held across expiry. The trace callback
    # observes the competing BEGIN; it never replaces SQLite or the clock.
    with original() as writer, ThreadPoolExecutor(max_workers=1) as pool:
        writer.execute("BEGIN IMMEDIATE")
        time.sleep(max(0, expiry - 0.25 - time.time()))
        pending = pool.submit(store.admit, principal, CHAT, "llm.primary") if tracked else pool.submit(store.admit, key_id)
        try:
            assert attempting.wait(0.5)
            assert attempted_at[0] < expiry and not pending.done()
            time.sleep(max(0, expiry + 0.1 - time.time()))
        finally:
            writer.execute("COMMIT")
        error = "admission_policy_changed" if tracked else "credential key is unavailable"
        with pytest.raises(keys.KeyStoreError, match=error):
            pending.result(timeout=2)
    assert time.time() >= expiry
    assert buckets(store) == before  # Includes the shared Connect account bucket.
    assert checked == ([(OWNER, "1", EPOCH)] if tracked and owned else [])
    assert store.authenticate(token, check_owner=False) is None


def test_authentication_reads_key_and_binding_in_one_transaction(tmp_path, monkeypatch):
    store, key_id, token = ordinary(tmp_path)
    original = store._caller_candidate
    def inspect_transaction(db, *args):
        assert db.in_transaction
        return original(db, *args)
    monkeypatch.setattr(store, "_caller_candidate", inspect_transaction)
    assert store.authenticate(token).key_id == key_id


def test_connect_snapshot_exact_narrowing_and_rotation(tmp_path):
    store, _, key_id, token, account = connect(tmp_path)
    principal = store.authenticate(token)
    caller = store.admit(principal, CHAT, "llm.primary").caller_snapshot
    grant = caller.grant
    assert principal.owner == (OWNER, "1", EPOCH)
    assert grant.approval_revision == grant.revision == account["revision"]
    assert grant.models == ("llm.primary",) and set(grant.account_models) == {"llm.primary", "llm.heavy"}
    assert grant.rpm == grant.account_rpm == 60 and grant.account_expires_days == 30
    assert grant.expires_at - grant.created_at == 7 * 86400
    assert grant.reference.startswith("connect:") and len(grant.reference) <= 128
    with pytest.raises(keys.KeyStoreError): store.bind_owner(key_id, "service", "service:fake", 0)
    metadata, rotated = store.create("rotation", ["llm.primary"], [CHAT], 60, 7,
                                     owner=(OWNER, "1", EPOCH, account["revision"]))
    rotated_caller = store.admit(store.authenticate(rotated), CHAT, "llm.primary").caller_snapshot
    assert rotated_caller.credential_id == metadata["key_id"] != caller.credential_id
    assert rotated_caller.actor == caller.actor and rotated_caller.grant.reference == caller.grant.reference
    saved = caller.to_json()
    store.revoke(key_id)
    assert CallerSnapshot.from_json(saved) == caller


@pytest.mark.parametrize("change", ["approval", "binding", "generation", "epoch", "denied", "forgotten"])
def test_connect_local_cas_changes_consume_no_tokens(tmp_path, change):
    store, _, key_id, token, _ = connect(tmp_path)
    principal = store.authenticate(token, check_owner=False)
    store.owner_check = lambda *identity: True
    with store._connect() as db:
        if change == "approval": db.execute("UPDATE connect_accounts SET revision=revision+1 WHERE owner=?", (OWNER,))
        elif change == "binding": db.execute("UPDATE connect_key_owners SET revision=revision+1 WHERE key_id=?", (key_id,))
        elif change == "generation": db.execute("UPDATE connect_accounts SET generation='2' WHERE owner=?", (OWNER,))
        elif change == "epoch": db.execute("UPDATE connect_accounts SET epoch=? WHERE owner=?", ("d"*64, OWNER))
        elif change == "denied": db.execute("UPDATE connect_accounts SET status='denied' WHERE owner=?", (OWNER,))
        else: db.execute("DELETE FROM connect_accounts WHERE owner=?", (OWNER,))
    with pytest.raises(keys.KeyStoreError, match="admission_policy_changed"): store.admit(principal, CHAT, "llm.primary")
    assert buckets(store) == []


def test_external_check_holds_no_sqlite_transaction_and_final_cas_wins(tmp_path):
    store, _, key_id, token, _ = connect(tmp_path)
    principal = store.authenticate(token, check_owner=False)
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


def test_connect_checker_denial_and_shared_rpm(tmp_path):
    store, _, _, token, account = connect(tmp_path, rpm=1)
    principal = store.authenticate(token, check_owner=False)
    store.owner_check = lambda *identity: False
    with pytest.raises(keys.KeyStoreError): store.admit(principal, CHAT, "llm.primary")
    assert buckets(store) == []
    store.owner_check = lambda *identity: True
    assert store.admit(principal, CHAT, "llm.primary").caller_snapshot is not None
    _, second = store.create("second", ["llm.primary"], [CHAT], 1, 7, owner=(OWNER,"1",EPOCH,account["revision"]))
    decision = store.admit(store.authenticate(second), CHAT, "llm.primary")
    assert decision.retry_after > 0 and decision.caller_snapshot is None


def test_denied_model_path_and_rate_have_no_snapshot(tmp_path, monkeypatch):
    store, key_id, token = ordinary(tmp_path, rpm=1)
    principal = store.authenticate(token)
    for path, model in [("/v1/embeddings", "llm.primary"), (CHAT, "llm.other")]:
        with pytest.raises(keys.KeyStoreError): store.admit(principal, path, model)
    assert buckets(store) == []
    assert store.admit(principal, CHAT, "llm.primary").retry_after == 0
    decision = store.admit(principal, CHAT, "llm.primary")
    assert decision.retry_after > 0 and decision.caller_snapshot is None
    with pytest.raises(keys.KeyStoreError): store.admit(principal, CHAT, "LLM.Primary", normalize=False)
    assert type(store.admit(key_id)) is int


def test_failed_commit_returns_no_snapshot_and_rolls_back(tmp_path, monkeypatch):
    store, _, token = ordinary(tmp_path)
    principal = store.authenticate(token)
    original = store._connect
    class Connection:
        def __init__(self, db): self.db = db
        def execute(self, sql, *args):
            if sql == "COMMIT": raise sqlite3.OperationalError("injected")
            return self.db.execute(sql, *args)
    @contextmanager
    def failed():
        with original() as db: yield Connection(db)
    monkeypatch.setattr(store, "_connect", failed)
    with pytest.raises(keys.KeyStoreError): store.admit(principal, CHAT, "llm.primary")
    monkeypatch.setattr(store, "_connect", original)
    assert buckets(store) == []


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


@pytest.mark.parametrize("decision", [AuthorizationDecision(False,"authorization_scope_denied"),
    AuthorizationDecision(True,"authorized","bad/url",frozenset([INFERENCE_USE])),
    AuthorizationDecision(True,"authorized","valid",frozenset(["admin:fake"])),
    AuthorizationDecision(True,"authorized","valid",frozenset())])
def test_configured_invalid_decisions_cannot_make_caller(decision):
    with pytest.raises(IdentityError): configured_scope_caller(decision)


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(secret="untrusted"), lambda v: v["actor"].update(name="profile"),
    lambda v: v["grant"].update(credential_env="REF"), lambda v: v.update(end_user={"subject":"fake"}),
    lambda v: v["actor"].update(kind=[]), lambda v: v.update(credential_id="wrong"),
    lambda v: v["actor"].update(binding_revision=True), lambda v: v["grant"].update(rpm=True),
    lambda v: v["grant"].update(models=["*"]), lambda v: v["grant"].update(policy_digest="f"*64)])
def test_closed_metadata_rejects_unknown_and_inconsistent_fields(tmp_path, mutate):
    store, key_id, token = ordinary(tmp_path)
    store.bind_owner(key_id, "human", "human:example", 0)
    value = store.authenticate(token).caller_snapshot.to_dict()
    mutate(value)
    with pytest.raises(IdentityError): CallerSnapshot.from_dict(value)


def test_duplicate_json_and_bound_overflow_refuse(tmp_path):
    raw = legacy_caller().to_json()
    with pytest.raises(IdentityError): CallerSnapshot.from_json(raw.replace('"schema":', '"schema":"router-usage/v1","schema":'))
    with pytest.raises(IdentityError): CallerSnapshot.from_json(" "*16385)
    # Valid individual model lists can still exceed the complete caller cap.
    models = [f"m{i}_" + "x"*120 for i in range(64)]
    portal_store, _, owned_key, _, _ = connect(tmp_path / "other")
    with portal_store._connect() as db:
        db.execute("UPDATE keys SET models=? WHERE key_id=?", (json.dumps(models), owned_key))
        db.execute("UPDATE connect_accounts SET models=? WHERE owner=?", (json.dumps(models), OWNER))
    with portal_store._connect() as db:
        with pytest.raises(IdentityError): portal_store._caller_candidate(db, owned_key, keys.time.time())
