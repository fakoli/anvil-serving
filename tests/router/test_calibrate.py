"""Hermetic tests for the T016 async calibration sampler.

Proves the three load-bearing properties (no network, no real grader, stdlib
only): (1) ``observe`` returns the response BEFORE the grade is awaited — the
grade runs on a background future; (2) calibration OFF (or a 0 sample rate)
submits nothing and never calls the grader; (3) the sample is redacted (API key
masked + configured sensitive fields dropped, prompt retained) BEFORE it reaches
the grader. Plus thread-safety of the shared profile update and clean executor
shutdown.
"""
from __future__ import annotations

import concurrent.futures
import json
import threading

import pytest

from anvil_serving.router.calibrate import Calibrator, Grade
from anvil_serving.router.fingerprint import mark_stale_on_change, serve_fingerprint
from anvil_serving.router.profile_store import default_profile
from anvil_serving.router.secrets import MASK

API_KEY = "sk-ant-CALIB-SECRET-0123456789abcdef"


# ── Criterion 1: the response is returned BEFORE the grade completes ───────────
def test_observe_returns_before_grade_completes():
    store = default_profile()
    before = store.entry("fast-local", "review")

    release = threading.Event()      # held until we've proven the early return
    grade_started = threading.Event()  # set once the worker reaches the grader

    def grader(sample):
        grade_started.set()
        assert release.wait(5), "test never released the grader"
        return Grade(score=0.9)

    cal = Calibrator(
        store,
        grader=grader,
        enabled=True,
        sample_rate=1.0,
        now=lambda: "2026-06-30T00:00:00Z",
    )
    response = {"content": "ok"}
    try:
        resp = cal.observe({"messages": [{"role": "user", "content": "hi"}]},
                           response, "review", "fast-local")
        # observe handed the response straight back, synchronously.
        assert resp is response

        # The grade is demonstrably still running (parked in the grader) while we
        # execute here — observe did NOT await it.
        assert grade_started.wait(5), "background grade never started"
        assert cal.pending() == 1
        mid = store.entry("fast-local", "review")
        assert mid.sample_n == before.sample_n          # not yet recorded
        assert mid.quality_score == before.quality_score

        # Release the grade, drain, and confirm it folded into the profile.
        release.set()
        assert cal.drain(timeout=5)
        after = store.entry("fast-local", "review")
        assert after.sample_n == before.sample_n + 1
        assert after.quality_score != before.quality_score
        assert after.last_measured == "2026-06-30T00:00:00Z"
        assert cal.errors == 0
    finally:
        release.set()
        cal.close()


# ── Criterion 2: OFF (or 0-rate) samples nothing / never calls the grader ──────
def test_disabled_samples_nothing():
    store = default_profile()

    def grader(sample):  # pragma: no cover - must never run
        raise AssertionError("grader called while calibration disabled")

    cal = Calibrator(store, grader=grader, enabled=False, sample_rate=1.0)
    try:
        resp = cal.observe({"prompt": "x"}, {"content": "y"}, "review", "fast-local")
        assert resp == {"content": "y"}
        assert cal.pending() == 0
        assert cal.drain(timeout=2) is True
        assert cal.errors == 0  # grader never ran (an error would have been counted)
        assert store.entry("fast-local", "review").sample_n == 1  # untouched
    finally:
        cal.close()


def test_zero_sample_rate_samples_nothing():
    store = default_profile()

    def grader(sample):  # pragma: no cover - must never run
        raise AssertionError("grader called with sample_rate=0")

    cal = Calibrator(store, grader=grader, enabled=True, sample_rate=0.0)
    try:
        for _ in range(20):
            cal.observe({"prompt": "x"}, {"content": "y"}, "review", "fast-local")
        assert cal.pending() == 0
        assert cal.errors == 0
    finally:
        cal.close()


# ── Criterion 3: redact secrets + configured fields BEFORE the grader sees it ──
def test_redacts_secrets_and_configured_fields_before_grader():
    store = default_profile()
    seen = {}
    done = threading.Event()

    def grader(sample):
        seen["sample"] = sample
        done.set()
        return Grade(score=0.8)

    cal = Calibrator(
        store,
        grader=grader,
        enabled=True,
        sample_rate=1.0,
        redact_fields=("customer_id", "ssn"),
        secrets=(API_KEY,),
    )
    request = {
        "api_key": API_KEY,                                  # secret-named -> masked
        "headers": {"Authorization": f"Bearer {API_KEY}"},   # scrubbed substring
        "customer_id": "CUST-13371337",                      # configured -> dropped
        "ssn": "123-45-6789",                                # configured -> dropped
        "messages": [{"role": "user", "content": "Refactor the auth module please"}],
    }
    response = {"content": "Here is the refactor", "note": f"leaked {API_KEY} here"}
    try:
        cal.observe(request, response, "review", "fast-local")
        assert cal.drain(timeout=5)
        assert done.is_set()

        sample = seen["sample"]
        blob = json.dumps(sample, default=str)

        # The API key is gone everywhere (masked field + scrubbed free text).
        assert API_KEY not in blob
        req = sample["request"]
        assert MASK in req["api_key"]            # field masked, not the raw key
        assert API_KEY not in json.dumps(req["headers"])

        # Configured sensitive fields are DROPPED entirely.
        assert "customer_id" not in req
        assert "ssn" not in req

        # The prompt text is RETAINED for grading (calibration=True keeps bodies).
        assert req["messages"][0]["content"] == "Refactor the auth module please"
        assert "Refactor the auth module please" in blob
        # ...but a key leaked into the response body is still scrubbed.
        assert "leaked" in sample["response"]["note"]
        assert API_KEY not in sample["response"]["note"]
    finally:
        cal.close()


