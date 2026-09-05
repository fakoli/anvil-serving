from __future__ import annotations

from collections.abc import Sequence
import threading
import time

import pytest

from anvil_serving.router.admission import TierAdmission
from anvil_serving.router.availability import AvailabilityResult


def _ready(value=True):
    return AvailabilityResult(value, "ready" if value else "unavailable", "test")


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
    waiter = threading.Thread(target=lambda: result.update(admission.wait_for_drain("heavy", 1.0)))
    waiter.start()
    time.sleep(0.02)
    assert waiter.is_alive()
    lease.release()
    waiter.join(0.5)

    assert result["drained"] is True
    assert result["timed_out"] is False
    assert result["snapshot"]["draining"] is False


def test_readmit_is_rejected_while_drain_barrier_is_active():
    admission = TierAdmission(["heavy"])
    lease = admission.acquire("heavy")
    assert lease is not None
    admission.quiesce("heavy")
    result = {}
    waiter = threading.Thread(target=lambda: result.update(admission.wait_for_drain("heavy", 1.0)))
    waiter.start()
    for _ in range(100):
        if admission.snapshot("heavy").draining:
            break
        time.sleep(0.001)
    assert admission.snapshot("heavy").draining is True
    with pytest.raises(ValueError, match="drain is in progress"):
        admission.readmit("heavy")
    lease.release()
    waiter.join(0.5)
    assert result["drained"] is True


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


def test_member_admission_rotates_full_membership_and_releases_once():
    admission = TierAdmission(["replica"], replica_members={"replica": ["c", "a", "b"]})
    readiness = {"a": _ready(False), "b": _ready(), "c": _ready()}
    first = admission.acquire_member("replica", readiness)
    second = admission.acquire_member("replica", readiness)
    assert (first.member_id, second.member_id) == ("b", "c")
    first.release()
    first.close()
    second.release()
    recovered = admission.acquire_member("replica", {"a": _ready(), "b": _ready(), "c": _ready()})
    assert recovered.member_id == "a"
    snapshot = admission.snapshot("replica")
    assert snapshot.active_requests == sum(dict(snapshot.member_active_requests).values()) == 1
    recovered.release()
    assert admission.snapshot("replica").as_dict()["member_active_requests"] == {
        "a": 0,
        "b": 0,
        "c": 0,
    }


def test_member_admission_refuses_cross_api_and_malformed_readiness_without_cursor_change():
    admission = TierAdmission(["direct", "replica"], replica_members={"replica": ["a", "b"]})
    assert admission.acquire("replica") is None
    assert admission.acquire_member("direct", {}) is None
    malformed = {"a": _ready(), "b": object()}
    assert admission.acquire_member("replica", malformed) is None
    assert admission.acquire_member("replica", {"a": _ready()}) is None
    lease = admission.acquire_member("replica", {"a": _ready(), "b": _ready()})
    assert lease.member_id == "a"
    lease.release()


def test_direct_snapshot_projection_is_byte_for_byte_compatible():
    admission = TierAdmission(["direct"])
    assert admission.snapshot("direct").as_dict() == {
        "tier_id": "direct",
        "state": "admitting",
        "reason": "admitting",
        "active_requests": 0,
        "draining": False,
    }
    assert [snapshot.tier_id for snapshot in TierAdmission(["z", "a"]).snapshots()] == ["z", "a"]


def test_member_admission_constructor_copies_and_validates_membership():
    members = ["b", "a"]
    admission = TierAdmission(["replica"], replica_members={"replica": members})
    members.append("c")
    assert admission.snapshot("replica").as_dict()["member_active_requests"] == {"a": 0, "b": 0}
    for invalid in ("a", b"a", ["a"], {"unknown": ["a", "b"]}):
        with pytest.raises(ValueError):
            TierAdmission(["replica"], replica_members=invalid)


def test_replica_member_id_matches_config_64_character_boundary():
    admission = TierAdmission(["replica"], replica_members={"replica": ["a" * 64, "b"]})
    assert admission.snapshot("replica").member_active_requests[0][0] == "a" * 64
    with pytest.raises(ValueError):
        TierAdmission(["replica"], replica_members={"replica": ["a" * 65, "b"]})


def test_member_quiesce_race_is_counted_or_rejected_and_other_tier_progresses():
    admission = TierAdmission(["a", "b"], replica_members={"a": ["one", "two"]})
    barrier = threading.Barrier(2)
    result = []

    def acquire_member():
        barrier.wait()
        result.append(admission.acquire_member("a", {"one": _ready(), "two": _ready()}))

    thread = threading.Thread(target=acquire_member)
    thread.start()
    barrier.wait()
    admission.quiesce("a")
    thread.join(0.5)
    direct = admission.acquire("b")
    assert direct is not None
    direct.release()
    if result[0] is not None:
        result[0].release()
    snapshot = admission.snapshot("a")
    assert snapshot.active_requests == sum(dict(snapshot.member_active_requests).values())


def test_member_release_detects_divergence_before_decrementing():
    admission = TierAdmission(["replica"], replica_members={"replica": ["a", "b"]})
    lease = admission.acquire_member("replica", {"a": _ready(), "b": _ready()})
    state = admission._tiers["replica"]
    state.member_active_requests[lease.member_id] = 0
    with pytest.raises(RuntimeError, match="admission_member_count_invariant"):
        lease.release()
    assert state.active_requests == 1


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


def test_concurrent_member_leases_keep_aggregate_equal_to_members():
    admission = TierAdmission(["replica"], replica_members={"replica": ["a", "b", "c"]})
    ready = {"a": _ready(), "b": _ready(), "c": _ready()}
    start = threading.Barrier(4)
    release = threading.Event()
    all_acquired = threading.Event()
    lease_lock = threading.Lock()
    leases = []

    def acquire():
        start.wait()
        lease = admission.acquire_member("replica", ready)
        with lease_lock:
            leases.append(lease)
            if len(leases) == 3:
                all_acquired.set()
        release.wait(1)
        lease.release()

    threads = [threading.Thread(target=acquire) for _ in range(3)]
    for thread in threads:
        thread.start()
    start.wait()
    assert all_acquired.wait(0.5)
    snapshot = admission.snapshot("replica")
    assert snapshot.active_requests == sum(dict(snapshot.member_active_requests).values()) == 3
    assert dict(snapshot.member_active_requests) == {"a": 1, "b": 1, "c": 1}
    assert {lease.member_id for lease in leases} == {"a", "b", "c"}
    release.set()
    for thread in threads:
        thread.join(0.5)
        assert not thread.is_alive()
    assert admission.snapshot("replica").active_requests == 0


def test_blocked_replica_drain_does_not_block_another_tier():
    admission = TierAdmission(["a", "b"], replica_members={"a": ["one", "two"]})
    lease = admission.acquire_member("a", {"one": _ready(), "two": _ready()})
    admission.quiesce("a")
    entered = threading.Event()
    result = {}
    original_wait = admission._conditions["a"].wait

    def signal_wait(timeout):
        entered.set()
        return original_wait(timeout)

    admission._conditions["a"].wait = signal_wait

    def drain():
        result.update(admission.wait_for_drain("a", 1.0))

    thread = threading.Thread(target=drain)
    thread.start()
    assert entered.wait(0.5)
    direct = admission.acquire("b")
    assert direct is not None
    direct.release()
    lease.release()
    thread.join(0.5)
    assert not thread.is_alive()
    assert result["drained"] is True
