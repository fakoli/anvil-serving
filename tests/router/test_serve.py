"""Per-tier concurrency cap on the serve path (flexibility:T009 / ADR-0010 Phase 3).

Proves the ``Tier.max_concurrency`` acceptance criteria end to end:

* A tier with ``max_concurrency=N`` bounds ITS OWN concurrent in-flight requests
  to N (excess requests to that tier block on the per-tier
  ``threading.BoundedSemaphore`` until a slot frees; they are serialised, never
  rejected).
* A tier WITHOUT ``max_concurrency`` is unaffected — its dispatch is unbounded
  by this feature (only the process-global front-door limiter would cap it).
* The cap is strictly per-tier: it lives in ``RoutingBackend`` as a wrapper
  around only the capped tier's backend, and never touches the process-global
  limiter in ``front_door.py``.

Hermetic and stdlib-only: fake backends that block at a barrier, driven from
worker threads; no sockets are served (the RoutingBackend is exercised directly).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import List, Optional

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import load as load_router_config
from anvil_serving.router.fingerprint import refresh_fingerprint, serve_fingerprint
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.profile_store import ProfileStore, default_profile
from anvil_serving.router.serve import _ConcurrencyLimitedBackend, build_server


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
# Two LOCAL tiers, each with a single-tier custom preset so routing is fully
# deterministic (a custom preset has work_class=None -> policy.route skips the
# quality gate, so the pool is served verbatim). ``verify_local_min = false`` so
# an "allow" local tier takes the DIRECT allow-stream dispatch path (the clearest
# "in-flight while streaming" demonstration); only the `capped` tier sets
# max_concurrency.
_TWO_TIER_CONFIG = """\
[router]
mapping_version = "test-t009"
verify_local_min = false

[[router.tiers]]
id            = "capped"
base_url      = "http://127.0.0.1:39001/v1"
model         = "capped-model"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_CAPPED_KEY"
max_concurrency = 2

[[router.tiers]]
id            = "uncapped"
base_url      = "http://127.0.0.1:39002/v1"
model         = "uncapped-model"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_UNCAPPED_KEY"

