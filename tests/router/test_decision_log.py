"""Metadata-only audit records for the direct gateway."""
from __future__ import annotations

import threading
import dataclasses
import json
import http.client
from pathlib import Path

import pytest

from anvil_serving.router.decision_log import (
    MAX_HISTORY_SCAN_BYTES, AttemptRecord, DecisionLog, DecisionLogWriter,
    DecisionRecord, summarize_decisions,
)
from anvil_serving.router.decision_log import decision_line
from anvil_serving.router.replica_scheduler import (
    PressureFreshness,
    ReplicaDecision,
    ReplicaDecisionReason,
    ReplicaScore,
)


def _record(*, reason="served", served=True):
    return DecisionRecord(
        kind="chat",
        requested_tier="primary-local",
        attempts=(AttemptRecord("primary-local", served, reason, 3, 2 if served else 0, "served" if served else "skipped"),),
        served_tier="primary-local" if served else None,
        total_prompt_tokens=3,
        total_completion_tokens=2 if served else 0,
        route="llm.primary",
    )


def test_decision_log_is_bounded_and_snapshot_is_independent():
    log = DecisionLog(max_records=2)
    log.record(_record(reason="one"))
    snapshot = log.records
    log.record(_record(reason="two"))
    log.record(_record(reason="three"))

    assert len(snapshot) == 1
    assert [record.attempts[0].reason for record in log.records] == ["two", "three"]


def test_legacy_client_identity_is_retained_only_on_the_client_field():
    from anvil_serving.router.internal import InternalRequest
    from anvil_serving.router.decision_log import request_correlation

    correlation = request_correlation(InternalRequest(
        model="llm.primary", messages=[], raw={"_anvil_correlation": {"client_id": "_legacy"}},
    ))
    assert correlation["client_id"] == "_legacy"
    log = DecisionLog()
    log.record(dataclasses.replace(_record(), client_id="_legacy"))
    assert log.last.client_id == "_legacy"
    assert log.summary()["records"][0]["client_id"] == "_legacy"


def test_summary_keeps_only_metadata_and_redacts_secret_shaped_values():
    log = DecisionLog()
    log.record(_record(reason="Bearer this-is-a-token"))

    summary = summarize_decisions(log.records)

    attempt = summary["records"][0]["attempts"][0]
    assert attempt["reason"] == "<redacted>"
    assert "this-is-a-token" not in repr(summary)
    assert summary["totals"]["served_tiers"] == {"primary-local": 1}


def test_direct_failure_record_is_reported_as_unserved():
    summary = summarize_decisions((_record(reason="unavailable", served=False),))

    assert summary["records"][0]["served_tier"] == "-"


def test_concurrent_appends_do_not_lose_or_corrupt_records():
    thread_count = 12
    records_per_thread = 40
    log = DecisionLog(max_records=None)
    barrier = threading.Barrier(thread_count)
    errors = []

    def writer(thread_index):
        try:
            barrier.wait()
            for record_index in range(records_per_thread):
                route = f"llm.thread-{thread_index}-{record_index}"
                log.record(DecisionRecord(
                    kind="chat",
                    requested_tier="primary-local",
                    attempts=(AttemptRecord(
                        "primary-local", True, "served", 1, 1, "served"
                    ),),
                    served_tier="primary-local",
                    total_prompt_tokens=1,
                    total_completion_tokens=1,
                    route=route,
                ))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(index,))
        for index in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(log) == thread_count * records_per_thread
    assert {record.route for record in log.records} == {
        f"llm.thread-{thread_index}-{record_index}"
        for thread_index in range(thread_count)
        for record_index in range(records_per_thread)
    }


def test_concurrent_reads_are_safe_while_writers_append():
    writer_count = 8
    reader_count = 8
    records_per_writer = 50
    log = DecisionLog(max_records=None)
    done = threading.Event()
    read_errors = []

    def reader():
        while not done.is_set():
            try:
                _ = log.last
                _ = log.records
                _ = len(log)
                _ = log.summary(limit=20)
            except Exception as exc:  # noqa: BLE001
                read_errors.append(exc)
                return

    def writer():
        for _ in range(records_per_writer):
            log.record(_record())

    readers = [threading.Thread(target=reader) for _ in range(reader_count)]
    writers = [threading.Thread(target=writer) for _ in range(writer_count)]
    for thread in readers:
        thread.start()
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join()
    done.set()
    for thread in readers:
        thread.join()

    assert read_errors == []
    assert len(log) == writer_count * records_per_writer


class _UnsafeValue:
    def __str__(self):
        raise AssertionError("optional metadata must not be stringified")


