"""Typed-seam catalog + registry tests (harness-router:T011).

Pins the two acceptance criteria and the registry's contract surface:

* **AC1** — every seam in the :mod:`~anvil_serving.router.seams` catalog has at
  least one implementation in :func:`~anvil_serving.router.registry.default_registry`,
  resolvable BY NAME, and each resolved impl ``isinstance``-satisfies the seam's
  ``runtime_checkable`` ``Protocol``. The catalog is ENUMERATED, so adding a seam
  without seeding an impl fails this test by construction.
* **AC2** — failure isolation: a verifier whose ``verify`` RAISES is converted to
  a failing :class:`~anvil_serving.router.verify.VerifyResult` by ``safe_verify``
  (no exception escapes), and when wrapped into the T009 ``route_with_fallback``
  walk the request FALLS BACK rather than crashing — the throwing tier is recorded
  as a failed attempt.

Plus registry hygiene (unknown seam/name errors, read-only ``implementations``)
and the single-source-of-truth identity of the re-exported Protocols.

Hermetic, stdlib + pytest only; no network, no GPU.
"""
from __future__ import annotations

import threading
from typing import Iterator

import pytest

from anvil_serving.router import internal as internal_mod
from anvil_serving.router import seams
from anvil_serving.router import verify as verify_mod
from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.decision_log import DecisionLog, DecisionRecord
from anvil_serving.router.dialects import Dialect as dialects_Dialect
from anvil_serving.router.fallback import RoutingDecision, route_with_fallback
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.registry import (
    Registry,
    RegistryError,
    UnknownImplementationError,
    UnknownSeamError,
    default_registry,
    safe_call,
    safe_verify,
    wrap_verifier,
)
from anvil_serving.router.seams import SEAM_NAMES, SEAMS
from anvil_serving.router.verify import ResponseView, VerifyResult


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class ThrowingVerifier:
    """A verifier whose ``verify`` always raises — the AC2 fault to isolate."""

    name = "throwing"

    def verify(self, response: ResponseView) -> VerifyResult:
        raise RuntimeError("verifier blew up (and may echo content)")


def make_tier(tier_id: str, privacy: str) -> Tier:
    return Tier(
        id=tier_id,
        base_url="https://example.test",
        dialect="anthropic" if privacy == "cloud" else "openai",
        context_limit=32_000,
        privacy=privacy,
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
    )


def make_config(*tiers: Tier) -> RouterConfig:
    return RouterConfig(tiers=tuple(tiers), presets={}, mapping_version="test")


def make_request(text: str = "fix the parser") -> InternalRequest:
    return InternalRequest(model="anvil/quick-edit", messages=[Message("user", text)])


# --------------------------------------------------------------------------- #
# AC1: every catalogued seam has >=1 impl that satisfies its Protocol
# --------------------------------------------------------------------------- #
def test_catalog_is_non_empty_and_matches_seam_names():
    # The two views of the catalog agree, and it actually contains the 7 stages.
    assert tuple(SEAMS) == SEAM_NAMES
    assert set(SEAM_NAMES) == {
        "dialect",
        "classifier",
        "routing_policy",
        "backend",
        "verifier",
        "profile_store",
        "observer",
    }


def test_every_seam_has_an_impl_satisfying_its_protocol():
    reg = default_registry()
    for seam_name, spec in SEAMS.items():
        impl_names = reg.names(seam_name)
        # AC1: at least one registered implementation per seam. Enumerated, so a
        # newly-added seam with no impl fails HERE.
        assert impl_names, f"seam {seam_name!r} has no registered implementation"
        for impl_name in impl_names:
            impl = reg.resolve(seam_name, impl_name)
            assert isinstance(impl, spec.protocol), (
                f"{seam_name}/{impl_name} does not satisfy "
                f"{spec.protocol.__name__}"
            )


def test_seam_specs_declare_a_plane_and_are_early():
    # Each Protocol is catalogued with a control/data/cross plane (§10) and the
    # current catalog is all early (M0-M2).
    for spec in SEAMS.values():
        assert spec.plane in {"data", "control", "cross"}
        assert spec.early is True
        assert spec.summary  # a human-readable one-liner is present