# ── thread-safety: concurrent grades must not lose an update ───────────────────
def test_concurrent_grades_no_lost_update():
    store = default_profile()
    before_n = store.entry("fast-local", "review").sample_n

    cal = Calibrator(
        store,
        grader=lambda sample: Grade(score=0.7),
        enabled=True,
        sample_rate=1.0,
        max_workers=8,
    )
    n = 200
    try:
        for _ in range(n):
            cal.observe({"prompt": "x"}, {"content": "y"}, "review", "fast-local")
        assert cal.drain(timeout=20)
        entry = store.entry("fast-local", "review")
        # Without the store lock, racing read-modify-writes would LOSE increments
        # and this count would come up short.
        assert entry.sample_n == before_n + n
        assert cal.errors == 0
        assert 0.0 <= entry.quality_score <= 1.0
    finally:
        cal.close()


# ── grade shape handling: bare float and explicit decision revision ────────────
def test_bare_float_grade_updates_score_not_decision():
    store = default_profile()
    cal = Calibrator(store, grader=lambda s: 0.95, enabled=True, sample_rate=1.0)
    try:
        cal.observe({"p": "x"}, {"c": "y"}, "review", "heavy-local")
        assert cal.drain(timeout=5)
        e = store.entry("heavy-local", "review")
        assert e.sample_n == 2
        assert e.decision == "allow"   # bare float carries no decision -> unchanged
        assert e.quality_score == pytest.approx(0.875, abs=1e-9)  # (0.80 + 0.95)/2
    finally:
        cal.close()


def test_grade_can_revise_decision_when_explicit():
    store = default_profile()
    cal = Calibrator(
        store,
        grader=lambda s: Grade(score=0.2, decision="deny"),
        enabled=True,
        sample_rate=1.0,
    )
    try:
        cal.observe({"p": "x"}, {"c": "y"}, "review", "heavy-local")
        assert cal.drain(timeout=5)
        assert store.entry("heavy-local", "review").decision == "deny"
    finally:
        cal.close()


# ── executor lifecycle: owned shut down, injected left alone ───────────────────
def test_close_shuts_down_owned_executor():
    store = default_profile()
    cal = Calibrator(store, grader=lambda s: Grade(score=0.5), enabled=True, sample_rate=1.0)
    cal.observe({"p": "x"}, {"c": "y"}, "review", "fast-local")
    assert cal.drain(timeout=5)
    cal.close()
    # After close the calibrator stops sampling (no leaked work) ...
    resp = cal.observe({"p": "x"}, {"c": "y"}, "review", "fast-local")
    assert resp == {"c": "y"}
    assert cal.pending() == 0
    # ... and the owned executor is shut down (rejects new work).
    with pytest.raises(RuntimeError):
        cal.executor.submit(lambda: 1)


def test_context_manager_shuts_down_executor():
    store = default_profile()
    with Calibrator(store, grader=lambda s: Grade(score=0.5), enabled=True, sample_rate=1.0) as cal:
        cal.observe({"p": "x"}, {"c": "y"}, "review", "fast-local")
        assert cal.drain(timeout=5)
        ex = cal.executor
    with pytest.raises(RuntimeError):
        ex.submit(lambda: 1)


def test_injected_executor_is_not_closed():
    store = default_profile()
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        cal = Calibrator(
            store,
            grader=lambda s: Grade(score=0.5),
            enabled=True,
            sample_rate=1.0,
            executor=ex,
        )
        cal.observe({"p": "x"}, {"c": "y"}, "review", "fast-local")
        assert cal.drain(timeout=5)
        cal.close()
        # The calibrator must NOT shut down an executor it doesn't own.
        fut = ex.submit(lambda: 42)
        assert fut.result(timeout=5) == 42
    finally:
        ex.shutdown(wait=True)


# ── integration: a fresh grade clears the staleness a serve change set ─────────
def test_fresh_grade_clears_stale_after_serve_change():
    store = default_profile()
    fp0 = serve_fingerprint({"id": "fast-local", "model": "a"})
    fp1 = serve_fingerprint({"id": "fast-local", "model": "b"})
    mark_stale_on_change(store, "fast-local", fp0)   # baseline
    mark_stale_on_change(store, "fast-local", fp1)   # serve changed -> stale
    assert store.is_stale("fast-local", "review") is True

    cal = Calibrator(store, grader=lambda s: Grade(score=0.9), enabled=True, sample_rate=1.0)
    try:
        cal.observe({"p": "x"}, {"c": "y"}, "review", "fast-local")
        assert cal.drain(timeout=5)
    finally:
        cal.close()

    entry = store.entry("fast-local", "review")
    assert entry.stale is False              # re-measured -> trust restored
    assert entry.fingerprint == fp1          # under the CURRENT serve identity
