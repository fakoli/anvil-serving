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

import pytest

from anvil_serving.router import internal as internal_mod
from anvil_serving.router import seams
from anvil_serving.router import verify as verify_mod
from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.classify import Classification
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
    # The two views of the catalog agree, including runtime availability.
    assert tuple(SEAMS) == SEAM_NAMES
    assert set(SEAM_NAMES) == {
        "dialect",
        "classifier",
        "routing_policy",
        "backend",
        "verifier",
        "profile_store",
        "availability_store",
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
# DIRECT failure-isolation: the boundary must never raise/hang for ANY fault
# shape. These hit safe_verify / wrap_verifier head-on, NOT through
# route_with_fallback (whose run_verifiers backstop would mask a wrap_verifier
# regression) — so a hole in the wrapper itself is caught here.
# --------------------------------------------------------------------------- #
def test_safe_verify_isolates_a_raise_directly_at_the_boundary():
    # The RAISE path, exercised straight through safe_verify (not the fallback
    # walk): a fail with the ExcType name only — never the message body (R012).
    result = safe_verify(ThrowingVerifier(), ResponseView(text="x"))
    assert isinstance(result, VerifyResult)
    assert result.passed is False and result.score == 0.0
    assert "RuntimeError" in result.reason
    assert "blew up" not in result.reason  # no raised-message body leaks (R012)


def test_safe_verify_survives_a_name_property_that_raises():
    # Name resolution is part of the boundary: a `.name` that raises ANY type must
    # not crash safe_verify. getattr() re-raises a non-AttributeError (the default
    # only covers a MISSING attr), so it lands on the constant fallback "verifier"
    # and still returns a fail (here verify also blows up, so the fail is
    # deterministic) — no exception escapes the boundary.
    class NameAndVerifyExplode:
        @property
        def name(self) -> str:
            raise RuntimeError("name property exploded")

        def verify(self, response: ResponseView) -> VerifyResult:
            raise ValueError("verify exploded too")

    result = safe_verify(NameAndVerifyExplode(), ResponseView(text="x"))
    assert result.passed is False
    assert result.verifier == "verifier"  # the never-raises constant fallback
    assert "ValueError" in result.reason
    assert "exploded" not in result.reason  # neither message body leaks (R012)


def test_safe_verify_rejects_a_non_verifyresult_return():
    # A verifier may return the WRONG TYPE (None / str / tuple). safe_verify must
    # convert that to a failing VerifyResult, so no downstream .passed/.score
    # access AttributeErrors on a bad shape.
    class ReturnsNone:
        name = "returns_none"

        def verify(self, response: ResponseView):
            return None

    class ReturnsStr:
        name = "returns_str"

        def verify(self, response: ResponseView):
            return "looks fine to me"

    r_none = safe_verify(ReturnsNone(), ResponseView(text="x"))
    assert r_none.passed is False
    assert "non-VerifyResult" in r_none.reason and "NoneType" in r_none.reason

    r_str = safe_verify(ReturnsStr(), ResponseView(text="x"))
    assert r_str.passed is False
    assert "non-VerifyResult" in r_str.reason and "str" in r_str.reason


def test_safe_verify_timeout_mode_isolates_a_missing_verify_method():
    # The timeout-mode guard: a verifier MISSING .verify entirely (AttributeError
    # raised INSIDE the worker) must still produce a fail, not a crash.
    class NoVerifyMethod:
        name = "no_verify"

    result = safe_verify(NoVerifyMethod(), ResponseView(text="x"), timeout=0.5)
    assert result.passed is False
    assert result.verifier == "no_verify"
    assert "AttributeError" in result.reason


def test_safe_verify_abandons_a_genuinely_hung_worker_quickly():
    # A verify that HANGS forever must return a budget-overrun fail PROMPTLY and
    # the daemon worker is abandoned (the interpreter-exit test pins that it does
    # not block exit). Deterministic: the worker blocks on an Event the test only
    # sets in `finally`, so there is no sleep-timing race.
    release = threading.Event()

    class HangingVerifier:
        name = "hanging"

        def verify(self, response: ResponseView) -> VerifyResult:
            release.wait()  # blocks until the test releases it; daemon = abandoned
            return VerifyResult(self.name, True, 1.0, "eventually done")

    try:
        result = safe_verify(HangingVerifier(), ResponseView(text="x"), timeout=0.05)
        assert result.passed is False
        assert result.verifier == "hanging"
        assert "budget" in result.reason
    finally:
        release.set()  # release the daemon worker for a clean shutdown


def test_safe_verify_distinguishes_a_raised_timeouterror_from_a_budget_overrun():
    # finding #5: a verifier that itself raises builtin TimeoutError COMPLETES
    # (it does not hang), so the reason must be "raised: TimeoutError", NOT a
    # budget overrun — the thread-join model has no concurrent.futures ambiguity
    # where both would surface as the same TimeoutError.
    class RaisesTimeoutError:
        name = "timeout_raiser"

        def verify(self, response: ResponseView) -> VerifyResult:
            raise TimeoutError("I raised this one myself")

    result = safe_verify(RaisesTimeoutError(), ResponseView(text="x"), timeout=0.5)
    assert result.passed is False
    assert "raised: TimeoutError" in result.reason
    assert "budget" not in result.reason
    assert "myself" not in result.reason  # no message body (R012)


def test_wrap_verifier_inherits_the_bad_return_guard_not_just_the_throw_path():
    # wrap_verifier delegates to safe_verify, so the WRAPPED verifier inherits the
    # non-VerifyResult guard too — catchable only by hitting the wrapper directly
    # (route_with_fallback's own run_verifiers backstop would otherwise mask it).
    class ReturnsTuple:
        name = "returns_tuple"

        def verify(self, response: ResponseView):
            return (True, 1.0, "not a VerifyResult")

    wrapped = wrap_verifier(ReturnsTuple())
    assert wrapped.name == "returns_tuple"
    out = wrapped.verify(ResponseView(text="x"))
    assert isinstance(out, VerifyResult)
    assert out.passed is False
    assert "non-VerifyResult" in out.reason and "tuple" in out.reason


# --------------------------------------------------------------------------- #
# SIGNATURE conformance (finding #6): isinstance(runtime_checkable Protocol) only
# checks attribute NAMES, not signatures — so CALL each adapter seam with its real
# argument shape and assert the RETURN behaves. A future impl whose method name
# matches but signature/return does not would fail HERE, where the Protocol
# isinstance check in AC1 would still pass.
# --------------------------------------------------------------------------- #
def _example_config():
    import pathlib

    from anvil_serving.router.config import load as load_config

    example = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example.toml"
    return load_config(str(example))


def test_adapter_seams_conform_to_signatures_not_just_names():
    from anvil_serving.router.intent import resolve as resolve_intent
    from anvil_serving.router.profile_store import default_profile

    reg = default_registry()
    cfg = _example_config()
    profile = default_profile()
    req = make_request("fix the bug")

    # Classifier.classify(request) -> a real Classification with a str work_class.
    clf = reg.resolve("classifier", "heuristic")
    classification = clf.classify(req)
    assert isinstance(classification, Classification)
    assert isinstance(classification.work_class, str)

    # RoutingPolicy.route(intent, config, profile) -> something with .tiers (a
    # tuple of tier-id strings). Built from the real intent-resolution args.
    policy = reg.resolve("routing_policy", "residency-aware")
    intent = resolve_intent(req, cfg)
    decision = policy.route(intent, cfg, profile)
    assert hasattr(decision, "tiers")
    assert all(isinstance(t, str) for t in decision.tiers)

    # ProfileStore.decision(tier, work_class, is_cloud=...) -> str, both branches.
    store = reg.resolve("profile_store", "default")
    d_local = store.decision("fast-local", "planning", is_cloud=False)
    d_cloud = store.decision("cloud", "planning", is_cloud=True)
    assert isinstance(d_local, str) and isinstance(d_cloud, str)
    assert d_local == "deny" and d_cloud == "allow"  # the eval gate, exercised live

    # Observer.observe(record) -> records into the backing log (a real side effect).
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