# --------------------------------------------------------------------------- #
# single source of truth: the re-exported Protocols are the SAME objects
# --------------------------------------------------------------------------- #
def test_reexported_protocols_are_identical_objects():
    assert seams.Backend is internal_mod.Backend
    assert seams.Verifier is verify_mod.Verifier
    assert seams.Dialect is dialects_Dialect
    # ...and the catalog points at those very objects (no shadow redefinition).
    assert SEAMS["backend"].protocol is internal_mod.Backend
    assert SEAMS["verifier"].protocol is verify_mod.Verifier
    assert SEAMS["dialect"].protocol is dialects_Dialect


# --------------------------------------------------------------------------- #
# AC2: a throwing verifier is isolated into a verify-FAIL, never a crash
# --------------------------------------------------------------------------- #
def test_safe_verify_converts_a_raise_into_a_failing_result():
    result = safe_verify(ThrowingVerifier(), ResponseView(text="anything"))
    assert isinstance(result, VerifyResult)
    assert result.passed is False
    assert result.score == 0.0
    # Content-free reason: the verifier name + exception TYPE only (R012) — never
    # the raised message body.
    assert result.verifier == "throwing"
    assert "RuntimeError" in result.reason
    assert "blew up" not in result.reason


def test_safe_verify_passes_a_good_result_through_unchanged():
    good = verify_mod.NonEmptyContent()
    result = safe_verify(good, ResponseView(text="hello world"))
    assert result.passed is True
    assert result.verifier == "non_empty_content"


def test_safe_verify_timeout_budget_returns_a_fail_not_a_hang():
    # Contract rule 2 (latency budget): a verify that blocks past its budget is a
    # FAIL, returned promptly. The gate makes this deterministic (no sleep-timing
    # race): the worker blocks until released, the budget fires first, then we
    # release the worker for a clean shutdown.
    gate = threading.Event()

    class BlockingVerifier:
        name = "blocking"

        def verify(self, response: ResponseView) -> VerifyResult:
            gate.wait(5.0)  # released by the test; 5s is a safety ceiling
            return VerifyResult(self.name, True, 1.0, "eventually ok")

    try:
        result = safe_verify(BlockingVerifier(), ResponseView(text="x"), timeout=0.05)
        assert result.passed is False
        assert "budget" in result.reason
    finally:
        gate.set()  # let the worker thread finish and exit cleanly


def test_wrapped_throwing_verifier_falls_back_instead_of_crashing():
    # AC2 end-to-end: wrap the throwing verifier and run the T009 fallback walk.
    # No exception escapes; the throwing tier is a failed attempt and the router
    # escalates (both tiers use the same throwing check, so it exhausts cleanly).
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")
    wrapped = wrap_verifier(ThrowingVerifier())

    # A non-empty backend: the response itself is fine; only the throwing verifier
    # makes it "fail", proving the FAULT (not the content) drives the fallback.
    backend = StaticBackend(["Here", " is", " an", " answer"])

    result = route_with_fallback(
        make_request(), decision, config, lambda tier: backend, verifiers=[wrapped]
    )

    # The throwing verifier became a verify-fail -> the first tier fell back.
    assert result.record.attempts[0].outcome == "fallback"
    assert result.record.attempts[0].verifier_passed is False
    # Every candidate fails the same throwing check -> exhausted, but NO crash.
    assert result.exhausted is True
    assert result.served_tier is None
    assert tuple(a.tier_id for a in result.record.attempts) == ("fast-local", "cloud")


def test_wrapped_verifier_satisfies_the_verifier_protocol():
    wrapped = wrap_verifier(ThrowingVerifier())
    assert isinstance(wrapped, seams.Verifier)
    assert wrapped.name == "throwing"  # carries the inner name for the audit trail


def test_safe_call_isolates_a_data_plane_seam_fault():
    def boom():
        raise ValueError("kaboom")

    sentinel = object()
    out = safe_call(boom, on_error=lambda exc: sentinel)
    assert out is sentinel
    # the happy path passes the return value through
    assert safe_call(lambda a, b: a + b, 2, 3, on_error=lambda exc: None) == 5


