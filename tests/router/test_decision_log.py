"""Metadata-only audit records for the direct gateway."""
from __future__ import annotations

import threading

from anvil_serving.router.decision_log import AttemptRecord, DecisionLog, DecisionRecord, summarize_decisions


def _record(*, reason="served", served=True):
    return DecisionRecord(
        kind="chat",
        requested_tier="heavy-local",
        attempts=(AttemptRecord("heavy-local", served, reason, 3, 2 if served else 0, "served" if served else "skipped"),),
        served_tier="heavy-local" if served else None,
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
    assert summary["totals"]["served_tiers"] == {"heavy-local": 1}


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
                    requested_tier="heavy-local",
                    attempts=(AttemptRecord(
                        "heavy-local", True, "served", 1, 1, "served"
                    ),),
                    served_tier="heavy-local",
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