[router.presets]
solo-capped = ["capped"]
solo-free   = ["uncapped"]
"""

_CAP = 2          # capped tier's max_concurrency
_N = 6            # concurrent requests fired at each tier (> _CAP)


def _write_two_tier_config(tmp_path: Path) -> str:
    p = tmp_path / "t009.toml"
    p.write_text(_TWO_TIER_CONFIG, encoding="utf-8")
    return str(p)


class _BlockingBackend:
    """A Backend whose ``generate()`` blocks at a barrier until released, tracking
    the peak number of concurrently in-flight generate() streams.

    On the first advance of its iterator it records itself as in-flight (updating
    the shared peak under a lock), waits on ``release`` (bounded by a timeout so a
    wiring bug can never hang the suite), then yields one token and exits
    (decrementing). Because the per-tier semaphore is acquired BEFORE the wrapped
    backend's generator body runs, only streams that got past the cap ever reach
    the in-flight increment — so ``peak`` is exactly the number of slots the cap
    allowed.
    """

    def __init__(self, release: threading.Event, token: str = "ok") -> None:
        self._release = release
        self._token = token
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0

    def generate(self, request: InternalRequest):
        with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            if not self._release.wait(timeout=5.0):
                raise AssertionError("blocking backend was never released")
            yield self._token
        finally:
            with self._lock:
                self.in_flight -= 1


def _drive(backend, n: int, request: InternalRequest):
    """Spawn ``n`` daemon threads, each fully consuming ``backend.generate(request)``.

    Returns ``(threads, results, errors)``; a thread's exception is captured into
    ``errors[i]`` rather than lost, so a failure surfaces as an assertion.
    """
    results: List[Optional[list]] = [None] * n
    errors: List[Optional[BaseException]] = [None] * n

    def _worker(i: int) -> None:
        try:
            results[i] = list(backend.generate(request))
        except BaseException as e:  # noqa: BLE001 - surfaced via errors[]
            errors[i] = e

    threads = [threading.Thread(target=_worker, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    return threads, results, errors


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --------------------------------------------------------------------------- #
# 1. The wrapper mechanism: bounds in-flight to N; the bare backend does not.
# --------------------------------------------------------------------------- #
def test_concurrency_wrapper_bounds_inflight_to_cap():
    """_ConcurrencyLimitedBackend(inner, N) lets at most N generate() streams run
    at once; the excess block on acquire until a slot frees, then all complete."""
    release = threading.Event()
    inner = _BlockingBackend(release)
    wrapped = _ConcurrencyLimitedBackend(inner, _CAP)
    req = InternalRequest(model="x", messages=[Message("user", "hi")])

    threads, results, errors = _drive(wrapped, _N, req)
    try:
        # Exactly the cap may be in flight; the other (_N - _CAP) block on acquire.
        assert _wait_until(lambda: inner.in_flight == _CAP), inner.in_flight
        # Give a (hypothetically) broken cap time to over-admit before asserting.
        time.sleep(0.1)
        assert inner.in_flight == _CAP, inner.in_flight
        assert inner.peak == _CAP, inner.peak
    finally:
        release.set()

    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    assert all(e is None for e in errors), errors
    # The cap serialises rather than rejecting: all _N requests eventually ran.
    assert results == [["ok"]] * _N
    assert inner.peak == _CAP, "cap must hold across the whole run"


def test_bare_backend_is_unbounded_baseline():
    """Control: the SAME blocking backend with NO wrapper reaches full concurrency
    (_N in flight at once). Proves the bound in the wrapped case comes from the
    cap, not from the driver or the backend itself."""
    release = threading.Event()
    inner = _BlockingBackend(release)
    req = InternalRequest(model="x", messages=[Message("user", "hi")])

    threads, _results, errors = _drive(inner, _N, req)
    try:
        assert _wait_until(lambda: inner.in_flight == _N), inner.in_flight
        assert inner.peak == _N, inner.peak
    finally:
        release.set()

    for t in threads:
        t.join(timeout=5)
    assert all(e is None for e in errors), errors


# --------------------------------------------------------------------------- #
# 2. Wiring: RoutingBackend wraps ONLY the tier that set max_concurrency.
# --------------------------------------------------------------------------- #
def test_routing_backend_wraps_only_the_capped_tier(tmp_path):
    """The per-tier cap is applied to the capped tier's backend and to no other:
    the uncapped tier keeps its exact injected backend instance."""
    cfg_path = _write_two_tier_config(tmp_path)
    capped_backend = StaticBackend(["c"])
    uncapped_backend = StaticBackend(["u"])
    httpd = build_server(
        cfg_path, host="127.0.0.1", port=0,
        backends={"capped": capped_backend, "uncapped": uncapped_backend},
        profile=ProfileStore({}),
    )
    try:
        routing = httpd.anvil_routing
        wrapped = routing._backends["capped"]
        assert isinstance(wrapped, _ConcurrencyLimitedBackend)
        assert wrapped.max_concurrency == _CAP
        # Strictly per-tier: the uncapped tier is NOT wrapped (same instance).
        assert routing._backends["uncapped"] is uncapped_backend
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# 3. End to end through RoutingBackend.generate: the cap bounds real dispatched
#    traffic to that tier, and the other tier stays unbounded — concurrently.
# --------------------------------------------------------------------------- #
def test_per_tier_cap_bounds_dispatch_and_leaves_other_tier_unbounded(tmp_path):
    """Dispatch _N concurrent requests to EACH tier through the real routing +
    dispatch path. The capped tier admits at most _CAP at once; the uncapped tier
    admits all _N at once. Both walls of the acceptance criterion, proven together
    on the same server."""
    cfg_path = _write_two_tier_config(tmp_path)
    release = threading.Event()
    capped_backend = _BlockingBackend(release, token="capped")
    uncapped_backend = _BlockingBackend(release, token="free")
    httpd = build_server(
        cfg_path, host="127.0.0.1", port=0,
        backends={"capped": capped_backend, "uncapped": uncapped_backend},
        profile=ProfileStore({}),
    )
    routing = httpd.anvil_routing

    capped_req = InternalRequest(model="solo-capped", messages=[Message("user", "hi")])
    free_req = InternalRequest(model="solo-free", messages=[Message("user", "hi")])

    cap_threads = free_threads = []
    cap_results = free_results = []
    cap_errors = free_errors = []
    try:
        cap_threads, cap_results, cap_errors = _drive(routing, _N, capped_req)
        free_threads, free_results, free_errors = _drive(routing, _N, free_req)

        # The uncapped tier reaches full concurrency; the capped tier caps at _CAP.
        assert _wait_until(lambda: uncapped_backend.in_flight == _N), uncapped_backend.in_flight
        assert _wait_until(lambda: capped_backend.in_flight == _CAP), capped_backend.in_flight
        # Give a broken cap time to over-admit before asserting the bound holds.
        time.sleep(0.1)
        assert capped_backend.in_flight == _CAP, capped_backend.in_flight
        assert capped_backend.peak == _CAP, capped_backend.peak
        assert uncapped_backend.peak == _N, uncapped_backend.peak
    finally:
        release.set()
        for t in list(cap_threads) + list(free_threads):
            t.join(timeout=5)
        httpd.server_close()

    assert all(e is None for e in cap_errors), cap_errors
    assert all(e is None for e in free_errors), free_errors
    # Every request to both tiers eventually completed (serialised, not rejected).
    assert cap_results == [["capped"]] * _N
    assert free_results == [["free"]] * _N
    # The per-tier bound held for the entire run; the other tier was never capped.
    assert capped_backend.peak == _CAP
    assert uncapped_backend.peak == _N


# --------------------------------------------------------------------------- #
# 4. Startup fingerprint refresh (flexibility:T002 / ADR-0009 phase 1)
# --------------------------------------------------------------------------- #
# One local tier whose CURRENT config identity uses model "qwen-new". A profile
# measured under a DIFFERENT serve identity (e.g. an older model) must go stale
# at startup; a profile that has never recorded a fingerprint must simply adopt
# this identity as its baseline (no spurious staleness). Custom preset -> the
# fingerprint refresh is orthogonal to routing, so no key/env is needed.
_ONE_TIER_CONFIG = """\
[router]
mapping_version = "test-t002"

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:39001/v1"
model         = "qwen-new"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_KEY"

