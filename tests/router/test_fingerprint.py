"""Hermetic tests for the T016 serve-fingerprint + profile-row staleness.

``serve_fingerprint`` must be a stable digest over a tier's quality-affecting
serve identity (model / endpoint / dialect / context / params) and ONLY that, so
incidental fields don't churn the profile. ``mark_stale_on_change`` must flag the
affected tier's rows stale on a fingerprint change and leave every other tier
untouched. Stdlib only, deterministic, no network.
"""
from __future__ import annotations

import threading
from types import MappingProxyType

from anvil_serving.router.config import Tier
from anvil_serving.router.fingerprint import (
    FINGERPRINT_SCHEMA,
    identity,
    mark_stale_on_change,
    refresh_fingerprint,
    serve_fingerprint,
)
from anvil_serving.router.profile_store import default_profile

# Every work class the seed profile carries for a single (local) tier.
ALL_CLASSES = {
    "planning",
    "multi-file-refactor",
    "long-context",
    "review",
    "bounded-edit",
    "chat",
    "chat-fast",  # flexibility:T018 -- voice-pipeline low-latency work class
}


# ── serve_fingerprint: deterministic, hex, identity-only ───────────────────────
def test_fingerprint_is_deterministic_hex():
    spec = {"id": "t", "model": "m", "base_url": "http://x", "dialect": "openai"}
    fp = serve_fingerprint(spec)
    assert isinstance(fp, str)
    assert len(fp) == 64
    int(fp, 16)  # valid lowercase hex
    # Identical inputs -> identical digest, across separate dicts.
    assert serve_fingerprint(dict(spec)) == fp


def test_fingerprint_changes_with_each_identity_field():
    base = {
        "id": "t",
        "model": "m",
        "base_url": "http://x",
        "dialect": "openai",
        "context_limit": 32000,
        "quantization": "awq",
    }
    ref = serve_fingerprint(base)
    for key, value in [
        ("model", "m2"),
        ("base_url", "http://y"),
        ("dialect", "anthropic"),
        ("context_limit", 8000),
        ("quantization", "fp8"),
    ]:
        changed = dict(base)
        changed[key] = value
        assert serve_fingerprint(changed) != ref, key


def test_fingerprint_ignores_non_identity_fields():
    base = {"id": "t", "model": "m", "base_url": "http://x", "dialect": "openai"}
    noisy = dict(base, latency_ms=12, load=0.93, pid=4242, replica="b7")
    # Operational churn must not invalidate the measured profile.
    assert serve_fingerprint(noisy) == serve_fingerprint(base)
    assert set(identity(noisy)) == {"tier_id", "model", "endpoint", "dialect"}


def test_fingerprint_accepts_tier_object_and_hashes_only_identity():
    t1 = Tier(
        id="fast-local",
        base_url="http://a",
        dialect="openai",
        context_limit=32000,
        privacy="local",
        tool_support=True,
        auth_env="FAST_KEY",
    )
    # A different ENDPOINT changes the fingerprint ...
    t2 = Tier(
        id="fast-local",
        base_url="http://b",
        dialect="openai",
        context_limit=32000,
        privacy="local",
        tool_support=True,
        auth_env="FAST_KEY",
    )
    assert serve_fingerprint(t1) != serve_fingerprint(t2)
    # ... but privacy / tool_support / auth_env are NOT quality identity, so
    # changing them leaves the fingerprint unmoved.
    t3 = Tier(
        id="fast-local",
        base_url="http://a",
        dialect="openai",
        context_limit=32000,
        privacy="cloud",
        tool_support=False,
        auth_env="OTHER_KEY",
    )
    assert serve_fingerprint(t3) == serve_fingerprint(t1)


# ── reasoning mode (extra_body) enters the fingerprint (flexibility:T003) ──────
def _fast_tier(**overrides):
    """A fixed fast-local Tier; ``overrides`` tweak one field for a comparison."""
    base = dict(
        id="fast-local",
        base_url="http://127.0.0.1:30001/v1",
        dialect="openai",
        context_limit=32000,
        privacy="local",
        tool_support=True,
        auth_env="FAST_KEY",
        model="qwen",
    )
    base.update(overrides)
    return Tier(**base)


