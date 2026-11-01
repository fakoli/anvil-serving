"""Synthetic exact retained queries and loss-safe bounded pruning."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3

import pytest

from anvil_serving.router import usage_store as ledger
from anvil_serving.router.identity import (Actor, CallerSnapshot, EffectiveGrant, EndUser, connect_reference,
                                           legacy_caller, scope_digest)
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.usage_store import AuthorityScope, RequestStart, RunOwner, UsageError, UsageQuery, UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_usage_lifecycle import ident, terminal, tokens

ORIGIN = "2025-01-01T00:00:00.000000Z"
NOW = "2026-10-07T12:00:00.000000Z"
DOMAIN = "domain_fixture"


@pytest.fixture
def store(tmp_path, monkeypatch):
    usage = UsageStore(KeyStore.initialize(tmp_path / "private" / "keys.sqlite3"))
    usage.migrate()
    owner = RunOwner("host_fixture", ident(), 1, 2, 1, 2, 1000, 1, 3, 123, 456)
    clock = [ORIGIN]
    monkeypatch.setattr(ledger, "_linux_owner", lambda *args: (owner, ()))
    monkeypatch.setattr(ledger, "_now", lambda: clock[0])
    run = usage.register_run(owner, domain_id=DOMAIN, configuration_revision="config_fixture", enabled=True)
    scope = AuthorityScope(DOMAIN, "config_fixture", (run,), ORIGIN, "2027-01-01T00:00:00.000000Z")
    clock[0] = NOW
    return usage, run, scope, clock


def put(store, at="2026-10-07T11:00:00.000000Z", *, caller=None, usage=None, model="llm.primary", unresolved=False):
    db, run, scope, clock = store
    clock[0] = at
    start = RequestStart(ident(), run, at, caller or legacy_caller(), "chat", model, attempt_id=ident(),
                         input_applicability=usage.input.applicability if usage else "applicable",
                         output_applicability=usage.output.applicability if usage else "applicable")
    db.start(start, authority_scope=scope)
    ended = (datetime.fromisoformat(at[:-1]) + timedelta(seconds=1)).isoformat(timespec="microseconds") + "Z"
    final = terminal(start, ended_at=ended, tokens=usage or tokens())
    if not unresolved:
        db.finalize(final)
    clock[0] = NOW
    return start, final


def query(store, granularity="cumulative", *, scope=True, **options):
    db, _, authority, _ = store
    return db.query(UsageQuery(granularity=granularity, **options), domain_id=DOMAIN,
                    authority_scope=authority if scope else None)


def full_day(day):
    start = datetime.fromisoformat(day)
    return dict(from_utc=start.isoformat(timespec="microseconds") + "Z",
                to_utc=(start + timedelta(days=1)).isoformat(timespec="microseconds") + "Z")


def connect_caller(generation="1", epoch="a" * 64, revision=1):
    owner = "human:" + "b" * 64
    grant = EffectiveGrant("connect", reference=connect_reference(owner, generation, epoch, revision), revision=revision,
                           models=("llm.primary",), paths=("/v1/models", "/v1/chat/completions"), rpm=10, created_at=1,
                           expires_at=86401, owner=owner, generation=generation, epoch=epoch, approval_revision=revision,
                           account_models=("llm.primary",), account_paths=("/v1/models", "/v1/chat/completions"),
                           account_rpm=10, account_expires_days=1)
    return CallerSnapshot("key_fixture", Actor("human", owner, revision, generation, epoch), grant, "owned_human")


def forwarded_caller():
    grant = EffectiveGrant("configured_scope", reference="service_fixture", client_id="service_fixture",
                           scopes=("workloads:read",), policy_digest=scope_digest("service_fixture", ("workloads:read",)))
    return CallerSnapshot("service_fixture", Actor("service", "service_fixture"), grant, "verified_forwarded",
                          end_user=EndUser("webui_fixture", "open-webui", "subject_fixture"))


@pytest.mark.parametrize("failure", ["DELETE FROM usage_details", "DELETE FROM usage_starts", "DELETE FROM usage_daily",
                                     "UPDATE usage_domains SET detail_floor_utc", "COMMIT"])
def test_prune_atomic_on_each_failure(store, monkeypatch, failure):
    db, _, _, _ = store
    put(store, "2025-10-06T12:00:00.000000Z")
    original = db.key_store._connect
    before = query(store)

    class Fail:
        def __init__(self, conn): object.__setattr__(self, "conn", conn)
        def __getattr__(self, name): return getattr(self.conn, name)
        def __setattr__(self, name, value): setattr(self.conn, name, value)
        def execute(self, sql, *args):
            if sql.startswith(failure): raise sqlite3.OperationalError("synthetic private failure")
            return self.conn.execute(sql, *args)

    @contextmanager
    def failing():
        with original() as conn: yield Fail(conn)

    monkeypatch.setattr(db.key_store, "_connect", failing)
    with pytest.raises(UsageError, match="accounting_unavailable"):
        db.prune(NOW, domain_id=DOMAIN)
    monkeypatch.setattr(db.key_store, "_connect", original)
    after = query(store)
    assert after["measured_input"] == before["measured_input"] == 7
    assert after["snapshot_revision"] == before["snapshot_revision"]
    assert after["retained_scope"]["detail_floor_utc"] is None
    with original() as conn:
        assert [conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("usage_starts", "usage_details", "usage_daily", "usage_cumulative")] == [1, 1, 1, 1]


def test_coverage_enable_disable_partial_day_rollback_overlapping_run_and_unknown(store):
    db, run, scope, _ = store
    put(store)
    db.coverage_transition(run, False, "config_fixture", at="2026-10-07T11:10:00Z")
    db.coverage_transition(run, True, "config_fixture", at="2026-10-07T11:20:00Z", authority_scope=scope)
    result = query(store, "daily", **full_day("2026-10-07"))
    assert result["measured_input"] == 7 and not result["coverage_complete"]
    assert any(g["reason"] == "accounting_disabled" for g in result["coverage_gaps"])
    assert any(s["enabled"] is False for s in result["coverage_segments"])
    with pytest.raises(UsageError, match="usage_coverage_unavailable"):
        query(store, "daily", require_complete=True, **full_day("2026-10-07"))
    # A query solely inside an enabled historical interval may be complete.
    exact = query(store, "detail", from_utc="2026-10-07T11:00:00Z", to_utc="2026-10-07T11:05:00Z", require_complete=True)
    assert exact["coverage_complete"] and exact["measured_input"] == 7
    owner = ledger.RunOwner.observe("host_fixture")
    other = db.register_run(owner, domain_id=DOMAIN, configuration_revision="config_fixture", enabled=False,
                            started_at="2026-10-07T11:30:00Z")
    overlapped = db.query(UsageQuery("cumulative"), domain_id=DOMAIN, authority_scope=replace(scope, run_ids=(run, other)))
    assert not overlapped["coverage_complete"] and overlapped["measured_input"] == 7
    assert not query(store)["coverage_complete"]  # stale trusted roster cannot hide other run
    db.recover((other,), host_domain_id="different_fixture")
    assert db.health(DOMAIN)["unknown_runs"] == 1
    unknown = query(store, scope=False)
    assert not unknown["coverage_complete"] and unknown["measured_input"] == 7
    empty = query(store, scope=False, filters=(("model", "no_match"),))
    assert empty["groups"] == [] and empty["measured_input"] is None


@pytest.mark.parametrize("grouped", [False, True])
def test_page_cursor_binds_revision_query_domain_authority_and_complete_groups(store, grouped):
    for index in range(6): put(store, model="model_" + str(index // 2))
    options = dict(group_by=("model",)) if grouped else dict(granularity="detail", from_utc="2026-10-07T00:00:00Z", to_utc=NOW)
    result = query(store, limit=1, **options)
    assert result["truncation"]["omitted"] and result["requests"] == (2 if grouped else 1)
    all_items, cursor = [], None
    while True:
        page = query(store, limit=1, cursor=cursor, **options)
        all_items.extend(page["groups"] if grouped else page["records"])
        cursor = page["truncation"]["next_cursor"]
        if cursor is None: break
    assert len(all_items) == (3 if grouped else 6)
    assert len({json.dumps(x, sort_keys=True) for x in all_items}) == len(all_items)
    cursor = result["truncation"]["next_cursor"]
    for altered in ({"limit": 2}, {"filters": (("model", "model_0"),)}, {"require_complete": True}):
        with pytest.raises(UsageError, match="usage_cursor_stale"):
            query(store, cursor=cursor, **{**options, "limit": 1, **altered})
    put(store)
    with pytest.raises(UsageError, match="usage_cursor_stale"):
        query(store, cursor=cursor, limit=1, **options)