class _StringSubclass(str):
    pass


@pytest.mark.parametrize("member,selection", [
    ("lane-a", "identity_passed"),
    ("A" + "x" * 63, "identity_passed"),
    (None, "not_admitted"),
    (None, "request_rejected"),
])
def test_replica_metadata_has_one_shared_safe_projection(tmp_path, member, selection):
    record = dataclasses.replace(_record(), replica_member_id=member, replica_selection=selection)
    log = DecisionLog(sink=DecisionLogWriter(str(tmp_path / "decisions.jsonl")))
    log.record(record)
    expected = {"replica_selection": selection}
    if member is not None:
        expected["replica_member_id"] = member
    assert log.last.replica_member_id == member
    assert log.last.replica_selection == selection
    for projection in (log.summary()["records"][0], json.loads((tmp_path / "decisions.jsonl").read_text())):
        assert {key: value for key, value in projection.items() if key.startswith("replica_")} == expected
    assert all(f"{key}={value}" in decision_line(record).split() for key, value in expected.items())


@pytest.mark.parametrize("member,selection", [
    (None, "identity_passed"), ("lane-a", None), ("lane-a", "not_admitted"),
    ("lane-a", "request_rejected"), ("lane-a", "provider-secret"),
    ("9lane", "identity_passed"), ("a" * 65, "identity_passed"),
    ("lane-a\nforged=value", "identity_passed"),
    ("http://100.64.0.10/v1", "identity_passed"),
    ({"secret": "private"}, "identity_passed"), (True, "identity_passed"),
    (_UnsafeValue(), "identity_passed"), ("lane-a", _UnsafeValue()),
    (_StringSubclass("lane-a"), "identity_passed"),
    ("lane-a", _StringSubclass("identity_passed")),
    ("lane-a", ["identity_passed"]), ("lane-a", "x" * 100000),
], ids=[f"invalid-pair-{index}" for index in range(17)])
def test_invalid_replica_pair_is_dropped_before_every_surface(tmp_path, member, selection):
    record = dataclasses.replace(_record(), replica_member_id=member, replica_selection=selection)
    log = DecisionLog()
    log.record(record)
    assert log.last.replica_member_id is None
    assert log.last.replica_selection is None
    # Summary also accepts untrusted JSON-like maps without conversion.
    summary = summarize_decisions([{"replica_member_id": member, "replica_selection": selection}])
    assert not any(key.startswith("replica_") for key in summary["records"][0])
    assert "replica_" not in decision_line(record)
    path = tmp_path / "raw-writer.jsonl"
    DecisionLogWriter(str(path))(record)  # independent of DecisionLog sanitization
    assert not any(key.startswith("replica_") for key in json.loads(path.read_text()))


def test_writer_allowlist_preserves_legacy_fields_and_excludes_subclass_payload(tmp_path):
    @dataclasses.dataclass(frozen=True)
    class ExtendedAttempt(AttemptRecord):
        private_payload: object = dataclasses.field(default_factory=_UnsafeValue)

    @dataclasses.dataclass(frozen=True)
    class ExtendedRecord(DecisionRecord):
        private_payload: object = dataclasses.field(default_factory=_UnsafeValue)

    original = dataclasses.replace(
        _record(), unix_ts=1.25,
        workload_created_at="2026-09-05T12:00:00.000000Z",
        workload_updated_at="2026-09-05T12:00:01.000000Z", workload_outcome="success",
    )
    expected = dataclasses.asdict(original)
    for name in (
        "replica_member_id", "replica_selection", "replica_scheduler", "session_id",
        "client_id", "cache_read_input_tokens", "admission_wait_ms", "config_sha256",
        "router_version",
    ):
        del expected[name]
    expected["attempts"] = list(expected["attempts"])
    record = ExtendedRecord(**{f.name: getattr(original, f.name) for f in dataclasses.fields(original)})
    record = dataclasses.replace(record, attempts=(ExtendedAttempt(**dataclasses.asdict(original.attempts[0])),))
    path = tmp_path / "allowlist.jsonl"
    DecisionLogWriter(str(path))(record)
    assert json.loads(path.read_text()) == expected


def test_direct_record_retains_legacy_optional_omission_and_audit_shape(tmp_path):
    path = tmp_path / "direct.jsonl"
    DecisionLogWriter(str(path))(_record())
    payload = json.loads(path.read_text())
    assert not any(key.startswith(("replica_", "workload_")) for key in payload)
    assert "replica_" not in repr(summarize_decisions([_record()]))
    assert decision_line(_record()) == (
        "route=llm.primary kind=chat served=primary-local outcome=served "
        "tier=primary-local prompt=3 completion=2"
    )


