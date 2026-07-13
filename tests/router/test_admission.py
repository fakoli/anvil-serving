from __future__ import annotations

import threading
import time

import pytest

from anvil_serving.router.admission import TierAdmission


def test_quiesce_blocks_late_leases_and_keeps_existing_counted():
    admission = TierAdmission(["heavy"])
    lease = admission.acquire("heavy")
    assert lease is not None

    snapshot = admission.quiesce("heavy", "promotion")

    assert snapshot.active_requests == 1
    assert admission.acquire("heavy") is None
    lease.release()
    lease.release()
    assert admission.snapshot("heavy").active_requests == 0


def test_atomic_race_is_either_counted_or_rejected():
    for _ in range(50):
        admission = TierAdmission(["heavy"])
        barrier = threading.Barrier(2)
        leases = []

        def acquire():
            barrier.wait()
            leases.append(admission.acquire("heavy"))

        thread = threading.Thread(target=acquire)
        thread.start()
        barrier.wait()
        snapshot = admission.quiesce("heavy")
        thread.join()

        lease = leases[0]
        if lease is None:
            assert snapshot.active_requests == 0
        else:
            assert admission.snapshot("heavy").active_requests == 1
            lease.release()


def test_drain_waits_for_final_release_and_wakes_promptly():
    admission = TierAdmission(["heavy"])
    lease = admission.acquire("heavy")
    assert lease is not None
    admission.quiesce("heavy")

    result = {}
    waiter = threading.Thread(
        target=lambda: result.update(admission.wait_for_drain("heavy", 1.0))
    )
    waiter.start()
    time.sleep(0.02)
    assert waiter.is_alive()
    lease.release()
    waiter.join(0.5)

    assert result["drained"] is True
    assert result["timed_out"] is False


def test_drain_timeout_does_not_change_state():
    admission = TierAdmission(["heavy"])
    lease = admission.acquire("heavy")
    assert lease is not None
    admission.quiesce("heavy", "promotion")

    result = admission.wait_for_drain("heavy", 0.01)

    assert result["timed_out"] is True
    assert admission.snapshot("heavy").state == "quiesced"
    assert admission.snapshot("heavy").active_requests == 1
    lease.release()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_drain_rejects_invalid_timeout(timeout):
    admission = TierAdmission(["heavy"])
    admission.quiesce("heavy")
    with pytest.raises(ValueError, match="timeout"):
        admission.wait_for_drain("heavy", timeout)


def test_state_change_invalidates_only_on_effective_change():
    invalidated = []
    admission = TierAdmission(["heavy"], on_state_change=invalidated.append)

    admission.quiesce("heavy", "promotion")
    admission.quiesce("heavy", "promotion")
    admission.readmit("heavy")
    admission.readmit("heavy")

    assert invalidated == ["heavy", "heavy"]


def test_status_and_reason_are_bounded():
    admission = TierAdmission(["heavy"])
    with pytest.raises(ValueError, match="content-free"):
        admission.quiesce("heavy", "upstream said secret body")
    with pytest.raises(ValueError, match="too long"):
        admission.quiesce("heavy", "x" * 129)
    with pytest.raises(KeyError, match="unknown tier"):
        admission.snapshot("missing")
