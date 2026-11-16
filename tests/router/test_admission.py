from __future__ import annotations

from collections.abc import Sequence
import threading
import time

import pytest

from anvil_serving.router.admission import TierAdmission
from anvil_serving.router.availability import AvailabilityResult


def _ready(value=True):
    return AvailabilityResult(value, "ready" if value else "unavailable", "test")


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
    waiter = threading.Thread(target=lambda: result.update(admission.wait_for_drain("heavy", 1.0)))
    waiter.start()
    time.sleep(0.02)
    assert waiter.is_alive()
    lease.release()
    waiter.join(0.5)

    assert result["drained"] is True
    assert result["timed_out"] is False
    assert result["snapshot"]["draining"] is False


def test_drain_timeout_does_not_change_state():
    admission = TierAdmission(["heavy"])
    lease = admission.acquire("heavy")
    assert lease is not None
    admission.quiesce("heavy", "promotion")

    result = admission.wait_for_drain("heavy", 0.01)

    assert result["timed_out"] is True
    assert result["snapshot"]["draining"] is False
    assert admission.snapshot("heavy").state == "quiesced"
    assert admission.snapshot("heavy").active_requests == 1
    lease.release()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_drain_rejects_invalid_timeout(timeout):
    admission = TierAdmission(["heavy"])
    admission.quiesce("heavy")
    with pytest.raises(ValueError, match="timeout"):
        admission.wait_for_drain("heavy", timeout)


def test_member_inputs_are_bounded_and_hostile_readiness_does_not_advance_cursor():
    class TooManyItems(dict):
        def __len__(self):
            return 2

        def items(self):
            return iter((("replica", ["a", "b"]), ("replica", ["a", "b"]), ("extra", ["a", "b"])))

    class HostileReadiness(dict):
        def __len__(self):
            return 2

        def items(self):
            return iter((("a", _ready()), ("b", _ready()), ("extra", _ready())))

    class UnstableSequence(Sequence):
        def __len__(self):
            return 2

        def __getitem__(self, index):
            if index == 0:
                return "a"
            raise RuntimeError("must not escape")

    with pytest.raises(ValueError):
        TierAdmission(["replica", "other"], replica_members=TooManyItems())
    with pytest.raises(ValueError):
        TierAdmission(["replica"], replica_members={"replica": UnstableSequence()})

    admission = TierAdmission(["replica"], replica_members={"replica": ["a", "b"]})
    assert admission.acquire_member("replica", HostileReadiness()) is None
    result = AvailabilityResult(True, "ready", "test")
    object.__setattr__(result, "available", 1)
    assert admission.acquire_member("replica", {"a": result, "b": _ready()}) is None
    lease = admission.acquire_member("replica", {"a": _ready(), "b": _ready()})
    assert lease.member_id == "a"
    lease.release()


@pytest.mark.parametrize("kwargs", [
    {"tier_max_concurrency": {"direct": 1}},
    {"member_max_concurrency": {"direct": {"a": 1}}},
    {"member_max_concurrency": {"replica": {"unknown": 1}}},
    {"member_max_concurrency": {"replica": {"a": 100001}}},
    {"member_max_concurrency": {"replica": None}},
    {"member_max_concurrency": []},
    {"tier_max_concurrency": []},
])
def test_ceiling_configuration_refuses_wrong_ownership_and_shape(kwargs):
    with pytest.raises(ValueError):
        TierAdmission(["direct", "replica"], replica_members={"replica": ["a", "b"]}, **kwargs)


def test_member_and_tier_readmission_have_independent_scopes():
    admission = TierAdmission(["replica"], replica_members={"replica": ["a", "b"]})
    ready = {"a": _ready(), "b": _ready()}
    admission.quiesce_member("replica", "a", "maintenance")
    peer = admission.acquire_member("replica", ready)
    assert peer.member_id == "b"
    admission.quiesce("replica")
    admission.readmit_member("replica", "a")
    assert admission.snapshot("replica").quiesced
    assert admission.acquire_member("replica", ready) is None
    admission.quiesce_member("replica", "b")
    admission.readmit("replica")
    assert admission.member_snapshot("replica", "b").quiesced
    selected = admission.acquire_member("replica", ready)
    assert selected.member_id == "a"
    selected.release()
    peer.release()


def test_member_drain_timeout_preserves_lease_and_quiesce(monkeypatch):
    admission = TierAdmission(["replica"], replica_members={"replica": ["a", "b"]})
    lease = admission.acquire_member("replica", {"a": _ready(), "b": _ready()})
    with pytest.raises(ValueError, match="member must be quiesced"):
        admission.wait_for_member_drain("replica", "a", 1)
    admission.quiesce_member("replica", "a")
    times = iter([0.0, 2.0])
    monkeypatch.setattr("anvil_serving.router.admission.time.monotonic", lambda: next(times))
    result = admission.wait_for_member_drain("replica", "a", 1)
    assert result["timed_out"] and not result["drained"]
    assert result["snapshot"]["active_requests"] == 1
    assert not result["snapshot"]["draining"]
    assert admission.member_snapshot("replica", "a").quiesced
    lease.release()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "1"])
def test_member_drain_rejects_invalid_timeout(timeout):
    admission = TierAdmission(["replica"], replica_members={"replica": ["a", "b"]})
    with pytest.raises(ValueError, match="timeout"):
        admission.wait_for_member_drain("replica", "a", timeout)


def test_member_quiesce_and_acquire_race_has_one_atomic_outcome():
    for _ in range(20):
        admission = TierAdmission(["replica"], replica_members={"replica": ["a", "b"]})
        barrier = threading.Barrier(2)
        leases = []

        def acquire():
            barrier.wait(timeout=2)
            leases.append(admission.acquire_member("replica", {"a": _ready(), "b": _ready(False)}))

        thread = threading.Thread(target=acquire)
        thread.start()
        barrier.wait(timeout=2)
        snapshot = admission.quiesce_member("replica", "a")
        thread.join(2)
        assert not thread.is_alive()
        assert snapshot.active_requests == (0 if leases[0] is None else 1)
        if leases[0] is not None:
            leases[0].release()
        final = admission.snapshot("replica")
        assert final.active_requests == sum(dict(final.member_active_requests).values()) == 0


def _capacity_admission(*, tier_cap=None, on_state_change=None):
    return TierAdmission(
        ["replica"], replica_members={"replica": ["a", "b"]},
        member_max_concurrency={"replica": {"a": 2, "b": 2}},
        tier_max_concurrency={} if tier_cap is None else {"replica": tier_cap},
        replica_strategies={"replica": "capacity"}, on_state_change=on_state_change,
    )


def test_twenty_capacity_rankings_reserve_current_counts_atomically():
    admission = _capacity_admission(tier_cap=3)
    barrier = threading.Barrier(21)
    leases = []
    lock = threading.Lock()

    def acquire():
        barrier.wait(timeout=2)
        lease = admission.acquire_member("replica", {"a": _ready(), "b": _ready()})
        with lock:
            leases.append(lease)

    threads = [threading.Thread(target=acquire) for _ in range(20)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    granted = [lease for lease in leases if lease is not None]
    assert len(granted) == 3
    snapshot = admission.snapshot("replica")
    assert snapshot.active_requests == sum(dict(snapshot.member_active_requests).values()) == 3
    assert dict(snapshot.member_active_requests) == {"a": 2, "b": 1}
    for lease in granted:
        assert lease.selection is not None
        lease.release()
        lease.release()
    assert admission.snapshot("replica").active_requests == 0