def test_history_reads_only_configured_rotations_and_projects_metadata(tmp_path):
    path = tmp_path / "decisions.jsonl"
    writer = DecisionLogWriter(str(path), max_bytes=1024)
    log = DecisionLog(sink=writer)
    for index in range(12):
        log.record(dataclasses.replace(
            _record(), gateway_request_id=f"req_{index:032x}",
            session_id="session-a", client_id="client-a",
            cache_read_input_tokens=index, admission_wait_ms=index + 1,
            config_sha256="a" * 64, router_version="1.2.3",
        ))
    assert path.exists() and (tmp_path / "decisions.jsonl.1").exists()
    history = log.lookup_history(session_id="session-a")
    assert history["scope"] == "decision_log_jsonl"
    assert history["available"] is True
    assert history["records"]
    assert all(record["session_id"] == "session-a" for record in history["records"])
    assert all(record["client_id"] == "client-a" for record in history["records"])
    assert "private" not in repr(history).lower()
    assert all(record["config_sha256"] == "a" * 64 for record in history["records"])


def test_history_ignores_malformed_and_oversized_lines_without_leaking_them(tmp_path):
    path = tmp_path / "decisions.jsonl"
    writer = DecisionLogWriter(str(path))
    log = DecisionLog(sink=writer)
    log.record(dataclasses.replace(_record(), session_id="safe-session"))
    with path.open("ab") as handle:
        handle.write(b'{"session_id":"safe-session","prompt":"PRIVATE-PROMPT"}\n')
        handle.write(b"{" + b"x" * (64 * 1024 + 1) + b"}\n")
        handle.write(b"not-json\n")
    history = log.lookup_history(session_id="safe-session")
    encoded = repr(history)
    assert len(history["records"]) == 2
    assert "PRIVATE-PROMPT" not in encoded
    assert "PRIVATE-PROMPT" not in encoded


def test_history_marks_large_generation_truncated_and_never_accepts_a_path(tmp_path):
    path = tmp_path / "decisions.jsonl"
    writer = DecisionLogWriter(str(path))
    log = DecisionLog(sink=writer)
    with path.open("wb") as handle:
        handle.write(b'{"session_id":"old-session"}\n')
        handle.write(b"x" * (MAX_HISTORY_SCAN_BYTES + 8))
        handle.write(b'\n{"session_id":"new-session","attempts":[]}\n')
    history = log.lookup_history(session_id="new-session")
    assert history["truncated"] is True
    assert len(history["records"]) == 1
    assert DecisionLog().lookup_history(session_id="new-session")["available"] is False
    with pytest.raises(ValueError):
        log.lookup_history(session_id="bad value\n")


def test_history_reports_unavailable_when_the_configured_file_is_lost(tmp_path):
    path = tmp_path / "decisions.jsonl"
    log = DecisionLog(sink=DecisionLogWriter(str(path)))
    path.unlink()
    history = log.lookup_history(session_id="session-a")
    assert history == {
        "scope": "decision_log_jsonl", "records": [], "truncated": False,
        "available": False,
    }


def test_request_trace_falls_back_to_retained_history_after_restart(tmp_path):
    from anvil_serving.router.serve import RoutingBackend

    path = tmp_path / "decisions.jsonl"
    original = DecisionLog(sink=DecisionLogWriter(str(path)))
    request_id = "req_" + "a" * 32
    original.record(dataclasses.replace(_record(), gateway_request_id=request_id))
    restarted = DecisionLog(sink=DecisionLogWriter(str(path)))
    trace = RoutingBackend.request_trace(type("Router", (), {"_decision_log": restarted})(), request_id)
    assert trace["scope"] == "decision_log_jsonl"
    assert trace["record"]["gateway_request_id"] == request_id
    with pytest.raises(KeyError):
        RoutingBackend.request_trace(type("Router", (), {"_decision_log": DecisionLog()})(), request_id)


