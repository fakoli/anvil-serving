"""Async quality-calibration sampler (harness-router:T016).

This is the OFF-HOT-PATH half of live calibration: a sampler that occasionally
takes a finished ``(request, response)`` pair, has an INJECTED grader score its
quality, and folds that grade back into the
:class:`~anvil_serving.router.profile_store.ProfileStore` row for
``(tier_id, work_class)``. The score feeds future routing; nothing here changes
the response the caller already got.

Three load-bearing properties (the T016 gate):

1. **Response first, grade later.** :meth:`Calibrator.observe` NEVER awaits the
   grade. When a request is sampled it submits a *background task* to a
   :class:`concurrent.futures.ThreadPoolExecutor` and returns the response
   immediately. The grade (build sample -> redact -> grade -> record) runs on a
   worker thread; the request path does not pay for it. Tests prove this by
   injecting a grader that blocks on a :class:`threading.Event`: ``observe``
   returns while the grade is still parked.

2. **Off means off.** With ``enabled=False`` (or a request not selected by the
   sample rate) ``observe`` does nothing — no task is submitted and the grader is
   never called.

3. **Redact before egress.** The sample is run through
   :func:`~anvil_serving.router.secrets.sanitize` with ``calibration=True`` (keeps
   the prompt text the grader needs to score quality, but STILL strips API keys
   and scrubs secret-shaped substrings) PLUS the operator's configured
   ``redact_fields`` (dropped entirely) BEFORE it is handed to the grader. The
   grader never sees a raw key or a configured-sensitive field.

The grader is ALWAYS injected — this module never makes a network/LLM call, and
neither do its tests. Thread-safety of the shared profile update is the store's
:class:`threading.Lock` (see :meth:`ProfileStore.record_grade`); the executor is
owned and shut down by :meth:`Calibrator.close` (or the context manager) unless
the caller injected their own.

Stdlib-only (``concurrent.futures`` / ``threading`` / ``random`` / ``datetime``).
"""
from __future__ import annotations

import concurrent.futures
import random
import threading
from dataclasses import dataclass, is_dataclass, asdict
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
)

from .profile_store import ProfileStore
from .secrets import sanitize


@dataclass(frozen=True)
class Grade:
    """A grader's quality verdict for one sampled exchange.

    ``score`` is the normalized ``[0, 1]`` quality the calibrator folds into the
    profile row's running mean. ``decision`` is OPTIONAL: when a grader is
    confident enough to also revise the trust verdict it returns one of
    :data:`~anvil_serving.router.profile_store.DECISIONS`; when it is ``None`` the
    row keeps its existing decision (a quality number alone never flips the
    load-bearing ``deny`` gate). ``notes`` is free-form provenance.
    """

    score: float
    decision: Optional[str] = None
    notes: str = ""


#: The injected grader: a redacted sample -> a quality verdict. May return a
#: :class:`Grade`, a bare ``float`` score, or a mapping with a ``"score"`` (and
#: optional ``"decision"``) key. NEVER a network/LLM call inside this module.
Grader = Callable[[Mapping[str, Any]], Any]


def _coerce_grade(grade: Any) -> Tuple[float, Optional[str]]:
    """Normalize a grader return into ``(score, optional_decision)``."""
    if isinstance(grade, Grade):
        return float(grade.score), grade.decision
    if isinstance(grade, Mapping):
        if "score" not in grade:
            raise TypeError("grade mapping must carry a 'score' key")
        return float(grade["score"]), grade.get("decision")
    # bool is an int subclass; reject it (a boolean is not a score).
    if isinstance(grade, (int, float)) and not isinstance(grade, bool):
        return float(grade), None
    raise TypeError(
        f"grader returned {type(grade).__name__}; expected Grade, a number, or a "
        f"mapping with a 'score' key"
    )


