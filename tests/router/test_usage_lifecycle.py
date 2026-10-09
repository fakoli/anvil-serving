"""Synthetic atomic ledger checks; no serving, account or network calls."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest

from anvil_serving.router import usage_store as ledger
from anvil_serving.router.decision_log import TokenDirection, TokenUsage
from anvil_serving.router.identity import EndUser, forwarded_caller, legacy_caller
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.usage_store import (AuthorityScope, Observation, RequestStart, RouteAssociation,
                                            RunOwner, Terminal, UsageError, UsageStore)
from tests.router.key_fixtures import tmp_path as tmp_path

AT = "2026-10-07T12:00:00.000000Z"
END = "2026-10-07T12:00:01.000000Z"
LATER = "2026-10-07T13:00:00.000000Z"
ROUTE = RouteAssociation("route_fixture", "backend_fixture", "serve_fixture", "owner_fixture")
_ACTUAL_LINUX_OWNER = ledger._linux_owner


def ident():
    return str(uuid.uuid4())


@pytest.fixture
def store(tmp_path, monkeypatch):
    key = KeyStore.initialize(tmp_path / "private" / "keys.sqlite3")
    usage = UsageStore(key)
    usage.migrate()
    owner = RunOwner("host_fixture", ident(), 1, 2, 1, 2, 1000, 1, 3, 123, 456)
    monkeypatch.setattr(ledger, "_linux_owner", lambda *args: (owner, ()))
    monkeypatch.setattr(ledger, "_now", lambda: AT)
    run = usage.register_run(owner, domain_id="domain_fixture", configuration_revision="config_fixture", enabled=True)
    scope = AuthorityScope("domain_fixture", "config_fixture", (run,), AT, LATER)
    return usage, owner, run, scope


def start_at(run, **options):
    return RequestStart(ident(), run, AT, legacy_caller(), "chat", "llm.primary", attempt_id=ident(), **options)


def tokens(input_count=7, output_count=5, **options):
    return TokenUsage(TokenDirection(input_count, "measured"), TokenDirection(output_count, "measured"), **options)


def terminal(start, **changes):
    values = dict(request_id=start.request_id, ended_at=END, dispatched=True, generation_outcome="success",
                  delivery_outcome="success", outcome="success", route=ROUTE, tokens=tokens(), latency_ms=1000)
    values.update(changes)
    return Terminal(**values)


def rows(usage, table):
    with usage.key_store._connect() as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]


def revision(usage):
    return usage.health("domain_fixture")["snapshot_revision"]


def test_start_dispatch_checkpoint_terminal_matching_replays_and_conflicts(store):
    usage, _, run, scope = store
    start = start_at(run)
    before = revision(usage)
    assert usage.start(start, authority_scope=scope) == "started"
    assert revision(usage) == before + 1
    assert usage.start(start, authority_scope=scope) == "same"
    assert revision(usage) == before + 1
    with pytest.raises(UsageError, match="accounting_conflict"):
        usage.start(replace(start, model="llm.secondary"), authority_scope=scope)
    assert usage.note_dispatch(start.request_id, ROUTE) == "recorded"
    assert usage.note_dispatch(start.request_id, ROUTE) == "same"
    with pytest.raises(UsageError, match="accounting_conflict"):
        usage.note_dispatch(start.request_id, replace(ROUTE, member_id="other"))
    first = Observation(1, END, tokens(9, 8))
    assert usage.note_observation(start.request_id, first) == "recorded"
    assert usage.note_observation(start.request_id, first) == "same"
    # A replacement can be smaller: order comes from sequence, never count.
    second = Observation(2, END, tokens(7, 5))
    assert usage.note_observation(start.request_id, second) == "recorded"
    for stale in (first, replace(second, tokens=tokens(100, 100))):
        with pytest.raises(UsageError, match="accounting_conflict"):
            usage.note_observation(start.request_id, stale)
    assert rows(usage, "usage_daily") == []
    assert usage.finalize(terminal(start)) == "committed"
    final_revision = revision(usage)
    assert usage.finalize(terminal(start)) == "same"
    assert revision(usage) == final_revision
    with pytest.raises(UsageError, match="accounting_conflict"):
        usage.finalize(terminal(start, delivery_outcome="disconnected"))
    with pytest.raises(UsageError, match="accounting_conflict"):
        usage.note_observation(start.request_id, Observation(3, END, tokens()))
    for table in ("usage_daily", "usage_cumulative"):
        [group] = rows(usage, table)
        assert (group["requests"], group["attempts"], group["measured_input"], group["measured_output"]) == (1, 1, 7, 5)
        assert group["latency_sum_ms"] == group["latency_le_1000_ms"] * 1000 == 1000
        assert group["latency_le_100_ms"] == 0 and group["latency_le_inf"] == 1


def test_concurrent_finalizers_contribute_once_and_conflicts_do_not_rewrite(store):
    usage, _, run, scope = store
    start = start_at(run)
    usage.start(start, authority_scope=scope)
    final = terminal(start)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: usage.finalize(final), range(12)))
    assert results.count("committed") == 1 and results.count("same") == 11
    assert rows(usage, "usage_cumulative")[0]["measured_input"] == 7
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(usage.finalize, replace(final, tokens=tokens(i + 10, 5))) for i in range(4)]
        assert all(isinstance(f.exception(), UsageError) for f in futures)
    assert rows(usage, "usage_cumulative")[0]["requests"] == 1


@pytest.mark.parametrize("failure", ["INSERT INTO usage_details", "INSERT INTO usage_daily", "INSERT INTO usage_cumulative", "COMMIT"])
def test_terminal_write_failure_rolls_back_every_contribution_and_leaves_start(store, monkeypatch, failure):
    usage, _, run, scope = store
    start = start_at(run)
    usage.start(start, authority_scope=scope)
    before = revision(usage)
    original = usage.key_store._connect

    class FailingConnection:
        def __init__(self, db):
            object.__setattr__(self, "db", db)
        def __getattr__(self, name):
            return getattr(self.db, name)
        def __setattr__(self, name, value):
            setattr(self.db, name, value)
        def execute(self, sql, *args):
            if sql.startswith(failure):
                raise sqlite3.OperationalError("synthetic failure")
            return self.db.execute(sql, *args)

    @contextmanager
    def failing():
        with original() as db:
            yield FailingConnection(db)
    monkeypatch.setattr(usage.key_store, "_connect", failing)
    with pytest.raises(UsageError, match="accounting_unavailable"):
        usage.finalize(terminal(start))
    monkeypatch.setattr(usage.key_store, "_connect", original)
    assert revision(usage) == before
    assert usage.health("domain_fixture")["unresolved_requests"] == 1
    assert usage.health("domain_fixture")["failure_pending"] is True
    for table in ("usage_details", "usage_daily", "usage_cumulative"):
        assert rows(usage, table) == []
    assert usage.finalize(terminal(start)) == "committed"
    usage.record_failure("domain_fixture")
    assert usage.health("domain_fixture")["accounting_failures"] == 1
    assert usage.health("domain_fixture")["failure_pending"] is False


def test_busy_start_blocks_stub_dispatch_and_no_transaction_spans_inference(store):
    usage, _, run, scope = store
    dispatched = []
    with usage.key_store._connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(UsageError, match="accounting_unavailable"):
            usage.start(start_at(run), authority_scope=scope)
            dispatched.append(True)
        writer.execute("ROLLBACK")
    assert dispatched == [] and rows(usage, "usage_starts") == []
    start = start_at(run)
    usage.start(start, authority_scope=scope)
    # Fake inference can acquire writer immediately after start returns.
    with usage.key_store._connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("ROLLBACK")
    dispatched.append(True)
    assert usage.health("domain_fixture")["unresolved_requests"] == 1


def test_missing_and_pruned_start_cannot_be_recreated_and_expired_replay_refuses(store):
    usage, _, run, scope = store
    absent = start_at(run)
    with pytest.raises(UsageError, match="accounting_start_missing"):
        usage.finalize(terminal(absent))
    old = replace(absent, accepted_at="2026-09-01T00:00:00Z")
    with pytest.raises(UsageError, match="accounting_conflict"):
        usage.start(old, authority_scope=scope)
    usage.start(absent, authority_scope=scope)
    usage.finalize(terminal(absent))
    with usage.key_store._connect() as db:
        db.execute("DELETE FROM usage_details WHERE request_id=?", (absent.request_id,))
        db.execute("DELETE FROM usage_starts WHERE request_id=?", (absent.request_id,))
        db.execute("UPDATE usage_domains SET detail_floor_utc=?", (END,))
    with pytest.raises(UsageError, match="accounting_start_missing"):
        usage.finalize(terminal(absent))
    with pytest.raises(UsageError, match="accounting_conflict"):
        usage.start(absent, authority_scope=scope)
    assert rows(usage, "usage_cumulative")[0]["requests"] == 1


def test_zero_unknown_partial_applicability_components_and_delivery_are_independent(store):
    usage, _, run, scope = store
    known = start_at(run)
    usage.start(known, authority_scope=scope)
    measured = TokenUsage(TokenDirection(0, "measured"), TokenDirection(3, "estimated", partial=True),
                          uncached_input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                          reasoning_output_tokens=2)
    usage.finalize(terminal(known, tokens=measured, delivery_outcome="disconnected", outcome="disconnected"))
    unknown = start_at(run, output_applicability="not_applicable")
    usage.start(unknown, authority_scope=scope)
    usage.finalize(terminal(unknown, tokens=TokenUsage(output=TokenDirection(applicability="not_applicable")), latency_ms=None))
    groups = rows(usage, "usage_cumulative")
    a = next(g for g in groups if g["outcome"] == "disconnected")
    assert a["measured_input"] == 0 and a["input_source"] == "measured" and a["unknown_input_requests"] == 0
    assert a["estimated_output"] == 3 and a["partial_output_requests"] == 1 and a["reasoning_output"] == 2
    assert a["unknown_cache_creation_requests"] == 0
    b = next(g for g in groups if g["outcome"] == "success")
    assert b["unknown_input_requests"] == b["not_applicable_output_requests"] == 1
    assert b["unknown_output_requests"] == b["latency_count"] == 0


def test_groups_keep_immutable_actor_binding_end_user_credential_and_effective_grant(store):
    usage, _, run, scope = store
    key = usage.key_store
    metadata, secret = key.create("synthetic label", ["llm.primary"], ["/v1/chat/completions"])
    key.bind_owner(metadata["key_id"], "service", "service_fixture", expected_revision=0)
    admitted = key.admit(key.authenticate(secret, snapshot=True), "/v1/chat/completions", "llm.primary").caller_snapshot
    caller = forwarded_caller(admitted, EndUser("webui_fixture", "open-webui", "subject_fixture"))
    start = replace(start_at(run), caller=caller)
    usage.start(start, authority_scope=scope)
    key.bind_owner(metadata["key_id"], "service", "later_owner", expected_revision=1)
    key.revoke(metadata["key_id"])
    usage.finalize(terminal(start))
    for table in ("usage_daily", "usage_cumulative"):
        [group] = rows(usage, table)
        assert (group["actor_kind"], group["actor_id"], group["binding_revision"]) == ("service", "service_fixture", 1)
        assert (group["end_user_instance"], group["end_user_issuer"], group["end_user_subject"]) == (
            "webui_fixture", "open-webui", "subject_fixture")
        assert group["credential_id"] == group["grant_reference"] == metadata["key_id"]
        assert group["grant_kind"] == "key_policy" and group["grant_policy_digest"] == caller.grant.policy_digest
        assert group["grant_revision"] is None and group["grant_generation"] is None and group["grant_epoch"] is None


def test_failed_checkpoint_retains_prior_durable_observation_and_revision(store, monkeypatch):
    usage, _, run, scope = store
    start = start_at(run)
    usage.start(start, authority_scope=scope)
    usage.note_dispatch(start.request_id, ROUTE)
    first = Observation(1, END, tokens(4, 2))
    usage.note_observation(start.request_id, first)
    before = revision(usage)
    with usage.key_store._connect() as db:
        db.execute("CREATE TRIGGER fail_checkpoint BEFORE UPDATE OF observation_payload ON usage_starts "
                   "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    with pytest.raises(UsageError, match="accounting_unavailable"):
        usage.note_observation(start.request_id, Observation(2, END, tokens(100, 100)))
    assert revision(usage) == before
    assert rows(usage, "usage_starts")[0]["observation_payload"] == first.to_json()
    with usage.key_store._connect() as db:
        db.execute("DROP TRIGGER fail_checkpoint")
    monkeypatch.setattr(ledger, "observe_run", lambda _: "dead")
    usage.recover((run,), host_domain_id="host_fixture")
    assert rows(usage, "usage_cumulative")[0]["measured_input"] == 4


def test_historical_attested_closed_segment_stays_known_after_current_owner_unknown(store, monkeypatch):
    usage, _, run, scope = store
    usage.coverage_transition(run, False, "config_fixture", at=END)
    monkeypatch.setattr(ledger, "observe_run", lambda _: "unknown")
    usage.recover((run,), host_domain_id="host_fixture")
    with usage.key_store._connect() as db:
        db.row_factory = sqlite3.Row
        assert usage._coverage_state(db, replace(scope, to_utc=END)).roster_complete
        assert not usage._coverage_state(db, scope).roster_complete


@pytest.mark.parametrize("value", ["20261007T120000Z", "2026-10-07 12:00:00Z", "2026-10-07T12:00:00+00:00",
                                  "2026-10-07T12:00:00.1234567Z", "2026-02-31T00:00:00Z"])
def test_start_times_reject_non_rfc3339_non_utc_and_precision_loss(store, value):
    _, _, run, _ = store
    with pytest.raises(UsageError):
        replace(start_at(run), accepted_at=value)


def test_signed64_overflow_refuses_terminal_and_both_aggregate_updates(store):
    usage, _, run, scope = store
    first, second = start_at(run), start_at(run)
    for start in (first, second):
        usage.start(start, authority_scope=scope)
    usage.finalize(terminal(first))
    with usage.key_store._connect() as db:
        db.execute("UPDATE usage_cumulative SET measured_input=?", (2**63 - 2,))
    before = revision(usage)
    with pytest.raises(UsageError, match="accounting_unavailable"):
        usage.finalize(terminal(second))
    assert revision(usage) == before and len(rows(usage, "usage_details")) == 1
    assert rows(usage, "usage_daily")[0]["measured_input"] == 7


def test_accepted_day_and_enabled_start_survive_mode_disable(store, monkeypatch):
    usage, _, run, scope = store
    monkeypatch.setattr(ledger, "_now", lambda: "2026-10-07T23:59:59.000000Z")
    scope = replace(scope, to_utc="2026-10-09T00:00:00Z")
    start = replace(start_at(run), accepted_at="2026-10-07T23:59:59Z")
    usage.start(start, authority_scope=scope)
    usage.coverage_transition(run, False, "config_fixture", at="2026-10-08T00:00:00Z")
    usage.finalize(terminal(start, ended_at="2026-10-08T00:00:10Z"))
    assert rows(usage, "usage_daily")[0]["accepted_day"] == "2026-10-07"
    assert rows(usage, "usage_cumulative")[0]["requests"] == 1
    with usage.key_store._connect() as db:
        db.row_factory = sqlite3.Row
        projection = usage._coverage_state(db, scope)
    assert not projection.roster_complete and "accounting_disabled" in projection.gaps


def test_inclusive_parent_child_attempts_exclusive_children_and_unobserved_do_not_double_count(store):
    usage, _, run, scope = store
    parent = start_at(run, usage_relation="inclusive_parent")
    usage.start(parent, authority_scope=scope)
    child = start_at(run, parent_request_id=parent.request_id, usage_relation="inclusive_parent")
    usage.start(child, authority_scope=scope)
    usage.finalize(terminal(parent, tokens=tokens(10, 10)))
    usage.finalize(terminal(child, tokens=tokens(10, 10)))
    assert rows(usage, "usage_cumulative")[0]["requests"] == 1
    assert rows(usage, "usage_cumulative")[0]["attempts"] == 2
    assert rows(usage, "usage_cumulative")[0]["measured_input"] == 10
    with pytest.raises(UsageError, match="accounting_conflict"):
        usage.start(start_at(run, parent_request_id=parent.request_id), authority_scope=scope)
    outer = start_at(run, usage_relation="unobserved")
    usage.start(outer, authority_scope=scope)
    exclusive = start_at(run, parent_request_id=outer.request_id)
    usage.start(exclusive, authority_scope=scope)
    usage.finalize(terminal(exclusive, tokens=tokens(4, 3)))
    usage.finalize(terminal(outer, tokens=tokens(100, 100), coverage=("usage_relation_unknown",)))
    assert sum(g["requests"] for g in rows(usage, "usage_cumulative")) == 2
    assert sum(g["measured_input"] for g in rows(usage, "usage_cumulative")) == 14


def test_unknown_roster_and_live_disabled_second_run_refuse_enabled_admission(store, monkeypatch):
    usage, owner, run, scope = store
    with pytest.raises(UsageError, match="accounting_configuration_unsupported"):
        usage.start(start_at(run))
    second = usage.register_run(owner, domain_id="domain_fixture", configuration_revision="config_fixture", enabled=False)
    full = replace(scope, run_ids=(run, second))
    for supplied in (scope, full):
        with pytest.raises(UsageError, match="accounting_configuration_unsupported"):
            usage.start(start_at(run), authority_scope=supplied)
    with usage.key_store._connect() as db:
        db.row_factory = sqlite3.Row
        assert not usage._coverage_state(db, domain_id="domain_fixture").roster_complete
    monkeypatch.setattr(ledger, "observe_run", lambda _: "live")
    assert usage.recover((run, second), host_domain_id="host_fixture")["live_runs"] == 2
    assert all(r["state"] == "live" for r in rows(usage, "usage_runs"))


@pytest.mark.parametrize("state", ["live", "unknown", "dead"])
def test_recovery_preserves_latest_durable_partial_and_only_dead_finalizes(store, monkeypatch, state):
    usage, _, run, scope = store
    start = start_at(run)
    usage.start(start, authority_scope=scope)
    usage.note_dispatch(start.request_id, ROUTE)
    observed = TokenUsage(TokenDirection(source="unknown", partial=True), TokenDirection(2, "measured"),
                          uncached_input_tokens=5, cache_read_input_tokens=3)
    usage.note_observation(start.request_id, Observation(1, END, observed))
    monkeypatch.setattr(ledger, "observe_run", lambda _: state)
    result = usage.recover((run,), host_domain_id="host_fixture")
    assert result["recovered_requests"] == int(state == "dead")
    if state == "dead":
        [detail] = rows(usage, "usage_details")
        recovered = Terminal.from_json(detail["terminal_payload"])
        assert recovered.tokens.uncached_input_tokens == 5 and recovered.tokens.cache_read_input_tokens == 3
        assert recovered.tokens.output.count == 2 and recovered.tokens.output.partial
        assert recovered.tokens.input.count is None and recovered.tokens.input.partial
        assert "dispatch_uncertain" in recovered.coverage
        assert usage.recover((run,), host_domain_id="host_fixture")["recovered_requests"] == 0
        assert rows(usage, "usage_cumulative")[0]["attempts"] == 1
    else:
        assert rows(usage, "usage_details") == [] and usage.health("domain_fixture")["unresolved_requests"] == 1


@pytest.mark.parametrize("change", [{"boot_id": "00000000-0000-0000-0000-000000000001"},
                                  {"pid_namespace_inode": 99}, {"procfs_pid_namespace_inode": 99},
                                  {"uid": 999}, {"user_namespace_inode": 99}])
def test_foreign_namespaces_uid_and_unattested_reboot_are_unknown_before_pid_lookup(store, monkeypatch, change):
    _, owner, _, _ = store
    other = replace(owner, **change)
    monkeypatch.setattr(ledger, "_linux_owner", lambda *args: (other, ()))
    monkeypatch.setattr(Path, "read_text", lambda *args, **kw: pytest.fail("must compare domain before PID lookup"))
    assert ledger.observe_run(owner) == "unknown"


def test_inaccessible_procfs_and_unstable_observation_stay_unknown(store, monkeypatch):
    _, owner, _, _ = store
    def denied(*args):
        raise PermissionError()
    monkeypatch.setattr(ledger, "_linux_owner", denied)
    assert ledger.observe_run(owner) == "unknown"


@pytest.mark.parametrize("mode,expected", [("matching", "live"), ("reused", "dead"), ("absent", "dead"),
                                          ("namespace_missing", "unknown"), ("unstable", "unknown")])
def test_comparable_view_process_instance_lookup_and_read_stability(store, monkeypatch, mode, expected):
    _, owner, _, _ = store
    ticks = owner.start_ticks + int(mode == "reused")
    raw = f"{owner.pid} (name with ) parentheses) " + " ".join(["S"] + ["0"] * 18 + [str(ticks)])
    def read(*args, **kwargs):
        if mode == "absent":
            raise FileNotFoundError()
        return raw
    monkeypatch.setattr(Path, "read_text", read)
    def stat(path):
        if mode == "namespace_missing":
            raise FileNotFoundError()
        return SimpleNamespace(st_dev=1, st_ino=3 if str(path).endswith("user") else 2, st_uid=owner.uid)
    monkeypatch.setattr(ledger.os, "stat", stat)
    if mode == "unstable":
        observations = iter(((owner, ()), (replace(owner, start_ticks=owner.start_ticks + 1), ())))
        monkeypatch.setattr(ledger, "_linux_owner", lambda *args: next(observations))
    assert ledger.observe_run(owner) == expected


@pytest.mark.parametrize("restriction", ["hidepid=2", "subset=pid", "overmount", "wrong_view"])
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux procfs oracle")
def test_restricted_procfs_mount_and_namespace_view_are_not_comparability_proof(store, monkeypatch, restriction):
    _, owner, _, _ = store
    monkeypatch.setattr(ledger.os, "getpid", lambda: owner.pid)
    monkeypatch.setattr(ledger.os, "geteuid", lambda: owner.uid)
    monkeypatch.setattr(ledger.os, "readlink", lambda _: str(owner.pid))
    def read(path, **kwargs):
        if str(path).endswith("mountinfo"):
            extra = "\n2 1 0:2 / /proc/123 rw - tmpfs tmpfs rw" if restriction == "overmount" else ""
            return "1 0 0:1 / /proc rw - proc proc rw," + restriction + extra
        if str(path).endswith("boot_id"):
            return owner.boot_id
        return f"{owner.pid} (fixture) " + " ".join(["S"] + ["0"] * 18 + [str(owner.start_ticks)])
    monkeypatch.setattr(Path, "read_text", read)
    def stat(path):
        inode = 3 if str(path).endswith("user") else 2
        if restriction == "wrong_view" and str(path) == "/proc/1/ns/pid":
            inode = 99
        return SimpleNamespace(st_dev=1, st_ino=inode)
    monkeypatch.setattr(ledger.os, "stat", stat)
    with pytest.raises(UsageError):
        _ACTUAL_LINUX_OWNER(owner.host_domain_id, owner.pid)


@pytest.mark.parametrize("overmount", [
    "/proc/123/stat", "/proc/123/ns", "/proc/123/ns/pid", "/proc/123/ns/user",
    "/proc/456/stat", "/proc/456/ns/pid", "/proc/self/stat", "/proc/self/ns/pid",
    "/proc/self/mountinfo", "/proc/1/ns/pid", "/proc/sys/kernel/random", "/proc/sys/kernel/random/boot_id",
])
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux procfs oracle")
def test_sensitive_descendant_overmount_is_unknown_and_preserves_unresolved_start(store, monkeypatch, overmount):
    usage, owner, run, scope = store
    start = start_at(run)
    usage.start(start, authority_scope=scope)
    saved_payload = rows(usage, "usage_starts")[0]["start_payload"]
    actual_stat = ledger.os.stat
    def read(path, **kwargs):
        path = str(path)
        if path == "/proc/self/mountinfo":
            return "1 0 0:1 / /proc rw - proc proc rw\n" + f"2 1 0:2 / {overmount} rw - tmpfs tmpfs rw\n"
        if path == "/proc/sys/kernel/random/boot_id":
            return owner.boot_id
        pid, ticks = (456, 100) if path == "/proc/self/stat" else (owner.pid, owner.start_ticks + 1)
        return f"{pid} (fixture) " + " ".join(["S"] + ["0"] * 18 + [str(ticks)])
    def metadata(path, *args, **kwargs):
        if not str(path).startswith("/proc/"):
            return actual_stat(path, *args, **kwargs)
        return SimpleNamespace(st_dev=1, st_ino=3 if str(path).endswith("/user") else 2, st_uid=owner.uid)
    def view(*args):
        # Scope synthetic procfs to the oracle; protected SQLite I/O must use
        # the real process identity even when the runner UID is not 1000.
        with monkeypatch.context() as patch:
            patch.setattr(ledger.os, "getpid", lambda: 456)
            patch.setattr(ledger.os, "geteuid", lambda: owner.uid)
            patch.setattr(ledger.os, "readlink", lambda _: "456")
            patch.setattr(ledger.os, "stat", metadata)
            patch.setattr(Path, "read_text", read)
            return _ACTUAL_LINUX_OWNER(*args)
    monkeypatch.setattr(ledger, "_linux_owner", view)
    assert ledger.observe_run(owner) == "unknown"
    recovered = usage.recover((run,), host_domain_id=owner.host_domain_id)
    assert recovered["unknown_runs"] == 1 and recovered["dead_runs"] == recovered["recovered_requests"] == 0
    assert rows(usage, "usage_starts")[0]["start_payload"] == saved_payload
    assert rows(usage, "usage_runs")[0]["state"] == "unknown"
    assert rows(usage, "usage_details") == rows(usage, "usage_daily") == rows(usage, "usage_cumulative") == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux-only owner proof; other platforms remain UNKNOWN")
def test_actual_live_second_process_safety_and_recovery_requires_full_comparability(tmp_path, store, monkeypatch):
    # This host may deliberately deny proc1 namespace access. Test that real
    # restriction with a live second process; never weaken it to make DEAD pass.
    try:
        _ACTUAL_LINUX_OWNER("host_fixture")
    except (OSError, UsageError):
        usage, _, run, scope = store
        start = start_at(run)
        usage.start(start, authority_scope=scope)
        process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.readline()"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            pid = process.pid
            ns = os.stat(f"/proc/{pid}/ns/pid")
            user = os.stat(f"/proc/{pid}/ns/user")
            real = RunOwner("host_fixture", Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                            ns.st_dev, ns.st_ino, ns.st_dev, ns.st_ino, os.geteuid(), user.st_dev, user.st_ino,
                            pid, ledger._stat_ticks(Path(f"/proc/{pid}/stat").read_text(), pid))
            # Seed a prior synthetic durable owner from the actual live instance;
            # current procfs-view authority remains unproved, so recovery UNKNOWN.
            with usage.key_store._connect() as db:
                values = asdict(real)
                db.execute("UPDATE usage_runs SET " + ",".join(k + "=?" for k in values) + " WHERE run_id=?",
                           (*values.values(), run))
            monkeypatch.setattr(ledger, "_linux_owner", _ACTUAL_LINUX_OWNER)
            assert usage.recover((run,), host_domain_id="host_fixture")["unknown_runs"] == 1
            assert usage.health("domain_fixture")["unresolved_requests"] == 1
            process.stdin.write("exit\n"); process.stdin.flush(); process.wait(timeout=5)
            assert usage.recover((run,), host_domain_id="host_fixture")["unknown_runs"] == 1
            assert rows(usage, "usage_details") == []
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        return
    monkeypatch.setattr(ledger, "_linux_owner", _ACTUAL_LINUX_OWNER)
    usage = UsageStore(KeyStore.initialize(tmp_path / "private" / "keys.sqlite3"))
    usage.migrate()
    # Only server-generated IDs escape the child, never the private process tuple.
    code = '''
import json,sys,uuid
from datetime import datetime,timedelta,timezone
from anvil_serving.router.usage_store import *
from anvil_serving.router.identity import legacy_caller
from anvil_serving.router.keys import KeyStore
u=UsageStore(KeyStore(sys.argv[1])); owner=RunOwner.observe('host_fixture')
at=datetime.now(timezone.utc); utc=lambda t:t.isoformat(timespec='microseconds').replace('+00:00','Z')
r=u.register_run(owner,domain_id='domain_fixture',configuration_revision='config_fixture',enabled=True,started_at=utc(at))
s=RequestStart(str(uuid.uuid4()),r,utc(at),legacy_caller(),'chat','llm.primary',attempt_id=str(uuid.uuid4()))
scope=AuthorityScope('domain_fixture','config_fixture',(r,),utc(at),utc(at+timedelta(hours=1)))
u.start(s,authority_scope=scope)
print(json.dumps({'run_id':r,'request_id':s.request_id}),flush=True)
sys.stdin.readline()
'''
    process = subprocess.Popen([sys.executable, "-c", code, str(usage.key_store.path)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        assert line, process.stderr.read()
        ids = json.loads(line)
        assert usage.recover((ids["run_id"],), host_domain_id="host_fixture")["live_runs"] == 1
        assert usage.health("domain_fixture")["unresolved_requests"] == 1
        process.stdin.write("exit\n"); process.stdin.flush()
        process.wait(timeout=5)
        assert process.returncode == 0
        assert usage.recover((ids["run_id"],), host_domain_id="host_fixture")["recovered_requests"] == 1
        assert usage.recover((ids["run_id"],), host_domain_id="host_fixture")["recovered_requests"] == 0
        [detail] = rows(usage, "usage_details")
        assert Terminal.from_json(detail["terminal_payload"]).tokens.input.count is None
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_closed_serializers_and_health_never_project_private_owner_content(store):
    usage, owner, run, _ = store
    start = start_at(run)
    assert RequestStart.from_json(start.to_json()) == start
    assert Terminal.from_json(terminal(start).to_json()) == terminal(start)
    assert Observation.from_json(Observation(1, END, tokens()).to_json()).tokens == tokens()
    for cls, value in ((RequestStart, start.to_dict()), (Terminal, terminal(start).to_dict()),
                       (Observation, Observation(1, END, tokens()).to_dict()), (RouteAssociation, ROUTE.to_dict())):
        value["prompt"] = "synthetic forbidden content"
        with pytest.raises(UsageError):
            cls.from_dict(value)
    with pytest.raises(UsageError):
        RouteAssociation(route_id="https://example.test/private")
    raw = json.dumps(usage.health("domain_fixture"))
    assert owner.host_domain_id not in raw and owner.boot_id not in raw and "pid" not in raw
    assert usage.health("domain_fixture")["coverage_complete"] is False
