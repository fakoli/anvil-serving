"""Thread-safety of DecisionLog (issue #47, Fix 1).

DecisionLog._records is mutated by record() and read by records/last/__len__,
all of which can be called concurrently from ThreadingHTTPServer handler threads.
The lock added in Fix 1 must prevent lost/corrupt appends and torn reads.
"""
from __future__ import annotations

import threading

from anvil_serving.router.decision_log import (
    AttemptRecord,
    DecisionLog,
    DecisionRecord,
    summarize_decisions,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _attempt(tier_id: str = "t", outcome: str = "served") -> AttemptRecord:
    return AttemptRecord(
        tier_id=tier_id,
        verifier_passed=True,
        verify_reason="ok",
        prompt_tokens=10,
        completion_tokens=5,
        outcome=outcome,
    )


def _record(work_class: str = "bounded-edit", served_tier: str = "t") -> DecisionRecord:
    return DecisionRecord(
        work_class=work_class,
        requested_tiers=(served_tier,),
        attempts=(_attempt(served_tier),),
        served_tier=served_tier,
        total_prompt_tokens=10,
        total_completion_tokens=5,
        fell_back=False,
    )


# ── sequential correctness (baseline) ─────────────────────────────────────────

def test_sequential_append_and_snapshot():
    log = DecisionLog()
    assert len(log) == 0
    assert log.last is None
    assert log.records == ()

    r1 = _record(served_tier="fast")
    r2 = _record(served_tier="cloud")
    log.record(r1)
    log.record(r2)

    assert len(log) == 2
    assert log.last is r2
    snap = log.records
    assert snap == (r1, r2)

    # Snapshot is a fresh tuple; mutating it does not touch the log.
    assert isinstance(snap, tuple)


def test_records_snapshot_is_independent():
    """records returns a tuple copy; appending after the snapshot does not change it."""
    log = DecisionLog()
    log.record(_record("a"))
    snap1 = log.records
    log.record(_record("b"))
    assert len(snap1) == 1   # stale snapshot unaffected
    assert len(log) == 2


# ── concurrent-append integrity (Fix 1) ───────────────────────────────────────

def test_concurrent_appends_no_lost_records():
    """N threads each appending M records must all land; no record may be lost."""
    N_THREADS = 20
    M_PER_THREAD = 50

    log = DecisionLog()
    barrier = threading.Barrier(N_THREADS)
    errors: list[Exception] = []

    def worker(thread_idx: int) -> None:
        try:
            barrier.wait()  # all threads start appending at the same moment
            for i in range(M_PER_THREAD):
                log.record(_record(work_class=f"wc-{thread_idx}-{i}",
                                   served_tier=f"t{thread_idx}"))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"thread errors: {errors}"
    expected = N_THREADS * M_PER_THREAD
    assert len(log) == expected, f"expected {expected} records, got {len(log)}"


def test_concurrent_appends_record_integrity():
    """Each appended record's fields must be exactly what was inserted (no corruption)."""
    N_THREADS = 10
    M_PER_THREAD = 30

    log = DecisionLog()
    barrier = threading.Barrier(N_THREADS)

    def worker(thread_idx: int) -> None:
        barrier.wait()
        for i in range(M_PER_THREAD):
            log.record(_record(work_class=f"class-{thread_idx}-{i}"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = log.records
    assert len(snap) == N_THREADS * M_PER_THREAD

    # Every work_class in the snapshot must be one we actually inserted.
    seen = {r.work_class for r in snap}
    expected = {
        f"class-{ti}-{mi}"
        for ti in range(N_THREADS)
        for mi in range(M_PER_THREAD)
    }
    assert seen == expected, f"corrupt or missing records; diff={seen.symmetric_difference(expected)}"


def test_concurrent_reads_while_appending():
    """Concurrent readers (last / records / len) must never raise during appending."""
    N_WRITERS = 8
    N_READERS = 8
    M_PER_WRITER = 40

    log = DecisionLog()
    done = threading.Event()
    read_errors: list[Exception] = []

    def reader() -> None:
        while not done.is_set():
            try:
                _ = log.last
                _ = log.records
                _ = len(log)
            except Exception as e:  # noqa: BLE001
                read_errors.append(e)
                break

    def writer(thread_idx: int) -> None:
        for i in range(M_PER_WRITER):
            log.record(_record(work_class=f"w{thread_idx}-{i}"))

    readers = [threading.Thread(target=reader) for _ in range(N_READERS)]
    writers = [threading.Thread(target=writer, args=(i,)) for i in range(N_WRITERS)]
    for t in readers:
        t.start()
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    done.set()
    for t in readers:
        t.join()

    assert read_errors == [], f"reader errors during concurrent appends: {read_errors}"
    assert len(log) == N_WRITERS * M_PER_WRITER


# ── active serving mode threads onto the record (flexibility:T013) ─────────────

def test_decision_record_defaults_mode_none():
    """A record built without a mode reads mode=None — existing records unchanged."""
    rec = _record()
    assert rec.mode is None
    # Frozen + hashable is preserved with the new optional field.
    assert hash(rec) == hash(rec)


def test_decision_record_carries_mode():
    """The active serving mode is stamped onto the record when provided."""
    rec = DecisionRecord(
        work_class="bounded-edit",
        requested_tiers=("fast-local",),
        attempts=(_attempt("fast-local"),),
        served_tier="fast-local",
        total_prompt_tokens=10,
        total_completion_tokens=5,
        fell_back=False,
        mode="flexibility",
    )
    assert rec.mode == "flexibility"
    assert hash(rec) == hash(rec)


def test_mode_threads_through_route_with_fallback_into_the_log():
    """End-to-end: route_with_fallback stamps the active mode onto the emitted
    DecisionRecord (and thus the DecisionLog); None leaves it unchanged."""
    from anvil_serving.router.backends import StaticBackend
    from anvil_serving.router.config import RouterConfig, Tier
    from anvil_serving.router.fallback import route_with_fallback
    from anvil_serving.router.internal import InternalRequest, Message

    def _tier(tier_id: str) -> Tier:
        return Tier(
            id=tier_id,
            base_url="https://example.test",
            dialect="openai",
            context_limit=32_000,
            privacy="local",
            tool_support=True,
            auth_env="ANVIL_TEST_KEY",
        )

    config = RouterConfig(tiers=(_tier("fast-local"),), presets={}, mapping_version="test")

    class _Decision:
        tiers = ("fast-local",)
        work_class = "chat"

    request = InternalRequest(
        model="anvil/chat",
        system="You are helpful",
        messages=[Message("user", "hello there, please answer")],
    )
    passing = StaticBackend(["Here", " is", " the", " answer"])

    # mode threaded -> stamped onto the record + the logged copy.
    log = DecisionLog()
    result = route_with_fallback(
        request, _Decision(), config, lambda tier: passing, log=log, mode="flexibility"
    )
    assert result.served_tier == "fast-local"
    assert result.record.mode == "flexibility"
    assert log.last.mode == "flexibility"

    # mode omitted -> record identical to pre-T013 (mode is None).
    result_none = route_with_fallback(
        request, _Decision(), config, lambda tier: passing
    )
    assert result_none.record.mode is None


def test_summarize_decisions_is_metadata_only_and_redacts_secret_shaped_values():
    records = [{
        "intent": "chat",
        "work_class": "bounded-edit",
        "requested_tiers": ["fast-local", "cloud"],
        "served_tier": "cloud",
        "fell_back": True,
        "total_prompt_tokens": 40,
        "total_completion_tokens": 12,
        "cost_usd": 0.001,
        "prompt": "please leak this prompt",
        "api_key": "sk-proj-secret",
        "attempts": [
            {
                "tier_id": "fast-local",
                "outcome": "fallback",
                "verifier_passed": False,
                "verify_reason": "bearer super-secret-token",
                "prompt_tokens": 20,
                "completion_tokens": 6,
                "detail": "raw output should not surface",
            },
            {
                "tier_id": "cloud",
                "outcome": "served",
                "verifier_passed": True,
                "verify_reason": "ok",
                "prompt_tokens": 20,
                "completion_tokens": 6,
            },
        ],
    }]
    summary = summarize_decisions(records)
    rendered = str(summary)
    assert summary["count"] == 1
    assert summary["totals"]["fallback_count"] == 1
    assert summary["records"][0]["attempts"][0]["verify_reason"] == "<redacted>"
    assert "please leak" not in rendered
    assert "sk-proj-secret" not in rendered
    assert "raw output" not in rendered


def test_decision_log_summary_uses_recent_limit():
    log = DecisionLog()
    log.record(_record(work_class="a", served_tier="fast"))
    log.record(_record(work_class="b", served_tier="heavy"))
    summary = log.summary(limit=1)
    assert summary["available"] == 2
    assert summary["count"] == 1
    assert summary["records"][0]["work_class"] == "b"