def _jsonable(value: Any) -> Any:
    """Best-effort view of ``request``/``response`` for the sample builder.

    Mappings / lists / scalars pass through (``sanitize`` walks them directly).
    A richer object is reduced to something walkable: a dataclass via
    :func:`dataclasses.asdict`, an object exposing a ``raw`` mapping (e.g.
    :class:`~anvil_serving.router.internal.InternalRequest`) via that mapping,
    then ``vars()``, and finally ``str()`` so the sampler can never raise on an
    odd shape.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        try:
            return asdict(value)
        except Exception:  # pragma: no cover - defensive
            pass
    raw = getattr(value, "raw", None)
    if isinstance(raw, Mapping):
        return raw
    try:
        return dict(vars(value))
    except TypeError:
        return str(value)


def _strip_fields(value: Any, drop: frozenset) -> Any:
    """Recursively drop any mapping key whose lowercased name is in ``drop``."""
    if isinstance(value, Mapping):
        return {
            k: _strip_fields(v, drop)
            for k, v in value.items()
            if str(k).lower() not in drop
        }
    if isinstance(value, list):
        return [_strip_fields(v, drop) for v in value]
    return value


def _now_iso() -> str:
    """Wall-clock ISO-8601 UTC timestamp (default ``last_measured`` stamp)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Calibrator:
    """Sample finished exchanges, grade them off-thread, fold grades into the profile.

    Parameters
    ----------
    store:
        The shared :class:`ProfileStore` the grade is recorded into (thread-safe
        via the store's lock).
    grader:
        INJECTED ``(redacted_sample) -> grade``. Called only on a worker thread,
        only with a redacted sample. Never a real network/LLM call here.
    enabled:
        Master switch. ``False`` -> :meth:`observe` is a no-op (criterion 2).
    sample_rate:
        Fraction of requests to sample when enabled (``1.0`` = all, the default;
        ``0.0`` = none). Ignored if an explicit ``sampler`` is injected.
    redact_fields:
        Operator-configured field NAMES (case-insensitive) to DROP from the sample
        before grading — sensitive fields ``sanitize`` would not otherwise catch.
    secrets:
        Known-literal secret values to scrub anywhere (passed straight to
        :func:`~anvil_serving.router.secrets.sanitize`).
    executor:
        Optional shared :class:`~concurrent.futures.ThreadPoolExecutor`. If
        ``None`` (default) the calibrator owns a private one and shuts it down in
        :meth:`close`; an injected executor is NOT shut down here.
    max_workers:
        Worker count for the private executor (ignored if ``executor`` is given).
    sampler:
        Optional ``() -> bool`` override for the sampling decision (tests inject a
        deterministic one); takes precedence over ``sample_rate``.
    now:
        Optional ``() -> str`` clock for the ``last_measured`` stamp (injectable
        for deterministic tests). Defaults to wall-clock UTC.
    """

    def __init__(
        self,
        store: ProfileStore,
        *,
        grader: Grader,
        enabled: bool,
        sample_rate: float = 1.0,
        redact_fields: Iterable[str] = (),
        secrets: Iterable[str] = (),
        executor: Optional[concurrent.futures.ThreadPoolExecutor] = None,
        max_workers: int = 2,
        sampler: Optional[Callable[[], bool]] = None,
        now: Optional[Callable[[], str]] = None,
    ):
        self._store = store
        self._grader = grader
        self._enabled = bool(enabled)
        self._sample_rate = float(sample_rate)
        self._redact_fields: Tuple[str, ...] = tuple(redact_fields)
        self._redact_field_set = frozenset(f.lower() for f in self._redact_fields)
        self._secrets: Tuple[str, ...] = tuple(secrets)
        self._sampler = sampler
        self._now = now or _now_iso

        self._executor = executor or concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="anvil-calibrate"
        )
        self._owns_executor = executor is None

        # Pending futures (so tests can drain) + a private RNG so sampling never
        # perturbs global random state.
        self._futures: List[concurrent.futures.Future] = []
        self._futures_lock = threading.Lock()
        self._rng = random.Random()
        self._closed = False

        # Observability: calibration failures are SWALLOWED (a grading error must
        # never break serving), but counted so they are not silently invisible.
        self._errors = 0
        self._last_error: Optional[BaseException] = None

    # --- public API -----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """The backing executor (exposed so tests can drain/await background grades)."""
        return self._executor

    @property
    def errors(self) -> int:
        """Count of background grades that raised (and were swallowed)."""
        return self._errors

    def observe(
        self,
        request: Any,
        response: Any,
        work_class: Optional[str],
        tier_id: str,
    ) -> Any:
        """Maybe sample this exchange for off-thread grading; return ``response`` now.

        Returns the passed ``response`` UNCHANGED and IMMEDIATELY. When calibration
        is enabled and this request is selected, a background task (build sample ->
        redact -> grade -> record) is submitted to the executor; ``observe`` does
        NOT await it. When disabled or not sampled, nothing is submitted and the
        grader is never called.
        """
        if self._enabled and not self._closed and self._should_sample():
            self._submit(request, response, work_class, tier_id)
        return response

    def pending(self) -> int:
        """Number of background grades not yet finished (for tests/observability)."""
        with self._futures_lock:
            return sum(1 for f in self._futures if not f.done())

    def drain(self, timeout: Optional[float] = None) -> bool:
        """Block until all submitted grades finish (or ``timeout``). ``True`` if all done.

        Never re-raises a grader error — calibration failures are swallowed by the
        worker (and counted in :attr:`errors`); ``drain`` only joins the futures.
        """
        with self._futures_lock:
            pending = list(self._futures)
        if not pending:
            return True
        _, not_done = concurrent.futures.wait(pending, timeout=timeout)
        return not not_done

    # ``wait`` reads better at call sites that just want to block for the grades.
    wait = drain

    def close(self, *, wait: bool = True) -> None:
        """Stop sampling and shut the OWNED executor down (no-op for an injected one)."""
        self._closed = True
        if self._owns_executor:
            self._executor.shutdown(wait=wait)

    def __enter__(self) -> "Calibrator":
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False

    # --- internals ------------------------------------------------------------

    def _should_sample(self) -> bool:
        if self._sampler is not None:
            return bool(self._sampler())
        if self._sample_rate >= 1.0:
            return True
        if self._sample_rate <= 0.0:
            return False
        return self._rng.random() < self._sample_rate

    def _submit(
        self, request: Any, response: Any, work_class: Optional[str], tier_id: str
    ) -> Optional[concurrent.futures.Future]:
        # Capture the serve identity active for this row AT DISPATCH TIME, on the
        # caller thread (before any worker can run). record_grade uses it so a
        # grade measured under the current serve cannot clear a staleness that a
        # LATER apply_fingerprint sets while this grade is still in flight.
        prev = self._store.entry(tier_id, work_class)
        submitted_fp = prev.fingerprint if prev is not None else None
        try:
            fut = self._executor.submit(
                self._grade_and_record,
                request,
                response,
                work_class,
                tier_id,
                submitted_fp,
            )
        except RuntimeError:
            # Executor already shut down: never block or raise on the request path.
            return None
        with self._futures_lock:
            # Opportunistically prune completed futures so the list can't grow
            # without bound under a long-lived server.
            self._futures = [f for f in self._futures if not f.done()]
            self._futures.append(fut)
        return fut

    def _build_sample(
        self, request: Any, response: Any, work_class: Optional[str], tier_id: str
    ) -> Dict[str, Any]:
        """The raw (pre-redaction) sample handed to :meth:`_redact`."""
        return {
            "tier_id": tier_id,
            "work_class": work_class,
            "request": _jsonable(request),
            "response": _jsonable(response),
        }

    def _redact(self, sample: Mapping[str, Any]) -> Dict[str, Any]:
        """Strip secrets + configured-sensitive fields BEFORE the grader sees it.

        ``sanitize(calibration=True)`` keeps prompt/completion text (the grader
        needs it to judge quality) but still masks secret-named fields and scrubs
        secret-shaped substrings; the configured ``redact_fields`` are then dropped
        entirely.
        """
        clean = sanitize(sample, calibration=True, secrets=self._secrets)
        if self._redact_field_set:
            clean = _strip_fields(clean, self._redact_field_set)
        return clean

    def _grade_and_record(
        self,
        request: Any,
        response: Any,
        work_class: Optional[str],
        tier_id: str,
        submitted_fingerprint: Optional[str],
    ) -> None:
        """Worker body: build -> redact -> grade -> record. Errors are swallowed.

        ``submitted_fingerprint`` is the serve identity captured at dispatch (see
        :meth:`_submit`); it is forwarded to
        :meth:`~anvil_serving.router.profile_store.ProfileStore.record_grade` so a
        grade for a now-superseded serve can't clear fresh staleness.
        """
        try:
            sample = self._build_sample(request, response, work_class, tier_id)
            redacted = self._redact(sample)  # BEFORE egress to the grader
            grade = self._grader(redacted)
            score, decision = _coerce_grade(grade)
            self._store.record_grade(
                tier_id,
                work_class,
                score=score,
                decision=decision,
                last_measured=self._now(),
                submitted_fingerprint=submitted_fingerprint,
            )
        except Exception as exc:  # noqa: BLE001 - calibration must NEVER break serving
            # Count + stash for observability; the request already succeeded, so a
            # grading failure is swallowed rather than surfaced. Guard the counter
            # so two failing grades can't race the increment.
            with self._futures_lock:
                self._errors += 1
                self._last_error = exc
