"""Synthetic exact retained queries and loss-safe bounded pruning."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3

import pytest

from anvil_serving.router import usage_store as ledger
from anvil_serving.router.decision_log import TokenDirection, TokenUsage
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


def test_day31_and_day366_retained_classes_and_all_dimensions(store):
    db, _, _, _ = store
    starts = [put(store, "2026-09-06T12:00:00.000000Z", caller=connect_caller()),
              put(store, "2026-09-06T13:00:00.000000Z", caller=connect_caller("2", "c" * 64, 2)),
              put(store, "2026-09-06T14:00:00.000000Z", caller=forwarded_caller()),
              put(store, "2025-10-06T12:00:00.000000Z")]
    cumulative = query(store)
    assert cumulative["requests"] == 4 and cumulative["measured_input"] == 28
    prune = db.prune(NOW, domain_id=DOMAIN)
    assert prune["detail_deleted"] == 4 and prune["daily_deleted"] == 1
    assert query(store)["measured_input"] == cumulative["measured_input"]
    for start, final in starts[:3]:
        dimensions, _ = db._contribution(start, final, DOMAIN)
        # Every immutable dimension remains filterable/groupable after detail expires.
        for name in ledger._QUERY_DIMENSIONS:
            value = bool(dimensions[name]) if name.endswith("_partial") else dimensions[name]
            result = query(store, "daily", filters=((name, value),), group_by=(name,), **full_day("2026-09-06"))
            assert any(g["dimensions"][name] == value for g in result["groups"])
            assert result["measured_input"] >= 7
    for options in (full_day("2026-09-06"), {"from_utc": "2026-09-06T12:00:00Z", "to_utc": "2026-09-06T13:00:00Z"}):
        with pytest.raises(UsageError, match="usage_coverage_unavailable") as error:
            query(store, "detail", **options)
        assert error.value.status == 422 and error.value.result["retained_scope"]["detail_floor_utc"]
    with pytest.raises(UsageError, match="usage_coverage_unavailable"):
        query(store, "daily", **full_day("2025-10-06"))
    with pytest.raises(UsageError, match="accounting_start_missing"):
        db.finalize(starts[0][1])
    assert query(store)["requests"] == 4


def test_prune_preserves_unresolved_and_late_finalization_contributes_once(store):
    db, _, _, _ = store
    old, final = put(store, "2025-10-06T12:00:00.000000Z", unresolved=True)
    db.prune(NOW, domain_id=DOMAIN)
    assert db.health(DOMAIN)["unresolved_requests"] == 1
    assert db.finalize(final) == "committed" and db.finalize(final) == "same"
    assert query(store)["measured_input"] == 7
    db.prune(NOW, domain_id=DOMAIN)
    with pytest.raises(UsageError, match="accounting_start_missing"):
        db.finalize(final)
    with pytest.raises(UsageError, match="accounting_conflict"):
        db.start(old, authority_scope=store[2])
    assert query(store)["requests"] == 1


def test_monotone_floors_boundary_and_reopen_survive_clock_and_policy_rewind(store):
    db, _, _, clock = store
    boundary, _ = put(store, "2026-09-07T12:00:00.000000Z")
    first = db.prune(NOW, domain_id=DOMAIN)
    assert first["detail_deleted"] == 0
    before = db.health(DOMAIN)["snapshot_revision"]
    again = db.prune("2026-10-06T00:00:00Z", domain_id=DOMAIN, detail_days=60, daily_days=730)
    assert first["detail_floor_utc"] == again["detail_floor_utc"]
    assert first["daily_floor_utc"] == again["daily_floor_utc"] == "2025-10-07T00:00:00.000000Z"
    assert db.health(DOMAIN)["snapshot_revision"] == before
    reopened = UsageStore(KeyStore(db.key_store.path))
    clock[0] = "2026-09-01T00:00:00.000000Z"
    with pytest.raises(UsageError, match="usage_coverage_unavailable"):
        reopened.query(UsageQuery("detail", **full_day("2026-09-06")), domain_id=DOMAIN)
    with db.key_store._connect() as conn:
        assert conn.execute("SELECT request_id FROM usage_starts").fetchone()[0] == boundary.request_id


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


def test_provenance_unknown_zero_partial_optional_breakdowns_and_unresolved(store):
    put(store, usage=TokenUsage(TokenDirection(0, "measured"), TokenDirection()))
    put(store, usage=TokenUsage(TokenDirection(9, "estimated", partial=True), TokenDirection(3, "measured", partial=True),
                               cache_read_input_tokens=2, reasoning_output_tokens=1))
    put(store, usage=TokenUsage(TokenDirection(applicability="not_applicable"), TokenDirection(applicability="not_applicable")))
    put(store, unresolved=True)
    result = query(store)
    assert (result["measured_input"], result["estimated_input"], result["unknown_output_requests"]) == (0, 9, 1)
    assert result["partial_input_requests"] == result["partial_output_requests"] == 1
    assert result["not_applicable_input_requests"] == result["not_applicable_output_requests"] == 1
    assert result["cache_read_input"] == 2 and result["reasoning_output"] == 1
    assert result["unknown_cache_read_requests"] == 1 and result["unresolved_requests"] == 1
    assert not result["coverage_complete"]
    detail = query(store, "detail", from_utc="2026-10-07T00:00:00Z", to_utc=NOW)
    assert len(detail["records"]) == 4 and sum(r["accounting_status"] == "unresolved" for r in detail["records"]) == 1
    assert "host_fixture" not in json.dumps(detail)


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


@pytest.mark.parametrize("options", [
    {"granularity": "minute"}, {"from_utc": NOW}, {"limit": True}, {"limit": 501},
    {"group_by": ("model;DROP TABLE usage_starts",)}, {"group_by": ("model", "model")},
    {"filters": (("request_id", "x"),)}, {"filters": (("binding_revision", True),)},
    {"filters": (("model", "a"), ("model", "b"))}, {"filters": (("input_partial", 1),)},
    {"filters": (("model", ["a"]),)}, {"require_complete": 1}, {"cursor": "a" * 4097},
    {"granularity": "daily", "from_utc": "2026-10-07T00:00:01Z", "to_utc": "2026-10-08T00:00:00Z"},
    {"granularity": "detail", "from_utc": ORIGIN, "to_utc": NOW},
])
def test_closed_query_validation(options):
    with pytest.raises(UsageError): UsageQuery(**{"granularity": "cumulative", **options})
    with pytest.raises(UsageError): UsageQuery.from_pairs((("limit", 1), ("limit", 2)))
    with pytest.raises(UsageError): UsageQuery.from_pairs((("raw_sql", "SELECT 1"),))


def test_python_integer_overflow_safe_reduction_and_native_limits(store, monkeypatch):
    put(store, model="model_0"); put(store, model="model_1")
    db = store[0]
    with db.key_store._connect() as conn:
        conn.execute("UPDATE usage_cumulative SET measured_input=?", (ledger._MAX_INT,))
        with pytest.raises(sqlite3.OperationalError): conn.execute("SELECT SUM(measured_input) FROM usage_cumulative").fetchone()
    assert query(store)["measured_input"] == ledger._MAX_INT * 2
    monkeypatch.setattr(ledger, "_QUERY_SCAN_LIMIT", 1)
    with pytest.raises(UsageError, match="usage_query_limited") as error: query(store)
    assert error.value.result["available"] is False and "coverage_segments" in error.value.result
    monkeypatch.setattr(ledger, "_QUERY_SCAN_LIMIT", 10000)
    monkeypatch.setattr(ledger, "_QUERY_GROUP_LIMIT", 1)
    with pytest.raises(UsageError, match="usage_query_limited"): query(store, group_by=("model",))
    monkeypatch.setattr(ledger, "_QUERY_GROUP_LIMIT", 5000)
    with pytest.raises(UsageError, match="usage_query_limited"):
        db.query(UsageQuery("cumulative"), domain_id=DOMAIN, deadline_seconds=1e-12)
    with pytest.raises(UsageError): db.query(UsageQuery("cumulative"), domain_id=DOMAIN, deadline_seconds=6)
    # Connection/transaction was released after each failed bounded query.
    assert query(store)["measured_input"] == ledger._MAX_INT * 2


def test_bounded_prune_batches_and_literal_filters(store, monkeypatch):
    monkeypatch.setattr(ledger, "_PRUNE_LIMIT", 1)
    for index in range(3): put(store, "2025-10-06T12:00:00.000000Z", model="model_" + str(index))
    db = store[0]
    total = query(store)["requests"]
    for index in range(3):
        result = db.prune(NOW, domain_id=DOMAIN)
        assert result["detail_deleted"] == result["daily_deleted"] == 1
        assert result["more"] == (index < 2)
        assert query(store)["requests"] == total
    assert db.prune(NOW, domain_id=DOMAIN)["detail_deleted"] == 0
    model = "model_' OR 1=1 --"
    put(store, model=model)
    assert query(store, filters=(("model", model),))["requests"] == 1
    assert query(store)["requests"] == 4


def test_query_snapshot_is_consistent_with_concurrent_finalization(store, monkeypatch):
    db = store[0]
    put(store)
    _, pending = put(store, unresolved=True)
    old_revision = db.health(DOMAIN)["snapshot_revision"]
    original = db._detail_query_row
    pending_write = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        def row_read(row):
            if not pending_write:
                pending_write.append(pool.submit(db.finalize, pending))
            return original(row)
        monkeypatch.setattr(db, "_detail_query_row", row_read)
        snapshot = query(store, "detail", from_utc="2026-10-07T00:00:00Z", to_utc=NOW)
        assert pending_write[0].result(timeout=3) == "committed"
    assert snapshot["snapshot_revision"] == old_revision and snapshot["unresolved_requests"] == 1
    assert snapshot["measured_input"] == 7
    current = query(store)
    assert current["snapshot_revision"] == old_revision + 1 and current["measured_input"] == 14


def test_prune_and_late_terminal_race_never_erase_cumulative_or_recreate_missing_start(store):
    db = store[0]
    _, pending = put(store, "2025-10-06T12:00:00.000000Z", unresolved=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pruner = pool.submit(db.prune, NOW, domain_id=DOMAIN)
        finalizer = pool.submit(db.finalize, replace(pending, ended_at=NOW))
        assert finalizer.result(timeout=3) == "committed"
        pruner.result(timeout=3)
    assert query(store)["measured_input"] == 7 and query(store)["unresolved_requests"] == 0
    db.prune(NOW, domain_id=DOMAIN)
    with pytest.raises(UsageError, match="accounting_start_missing"):
        db.finalize(replace(pending, ended_at=NOW))
    assert query(store)["requests"] == 1


def test_response_limit_and_bad_persisted_floor_never_return_partial_success(store, monkeypatch):
    put(store)
    monkeypatch.setattr(ledger, "_QUERY_RESPONSE_LIMIT", 64)
    with pytest.raises(UsageError, match="usage_query_limited") as error:
        query(store)
    assert error.value.result["available"] is False and "groups" not in error.value.result
    monkeypatch.setattr(ledger, "_QUERY_RESPONSE_LIMIT", 8 * 1024 * 1024)
    with store[0].key_store._connect() as conn:
        conn.execute("UPDATE usage_domains SET detail_floor_utc='bad-fixture'")
    with pytest.raises(UsageError, match="accounting_unavailable"):
        query(store)


def test_half_open_accepted_range_and_midnight_finalization(store):
    db = store[0]
    _, pending = put(store, "2026-10-06T23:59:59.000000Z", unresolved=True)
    db.finalize(replace(pending, ended_at="2026-10-07T00:00:02.000000Z"))
    put(store, "2026-10-07T00:00:00.000000Z")
    old_day = query(store, "daily", **full_day("2026-10-06"))
    assert old_day["requests"] == 1 and old_day["measured_input"] == 7
    detail = query(store, "detail", from_utc="2026-10-06T23:59:59Z", to_utc="2026-10-07T00:00:00Z")
    assert len(detail["records"]) == 1 and detail["requests"] == 1
    assert detail["records"][0]["ended_at"] > detail["requested_range"]["to_utc"]
    assert "latency_sum_ms" not in query(store)