def test_reasoning_extra_body_changes_fingerprint():
    """Criterion 1: a thinking-OFF serve is a DISTINCT fingerprint from the same
    tier with thinking on — and from the same tier with no reasoning config."""
    thinking_off = _fast_tier(
        extra_body=MappingProxyType({"chat_template_kwargs": {"enable_thinking": False}})
    )
    thinking_on = _fast_tier(
        extra_body=MappingProxyType({"chat_template_kwargs": {"enable_thinking": True}})
    )
    no_reasoning = _fast_tier()  # extra_body defaults to None

    fp_off = serve_fingerprint(thinking_off)
    fp_on = serve_fingerprint(thinking_on)
    fp_none = serve_fingerprint(no_reasoning)

    # thinking-OFF differs from thinking-ON and from no-extra_body: three regimes.
    assert fp_off != fp_on
    assert fp_off != fp_none
    assert fp_on != fp_none

    # It rides under the canonical ``reasoning`` identity key.
    assert "reasoning" in identity(thinking_off)
    assert "reasoning" not in identity(no_reasoning)

    # A different reasoning knob (reasoning_effort) is likewise distinct.
    assert serve_fingerprint(_fast_tier(extra_body={"reasoning_effort": "low"})) != fp_none


def test_reasoning_extra_body_is_key_order_and_container_insensitive():
    """The reasoning config is content-addressed: a MappingProxyType (as a Tier
    stores it) hashes identically to an equivalent inline dict, and reordering
    keys does not churn the digest — only the reasoning VALUES matter."""
    proxy = _fast_tier(
        extra_body=MappingProxyType({"chat_template_kwargs": {"enable_thinking": False}})
    )
    plain = _fast_tier(extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    assert serve_fingerprint(proxy) == serve_fingerprint(plain)

    order_a = _fast_tier(
        extra_body={"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "low"}
    )
    order_b = _fast_tier(
        extra_body={"reasoning_effort": "low", "chat_template_kwargs": {"enable_thinking": False}}
    )
    assert serve_fingerprint(order_a) == serve_fingerprint(order_b)


def test_reasoning_field_does_not_churn_extra_body_less_tiers():
    """Criterion 2 (no-churn): a tier that sets NO reasoning ``extra_body`` hashes
    to the exact value it did BEFORE the reasoning identity field existed. The new
    field resolves to None -> omitted from the hash -> digest byte-identical.

    The two anchors are digests captured from the pre-change code; if the reasoning
    field ever leaks into an extra_body-less digest, these constants break."""
    # A fixed dict spec with no extra_body.
    dict_spec = {
        "id": "fast-local",
        "model": "qwen",
        "base_url": "http://127.0.0.1:30001/v1",
        "dialect": "openai",
        "context_limit": 32000,
        "quantization": "nvfp4",
    }
    assert (
        serve_fingerprint(dict_spec)
        == "f5f7e692660d4efc14ce2c71b600886fd5f147a6a38e584ee0a2457e71a7ad47"
    )
    # A fixed Tier with no extra_body (extra_body=None).
    assert (
        serve_fingerprint(_fast_tier())
        == "d1cf852fc0d06741bfc1a6cd1027cd22a0eda54b9de19bb5c4e8461c197ac39e"
    )
    # And the None extra_body is simply absent from the hashed identity.
    assert "reasoning" not in identity(_fast_tier())


# ── serving engine enters the fingerprint (flexibility:T008, ADR-0010) ─────────
def test_engine_changes_fingerprint():
    """Criterion 1: two tiers identical except ``engine`` are DISTINCT fingerprints
    — an in-place engine swap (vLLM↔SGLang) at the same base_url is a different
    quality regime, so its measured rows must go stale."""
    vllm = _fast_tier(engine="vllm")
    sglang = _fast_tier(engine="sglang")
    no_engine = _fast_tier()  # engine defaults to None

    fp_vllm = serve_fingerprint(vllm)
    fp_sglang = serve_fingerprint(sglang)
    fp_none = serve_fingerprint(no_engine)

    # vLLM, SGLang, and no-engine are three distinct serve identities.
    assert fp_vllm != fp_sglang
    assert fp_vllm != fp_none
    assert fp_sglang != fp_none

    # It rides under the canonical ``engine`` identity key.
    assert identity(vllm)["engine"] == "vllm"
    assert "engine" not in identity(no_engine)

    # Works for a plain Mapping spec too, not just a Tier.
    base = {"id": "t", "model": "m", "base_url": "http://x", "dialect": "openai"}
    assert serve_fingerprint(dict(base, engine="vllm")) != serve_fingerprint(base)
    assert serve_fingerprint(dict(base, engine="vllm")) != serve_fingerprint(
        dict(base, engine="sglang")
    )


def test_engine_field_does_not_churn_engine_less_tiers():
    """Criterion 2 (no-churn): a tier that sets NO ``engine`` hashes to the exact
    value it did BEFORE the engine identity axis existed. The new field resolves to
    None -> omitted from the hash -> digest byte-identical.

    The anchors are digests captured from the pre-T008 code (they are the SAME
    engine-less/extra_body-less digests the T003 no-churn test pins); if ``engine``
    ever leaks into an engine-less digest, these constants break."""
    dict_spec = {
        "id": "fast-local",
        "model": "qwen",
        "base_url": "http://127.0.0.1:30001/v1",
        "dialect": "openai",
        "context_limit": 32000,
        "quantization": "nvfp4",
    }
    assert (
        serve_fingerprint(dict_spec)
        == "f5f7e692660d4efc14ce2c71b600886fd5f147a6a38e584ee0a2457e71a7ad47"
    )
    # A fixed Tier with no engine (engine=None).
    assert (
        serve_fingerprint(_fast_tier())
        == "d1cf852fc0d06741bfc1a6cd1027cd22a0eda54b9de19bb5c4e8461c197ac39e"
    )
    # And the None engine is simply absent from the hashed identity.
    assert "engine" not in identity(_fast_tier())


def test_engine_is_orthogonal_to_reasoning():
    """The engine axis and the reasoning axis are independent: setting only engine
    leaves the reasoning key absent, and two tiers that agree on reasoning but
    differ on engine still fingerprint differently."""
    r = MappingProxyType({"chat_template_kwargs": {"enable_thinking": False}})
    only_engine = _fast_tier(engine="vllm")
    assert "reasoning" not in identity(only_engine)
    assert "engine" in identity(only_engine)

    a = _fast_tier(engine="vllm", extra_body=r)
    b = _fast_tier(engine="sglang", extra_body=r)
    assert serve_fingerprint(a) != serve_fingerprint(b)


# ── active serving mode enters the fingerprint (flexibility:T013, ADR-0011) ────
def test_mode_changes_fingerprint():
    """Criterion 1: two serves identical except the active MODE are DISTINCT
    fingerprints — the SAME model measured in agentic vs flexibility mode is a
    distinct measured identity (composes with the ADR-0009 write-back loop)."""
    agentic = serve_fingerprint(_fast_tier(), mode="agentic")
    flexibility = serve_fingerprint(_fast_tier(), mode="flexibility")
    no_mode = serve_fingerprint(_fast_tier())  # mode unset

    # agentic, flexibility, and no-mode are three distinct serve identities.
    assert agentic != flexibility
    assert agentic != no_mode
    assert flexibility != no_mode

    # It rides under the canonical ``mode`` identity key.
    assert identity(_fast_tier(), mode="flexibility")["mode"] == "flexibility"
    assert "mode" not in identity(_fast_tier())

    # Works for a plain Mapping spec threaded via the param too, not just a Tier.
    base = {"id": "t", "model": "m", "base_url": "http://x", "dialect": "openai"}
    assert serve_fingerprint(base, mode="agentic") != serve_fingerprint(base)
    assert serve_fingerprint(base, mode="agentic") != serve_fingerprint(
        base, mode="flexibility"
    )


def test_mode_on_the_spec_is_ignored_keeping_no_churn_unconditional():
    """Mode is threaded ONLY as a keyword; a ``mode`` key carried on the spec is
    deliberately NOT resolved (it is absent from IDENTITY_FIELDS). This keeps the
    no-churn invariant UNCONDITIONAL — a mode-less call hashes identically no matter
    what keys the spec carries — closing the footgun where a serialized-tier dict
    with a stray ``mode`` key would silently change a mode-less digest."""
    base = {"id": "t", "model": "m", "base_url": "http://x", "dialect": "openai"}
    # A mode key on the spec does NOT enter the identity...
    assert "mode" not in identity(dict(base, mode="flexibility"))
    # ...so a mode-less call is byte-identical whether or not the spec carries a mode.
    assert serve_fingerprint(dict(base, mode="flexibility")) == serve_fingerprint(base)
    # Only the explicit keyword sets the mode.
    assert identity(base, mode="flexibility")["mode"] == "flexibility"
    assert serve_fingerprint(dict(base, mode="agentic"), mode="flexibility") == (
        serve_fingerprint(base, mode="flexibility")
    )


def test_mode_field_does_not_churn_mode_less_serves():
    """Criterion 2 (no-churn): a serve with mode=None/unset hashes to the exact
    value it did BEFORE the mode identity axis existed — the SAME digests the T003
    and T008 no-churn tests pin. The new field resolves to None -> omitted -> the
    digest is byte-identical. If ``mode`` ever leaks into a mode-less digest, these
    constants break."""
    dict_spec = {
        "id": "fast-local",
        "model": "qwen",
        "base_url": "http://127.0.0.1:30001/v1",
        "dialect": "openai",
        "context_limit": 32000,
        "quantization": "nvfp4",
    }
    anchor_dict = "f5f7e692660d4efc14ce2c71b600886fd5f147a6a38e584ee0a2457e71a7ad47"
    anchor_tier = "d1cf852fc0d06741bfc1a6cd1027cd22a0eda54b9de19bb5c4e8461c197ac39e"
    # Unset and an explicit mode=None are both byte-identical to pre-T013.
    assert serve_fingerprint(dict_spec) == anchor_dict
    assert serve_fingerprint(dict_spec, mode=None) == anchor_dict
    assert serve_fingerprint(_fast_tier()) == anchor_tier
    assert serve_fingerprint(_fast_tier(), mode=None) == anchor_tier
    # And the None mode is simply absent from the hashed identity.
    assert "mode" not in identity(_fast_tier())
    assert "mode" not in identity(_fast_tier(), mode=None)


def test_mode_is_orthogonal_to_engine_and_reasoning():
    """The mode axis is independent of the engine + reasoning axes: setting only
    mode leaves engine/reasoning absent, and two serves that agree on engine +
    reasoning but differ on mode still fingerprint differently."""
    r = MappingProxyType({"chat_template_kwargs": {"enable_thinking": False}})
    only_mode = identity(_fast_tier(), mode="flexibility")
    assert only_mode["mode"] == "flexibility"
    assert "engine" not in only_mode
    assert "reasoning" not in only_mode

    a = serve_fingerprint(_fast_tier(engine="vllm", extra_body=r), mode="agentic")
    b = serve_fingerprint(_fast_tier(engine="vllm", extra_body=r), mode="flexibility")
    assert a != b


def test_fingerprint_schema_untouched_by_mode_axis():
    """The identity-schema tag MUST NOT move for T013: no-churn is load-bearing, so
    a mode-less serve's digest under new code still matches an old one."""
    assert FINGERPRINT_SCHEMA == "anvil-serving.router.fingerprint/v1"


def test_refresh_fingerprint_stales_rows_on_mode_change():
    """refresh_fingerprint threads the mode into the digest: a tier measured under
    one mode goes stale when the SAME tier is next served under another mode — while
    a mode-less baseline (a --config boot) stays put."""
    store = default_profile()
    # Baseline under agentic: nothing was invalidated.
    assert refresh_fingerprint(store, "fast-local", _fast_tier(), mode="agentic") == []
    # Same tier, flexibility mode -> every fast-local row goes stale.
    newly = refresh_fingerprint(store, "fast-local", _fast_tier(), mode="flexibility")
    assert set(newly) == ALL_CLASSES
    assert store.is_stale("fast-local", "review") is True
    # Other tiers untouched.
    assert store.is_stale("heavy-local", "review") is False

    # A mode-less (--config) boot is a stable baseline: no drift, idempotent.
    store2 = default_profile()
    assert refresh_fingerprint(store2, "fast-local", _fast_tier()) == []
    assert refresh_fingerprint(store2, "fast-local", _fast_tier()) == []
    assert store2.stale_pairs() == []


# ── mark_stale_on_change: change stales the tier; others untouched ─────────────
def test_change_marks_affected_tier_rows_stale_only():
    store = default_profile()
    fp0 = serve_fingerprint({"id": "fast-local", "model": "qwen-A", "base_url": "http://x"})
    fp1 = serve_fingerprint({"id": "fast-local", "model": "qwen-B", "base_url": "http://x"})
    assert fp0 != fp1

    # First association is a baseline: nothing was invalidated.
    assert mark_stale_on_change(store, "fast-local", fp0) == []
    assert store.stale_pairs() == []

    # The change stales EVERY fast-local row ...
    newly = mark_stale_on_change(store, "fast-local", fp1)
    assert set(newly) == ALL_CLASSES
    assert all(tier == "fast-local" for (tier, _wc) in store.stale_pairs())
    assert store.is_stale("fast-local", "review") is True

    # ... and leaves other tiers completely untouched.
    assert store.is_stale("heavy-local", "review") is False
    assert store.is_stale("cloud", "planning") is False

    # Idempotent: re-applying the same fingerprint stales nothing new.
    assert mark_stale_on_change(store, "fast-local", fp1) == []


def test_baseline_then_same_fingerprint_is_noop():
    store = default_profile()
    fp = serve_fingerprint({"id": "heavy-local", "model": "m"})
    assert mark_stale_on_change(store, "heavy-local", fp) == []
    assert mark_stale_on_change(store, "heavy-local", fp) == []
    assert store.stale_pairs() == []


def test_unmeasured_pair_is_not_stale():
    store = default_profile()
    # A pair with no stored row is not "stale" — it falls through to the
    # fail-closed defaults, which already distrust the risky classes.
    assert store.is_stale("gpu0", "planning") is False


def test_refresh_fingerprint_wires_spec_to_staleness():
    store = default_profile()
    spec_a = {"id": "heavy-local", "model": "m1", "base_url": "http://h"}
    spec_b = {"id": "heavy-local", "model": "m2", "base_url": "http://h"}

    assert refresh_fingerprint(store, "heavy-local", spec_a) == []   # baseline
    newly = refresh_fingerprint(store, "heavy-local", spec_b)        # model swapped
    assert set(newly) == ALL_CLASSES
    assert store.is_stale("heavy-local", "planning") is True
    assert store.is_stale("fast-local", "planning") is False        # other tier safe


# ── review FIX 2: stale_pairs() snapshots under the lock (no concurrent-insert race)
def test_stale_pairs_safe_under_concurrent_record_grade():
    """Hammer ``stale_pairs()`` while writers INSERT brand-new keys via
    ``record_grade``. An unlocked iteration over the live dict view would raise
    ``RuntimeError: dictionary changed size during iteration``; the lock-snapshot
    must keep it safe and the returned list sorted."""
    store = default_profile()
    n_keys = 600
    errors: list = []
    done = threading.Event()

    def writer(prefix: str):
        for i in range(n_keys):
            # Each call inserts a previously-unseen (tier, work_class) key.
            store.record_grade("fast-local", f"{prefix}-{i}", score=0.5)

    def reader():
        # Read until all writers finish; any unsafe iteration surfaces here.
        while not done.is_set():
            try:
                pairs = store.stale_pairs()
                assert pairs == sorted(pairs, key=lambda k: (k[0], k[1] or ""))
            except Exception as exc:  # pragma: no cover - the bug we're guarding
                errors.append(exc)
                return

    writers = [threading.Thread(target=writer, args=(p,)) for p in ("a", "b", "c")]
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    for t in writers:
        t.start()
    for t in writers:
        t.join(20)
    done.set()
    for t in readers:
        t.join(5)

    assert not errors, errors
    # Sanity: all the inserted keys landed and none are stale (record_grade clears).
    assert isinstance(store.stale_pairs(), list)
    assert store.entry("fast-local", "a-0") is not None
