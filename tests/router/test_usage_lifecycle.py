"""Synthetic atomic ledger checks; no serving, account or network calls."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

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