def test_front_door_persists_session_history_and_reports_lost_source(tmp_path):
    from anvil_serving.router.config import load
    from anvil_serving.router.serve import RoutingBackend
    from tests.router.helpers import http_get, server_context

    class Backend:
        def generate(self, request):
            yield "ok"

    config = load(Path(__file__).resolve().parents[2] / "configs" / "example.toml")
    path = tmp_path / "decisions.jsonl"
    log = DecisionLog(sink=DecisionLogWriter(str(path)))
    routing = RoutingBackend(config, {"primary-local": Backend()}, decision_log=log)
    with server_context(routing, token="test-credential") as (host, port):
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.request("POST", "/v1/chat/completions", json.dumps({
                "model": "llm.primary", "messages": [{"role": "user", "content": "private prompt"}],
            }), {
                "Authorization": "Bearer test-credential", "Content-Type": "application/json",
                "X-Anvil-Session-Id": "session-a",
            })
            response = connection.getresponse()
            assert response.status == 200
            gateway_request_id = response.getheader("X-Anvil-Request-Id")
            response.read()
        finally:
            connection.close()
        status, _, body = http_get(host, port, "/v1/requests?session_id=session-a&history=1", token="test-credential")
        assert status == 200
        history = json.loads(body)
        assert history["records"] and history["records"][0]["session_id"] == "session-a"
        path.unlink()
        # Simulate the post-restart request-id fallback, where memory no
        # longer contains the terminal record and only JSONL could answer it.
        with log._lock:
            log._records.clear()
        status, _, _ = http_get(host, port, "/v1/requests?session_id=session-a&history=1", token="test-credential")
        assert status == 503
        status, _, _ = http_get(host, port, f"/v1/requests/{gateway_request_id}", token="test-credential")
        assert status == 503


def _scheduler_decision():
    score = ReplicaScore(
        "member-a", 0, 2, False, 100, 0, PressureFreshness.FRESH,
    )
    return ReplicaDecision(
        "member-a", ("member-a",), (score,), ReplicaDecisionReason.SELECTED,
    )


def test_scheduler_projection_is_closed_and_shared_by_memory_summary_line_and_writer(tmp_path):
    record = dataclasses.replace(_record(), replica_scheduler=_scheduler_decision())
    path = tmp_path / "scheduler.jsonl"
    log = DecisionLog(sink=DecisionLogWriter(str(path)))
    log.record(record)
    expected = _scheduler_decision().to_dict()
    assert log.last.replica_scheduler == _scheduler_decision()
    assert log.summary()["records"][0]["replica_scheduler"] == expected
    assert json.loads(path.read_text())["replica_scheduler"] == expected
    assert "replica_scheduler=capacity" in decision_line(log.last)
    assert "replica_eligible_count=1" in decision_line(log.last)


def test_scheduler_invalid_nested_shape_is_dropped_without_touching_valid_replica_pair():
    @dataclasses.dataclass(frozen=True)
    class UnsafeDecision(ReplicaDecision):
        pass

    record = dataclasses.replace(
        _record(), replica_member_id="member-a", replica_selection="identity_passed",
        replica_scheduler=UnsafeDecision(
            "member-a", ("member-a",), _scheduler_decision().scores,
            ReplicaDecisionReason.SELECTED,
        ),
    )
    log = DecisionLog()
    log.record(record)
    assert log.last.replica_member_id == "member-a"
    assert log.last.replica_scheduler is None
    malformed = {"replica_scheduler": {"selected_member_id": "member-a"}}
    assert "replica_scheduler" not in summarize_decisions([malformed])["records"][0]


@pytest.mark.parametrize("mutate", [
    lambda payload: payload.update(extra="private-token"),
    lambda payload: payload.update(selected_member_id="member-b"),
    lambda payload: payload.update(eligible_member_ids=["member-a"] * 17),
    lambda payload: payload["scores"][0].update(local_numerator=True),
    lambda payload: payload["scores"][0].update(freshness="future-private"),
    lambda payload: payload["scores"][0].update(local_numerator=-1),
], ids=["extra", "mismatch", "oversize", "numeric-bool", "freshness", "numeric-range"])
def test_captured_scheduler_json_requires_the_closed_canonical_shape(mutate):
    payload = _scheduler_decision().to_dict()
    mutate(payload)
    summary = summarize_decisions([{"replica_scheduler": payload}])
    assert "replica_scheduler" not in summary["records"][0]


def test_invalid_scheduler_never_leaks_private_nested_values_to_any_audit_surface(tmp_path):
    decision = _scheduler_decision()
    object.__setattr__(decision, "selected_member_id", "private-token")
    record = dataclasses.replace(
        _record(), replica_member_id="member-a", replica_selection="identity_passed",
        replica_scheduler=decision,
    )
    path = tmp_path / "private-scheduler.jsonl"
    log = DecisionLog(sink=DecisionLogWriter(str(path)))
    log.record(record)
    combined = repr(log.last) + repr(log.summary()) + decision_line(log.last) + path.read_text()
    assert "private-token" not in combined
    assert log.last.replica_member_id == "member-a"