# --------------------------------------------------------------------------- #
# registry hygiene
# --------------------------------------------------------------------------- #
def test_resolve_unknown_seam_raises_clear_keyerror():
    reg = default_registry()
    with pytest.raises(UnknownSeamError) as ei:
        reg.resolve("not_a_seam", "whatever")
    assert isinstance(ei.value, KeyError)  # KeyError-derived
    assert "not_a_seam" in str(ei.value)


def test_resolve_unknown_name_raises_and_names_registered():
    reg = default_registry()
    with pytest.raises(UnknownImplementationError) as ei:
        reg.resolve("backend", "nope")
    assert isinstance(ei.value, RegistryError)
    msg = str(ei.value)
    assert "nope" in msg and "backend" in msg
    assert "static" in msg  # the error names what IS registered


def test_register_rejects_an_unknown_seam():
    reg = Registry()
    with pytest.raises(UnknownSeamError):
        reg.register("ghost_seam", "x", object())


def test_register_and_resolve_round_trips_by_name():
    reg = Registry()
    impl = StaticBackend(["x"])
    assert reg.register("backend", "mine", impl) is impl  # returns the impl
    assert reg.resolve("backend", "mine") is impl
    assert "mine" in reg.names("backend")


def test_implementations_view_is_read_only():
    reg = default_registry()
    impls = reg.implementations("backend")
    assert "static" in impls and "echo" in impls
    with pytest.raises(TypeError):
        impls["evil"] = object()  # MappingProxyType is immutable
    # ...and the view is a copy: mutating it can't reach the registry's bucket.
    assert "evil" not in reg.names("backend")


def test_names_and_implementations_agree():
    reg = default_registry()
    for seam_name in SEAM_NAMES:
        assert set(reg.names(seam_name)) == set(reg.implementations(seam_name))


def test_observer_seam_records_via_the_decision_log_adapter():
    # The Observer adapter forwards observe() -> DecisionLog.record(), so a
    # routed-decision record lands in the backing log.
    reg = default_registry()
    observer = reg.resolve("observer", "decision_log")
    rec = DecisionRecord(
        work_class="chat",
        requested_tiers=("cloud",),
        attempts=(),
        served_tier="cloud",
        total_prompt_tokens=1,
        total_completion_tokens=1,
        fell_back=False,
    )
    observer.observe(rec)
    assert isinstance(observer.log, DecisionLog)
    assert observer.log.last is rec


# --------------------------------------------------------------------------- #
# adapters: the shipped function impls resolve and behave through the seam
# --------------------------------------------------------------------------- #
def test_classifier_adapter_resolves_and_classifies():
    reg = default_registry()
    clf = reg.resolve("classifier", "heuristic")
    assert isinstance(clf, seams.Classifier)
    out = clf.classify(make_request("please refactor across the codebase"))
    assert out.work_class in {
        "chat",
        "bounded-edit",
        "multi-file-refactor",
        "planning",
        "review",
        "long-context",
    }


def test_routing_policy_adapter_resolves_and_routes():
    import pathlib

    from anvil_serving.router.config import load as load_config
    from anvil_serving.router.intent import resolve as resolve_intent
    from anvil_serving.router.profile_store import default_profile

    reg = default_registry()
    policy = reg.resolve("routing_policy", "residency-aware")
    assert isinstance(policy, seams.RoutingPolicy)

    example = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example.toml"
    cfg = load_config(str(example))
    req = InternalRequest(model="quick-edit", messages=[Message("user", "fix the bug")])
    decision = policy.route(resolve_intent(req, cfg), cfg, default_profile())
    # A real RoutingDecision drops out of the adapter (duck-typed .tiers).
    assert hasattr(decision, "tiers")


def test_profile_store_seam_impl_decides_and_scores():
    reg = default_registry()
    store = reg.resolve("profile_store", "default")
    assert isinstance(store, seams.ProfileStore)
    # planning must DENY on a local tier (the eval gate), and score is a float.
    assert store.decision("fast-local", "planning", is_cloud=False) == "deny"
    assert isinstance(store.score("fast-local", "planning"), float)
