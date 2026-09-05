"""Metadata-only audit records for the direct gateway."""
from __future__ import annotations

import threading
import dataclasses
import json

import pytest

from anvil_serving.router.decision_log import AttemptRecord, DecisionLog, DecisionRecord, summarize_decisions
from anvil_serving.router.decision_log import DecisionLogWriter, decision_line


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
    del expected["replica_member_id"], expected["replica_selection"]
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