[router.presets]
solo = ["fast-local"]
"""


def _write_one_tier_config(tmp_path: Path) -> str:
    p = tmp_path / "t002.toml"
    p.write_text(_ONE_TIER_CONFIG, encoding="utf-8")
    return str(p)


def test_build_server_marks_drifted_rows_stale(tmp_path):
    """Criterion 1: a serve whose identity DRIFTED since it was measured has its
    rows marked stale by ``build_server`` at startup (routing then distrusts it).

    The injected profile's ``fast-local`` rows are first baselined to an OLDER
    serve identity (a different model). The live config declares model
    ``qwen-new`` -> the startup refresh sees the mismatch and stales every
    ``fast-local`` row, downgrading a stale ``allow`` to ``allow-with-verify``.
    """
    cfg_path = _write_one_tier_config(tmp_path)

    # A seeded profile whose fast-local rows were measured under an OLDER serve
    # identity (model "qwen-OLD" at the same endpoint).
    store = default_profile()
    old_spec = {
        "id": "fast-local",
        "model": "qwen-OLD",
        "base_url": "http://127.0.0.1:39001/v1",
        "dialect": "openai",
        "context_limit": 32768,
    }
    assert refresh_fingerprint(store, "fast-local", old_spec) == []  # baseline only
    assert store.stale_pairs() == []

    httpd = build_server(
        cfg_path, host="127.0.0.1", port=0,
        backends={"fast-local": StaticBackend(["x"])},
        profile=store,
    )
    try:
        # The tier's CURRENT identity (qwen-new) differs from the measured one:
        # every fast-local row is now stale, and no OTHER tier is touched.
        assert {tier for (tier, _wc) in store.stale_pairs()} == {"fast-local"}
        assert store.is_stale("fast-local", "planning") is True
        assert store.is_stale("fast-local", "chat") is True
        assert store.is_stale("heavy-local", "review") is False
        assert store.is_stale("cloud", "planning") is False
        # Routing now distrusts a stale 'allow' row: chat was seeded 'allow' for
        # fast-local and is downgraded to 'allow-with-verify' by decision().
        assert store.decision("fast-local", "chat") == "allow-with-verify"
    finally:
        httpd.server_close()


def test_build_server_freshly_loaded_profile_not_spuriously_stale(tmp_path):
    """Criterion 2: a freshly-loaded/seed profile (every row ``fingerprint=None``)
    is NOT spuriously distrusted at startup — it ADOPTS the tier's current serve
    identity as its baseline, so nothing is stale and a re-run is a no-op."""
    cfg_path = _write_one_tier_config(tmp_path)
    store = default_profile()  # every row carries fingerprint=None (never measured)
    assert store.stale_pairs() == []

    httpd = build_server(
        cfg_path, host="127.0.0.1", port=0,
        backends={"fast-local": StaticBackend(["x"])},
        profile=store,
    )
    try:
        # No row was invalidated: adoption, not staleness.
        assert store.stale_pairs() == []
        assert store.is_stale("fast-local", "planning") is False
        assert store.is_stale("fast-local", "chat") is False
        # A seeded 'allow' row stays 'allow' — it was NOT downgraded.
        assert store.decision("fast-local", "chat") == "allow"

        # The rows now carry the tier's ACTUAL config fingerprint (baseline
        # adopted), so they hash to the live tier's serve identity.
        tier = load_router_config(cfg_path).tier("fast-local")
        expected_fp = serve_fingerprint(tier)
        assert store.entry("fast-local", "chat").fingerprint == expected_fp
        assert store.entry("fast-local", "planning").fingerprint == expected_fp

        # Idempotent: a second refresh against the SAME identity stales nothing.
        assert refresh_fingerprint(store, "fast-local", tier) == []
        assert store.stale_pairs() == []
    finally:
        httpd.server_close()


def test_build_server_seed_only_profile_behaves_identically(tmp_path):
    """A seed-only deployment (default_profile, no configured profile_path) is
    unchanged by the startup refresh: the seed rows adopt their baseline and stay
    non-stale, so every trust verdict is exactly the seed's."""
    cfg_path = _write_one_tier_config(tmp_path)

    # Baseline verdicts straight from the seed (before any build_server call).
    baseline = default_profile()
    seed_verdicts = {
        wc: baseline.decision("fast-local", wc)
        for wc in ("planning", "multi-file-refactor", "long-context", "review",
                   "bounded-edit", "chat")
    }

    httpd = build_server(
        cfg_path, host="127.0.0.1", port=0,
        backends={"fast-local": StaticBackend(["x"])},
        profile=default_profile(),
    )
    try:
        routing = httpd.anvil_routing
        refreshed = routing._profile
        assert refreshed.stale_pairs() == []
        # Every verdict matches the untouched seed.
        for wc, verdict in seed_verdicts.items():
            assert refreshed.decision("fast-local", wc) == verdict, wc
    finally:
        httpd.server_close()
