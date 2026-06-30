"""Traffic-window routing-quality metrics + silent-failure gate (harness-router:T017).

Replays a *window* of router decision/outcome records — the very shape
:mod:`anvil_serving.router.decision_log` emits per request — and reports, **per
work-class**, the three numbers that say whether the quality-gated router is
actually earning its keep on real traffic:

* **accept-rate** = ``accepted_local / total`` — the share of a work-class's
  requests the router served from a LOCAL tier (and delivered to the caller).
  High is good *only when paired with a low silent-failure rate*: keeping work
  local is the whole point, but not if the kept-local answers are wrong.
* **silent-failure rate** = ``silent_failures / accepted_local`` — of the
  responses we accepted locally and DELIVERED, the share that were actually
  failures the verify gate (T007) should have caught but did not. This is the
  number the CI gate guards: a silent failure is the expensive miss (a wrong
  answer shipped to the caller as if correct).
* **cloud-tokens-saved** = tokens a local tier served that would otherwise have
  been billed by the cloud tier (prompt + completion of the served-local
  response). The dollars-saved side of the ledger.

Scope (honest boundary): this is a MEASUREMENT/REPORTING tool over a COMMITTED
fixture traffic window (``tests/router/fixtures/traffic.jsonl``). Capturing a
*live* window from a running router — persisting each :class:`DecisionRecord`
plus its served-tier privacy and a held-out/audit correctness label — is a
separate operational step (it needs durable storage + an offline grader); this
module deliberately owns only the deterministic replay + gate so CI can pin the
silent-failure rate with no network, no clock, and no live tier.

Record schema (a documented SUPERSET of ``decision_log.DecisionRecord``)
------------------------------------------------------------------------
Each JSONL line carries every ``DecisionRecord`` field by its real name
(``work_class``, ``requested_tiers``, ``attempts`` — each an ``AttemptRecord``
with ``tier_id``/``outcome``/``prompt_tokens``/``completion_tokens``/… —,
``served_tier``, ``total_prompt_tokens``, ``total_completion_tokens``,
``fell_back``, ``intent``) so this measures ACTUAL router output, not an invented
schema. Two capture-time enrichment fields are added (the superset):

* ``served_tier_privacy`` — ``"local"`` / ``"cloud"`` / ``null``: the privacy of
  the tier that served, read from ``config.Tier.privacy`` at decision time. The
  bare decision log keys tiers by id only; locality is what accept-rate and
  cloud-tokens-saved hinge on, so the window snapshot denormalizes it onto each
  record. (A served record without this field is treated as NON-local — the
  conservative direction: it cannot inflate accept-rate, tokens-saved, or the
  silent-failure denominator.)
* ``ground_truth`` — ``"pass"`` / ``"fail"`` / ``null``: the held-out / audit
  verdict of the served response's ACTUAL correctness. ``null`` = not audited.

Silent-failure derivation (the one rule this tool uses)
-------------------------------------------------------
A record is a **silent failure** iff it was *accepted locally* AND its audit
label says the delivered response was actually wrong::

    served_locally = served_tier is not None and served_tier_privacy == "local"
    accepted_local = served_locally          # a "served" outcome means verify
                                             # PASSED and the response was
                                             # delivered to the caller
    silent_failure = accepted_local and ground_truth == "fail"

That is exactly "a response delivered to the caller that the gate should have
caught but didn't." (An explicit ``silent_failure: true`` field, if present on an
accepted-local record, is also honored as a pre-adjudicated override — but the
canonical rule, and the one the committed fixture exercises, is the derived one
above.) Cloud-served and exhausted records are never silent failures: cloud is
the always-available safe tier, and nothing was delivered locally.

The denominator of the silent-failure rate is *all* accepted-local responses
(audited or not); an unaudited accepted-local response counts in the denominator
but can never be a numerator — we only ever count a *proven* failure, so the
rate is conservative (never overstated).

Gate (the CI assertion)
-----------------------
The gate trips when the silent-failure rate is NOT strictly below
``--silent-failure-threshold`` (default ``0.01`` = 1%) for ANY individual
work-class OR for the overall window (``rate >= threshold`` is a breach). On a
breach :func:`main` exits non-zero so CI fails. Gating per-class *and* overall is
intentional: a single bad work-class must not be averaged away by healthy ones.

Output is deterministic (work-classes sorted, integer token counts, no
``time.now()``): a readable per-work-class table + an OVERALL row (default), or
the machine-readable summary (``--json``). Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: Schema tag for the machine-readable summary — bump if its shape changes.
SCHEMA = "anvil-serving.router.metrics/v1"

#: Default CI gate: a work-class (or the whole window) must keep its silent-
#: failure rate strictly below 1%.
DEFAULT_SILENT_FAILURE_THRESHOLD = 0.01

#: Summary key standing in for an overall-window breach in ``breaches``.
OVERALL_KEY = "__overall__"

#: Bucket key for a record whose ``work_class`` is null (a custom preset with no
#: taxonomy mapping); kept distinct so it sorts and reports separately.
_UNCLASSIFIED = "(unclassified)"


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def load_records(path: Path) -> List[Dict[str, Any]]:
    """Parse a traffic-window JSONL file into a list of record dicts.

    One JSON object per non-blank line. Blank / whitespace-only lines are
    skipped. A malformed line raises ``ValueError`` naming the 1-based line
    number (so a corrupt window fails loudly rather than silently skewing the
    rate). Each record must be a JSON object.
    """
    records: List[Dict[str, Any]] = []
    text = Path(path).read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(
                f"{path}:{lineno}: each record must be a JSON object, got "
                f"{type(obj).__name__}"
            )
        records.append(obj)
    return records


# --------------------------------------------------------------------------- #
# per-record classification (the load-bearing definitions)
# --------------------------------------------------------------------------- #
def _work_class_key(record: Mapping[str, Any]) -> str:
    """The bucket key for a record: its ``work_class`` or the unclassified slot."""
    wc = record.get("work_class")
    return wc if isinstance(wc, str) and wc else _UNCLASSIFIED


def served_locally(record: Mapping[str, Any]) -> bool:
    """True when a LOCAL tier served (and delivered) the response.

    Requires both a non-null ``served_tier`` and ``served_tier_privacy ==
    "local"``. A served record missing the privacy enrichment reads as
    non-local (conservative — see the module docstring).
    """
    return (
        record.get("served_tier") is not None
        and record.get("served_tier_privacy") == "local"
    )


def is_silent_failure(record: Mapping[str, Any]) -> bool:
    """True when an accepted-local response was actually a failure.

    The canonical rule: ``served_locally(record) and ground_truth == "fail"``.
    An explicit ``silent_failure: true`` on an accepted-local record is also
    honored as a pre-adjudicated override. A record that was not accepted
    locally is never a silent failure.
    """
    if not served_locally(record):
        return False
    explicit = record.get("silent_failure")
    if isinstance(explicit, bool):
        return explicit
    return record.get("ground_truth") == "fail"


def _served_attempt(record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    """The attempt that served (``outcome == "served"``), or ``None``."""
    for attempt in record.get("attempts") or ():
        if isinstance(attempt, Mapping) and attempt.get("outcome") == "served":
            return attempt
    return None


def local_tokens_served(record: Mapping[str, Any]) -> int:
    """Tokens (prompt + completion) the SERVED local response actually moved.

    Uses the served attempt's own counts so wasted earlier-attempt tokens (a
    local fail before a local re-serve) are not miscredited as cloud savings; if
    the per-attempt breakdown is absent, falls back to the record totals. Both
    sides of the bill count — serving locally saves the cloud tier its prompt
    (input) AND completion (output) tokens.
    """
    attempt = _served_attempt(record)
    if attempt is not None:
        return int(attempt.get("prompt_tokens", 0) or 0) + int(
            attempt.get("completion_tokens", 0) or 0
        )
    return int(record.get("total_prompt_tokens", 0) or 0) + int(
        record.get("total_completion_tokens", 0) or 0
    )


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def _empty_bucket() -> Dict[str, int]:
    return {"total": 0, "accepted_local": 0, "silent_failures": 0, "cloud_tokens_saved": 0}


def _finalize(bucket: Mapping[str, int]) -> Dict[str, Any]:
    """Fold raw counts into the per-class metric block (the three metrics + counts)."""
    total = bucket["total"]
    accepted = bucket["accepted_local"]
    sf = bucket["silent_failures"]
    return {
        "total": total,
        "accepted_local": accepted,
        "accept_rate": (accepted / total) if total else 0.0,
        "silent_failures": sf,
        # Denominator is accepted-local responses; 0 accepted-local => no silent
        # failure is possible => rate 0.0 (never a divide-by-zero, never a breach).
        "silent_failure_rate": (sf / accepted) if accepted else 0.0,
        "cloud_tokens_saved": bucket["cloud_tokens_saved"],
    }


def gate_breaches(summary: Mapping[str, Any], threshold: float) -> List[str]:
    """Work-classes (and ``__overall__``) whose silent-failure rate breaches.

    A breach is ``rate >= threshold`` (i.e. NOT strictly below it). Returned
    sorted, with ``__overall__`` last, for deterministic reporting.
    """
    breaches = [
        wc
        for wc in sorted(summary["work_classes"])
        if summary["work_classes"][wc]["silent_failure_rate"] >= threshold
    ]
    if summary["overall"]["silent_failure_rate"] >= threshold:
        breaches.append(OVERALL_KEY)
    return breaches


def aggregate(
    records: Sequence[Mapping[str, Any]],
    *,
    threshold: float = DEFAULT_SILENT_FAILURE_THRESHOLD,
) -> Dict[str, Any]:
    """Aggregate a traffic window into the machine-readable metrics summary.

    Computes, per work-class and overall: ``accept_rate``,
    ``silent_failure_rate``, and ``cloud_tokens_saved`` (plus the ``total`` /
    ``accepted_local`` / ``silent_failures`` counts they derive from), then the
    gate ``breaches`` / ``gate_passed`` for ``threshold``. Pure and deterministic
    (no I/O, no clock); work-classes are emitted in sorted order by the caller.
    """
    per_class: Dict[str, Dict[str, int]] = {}
    overall = _empty_bucket()

    for record in records:
        key = _work_class_key(record)
        bucket = per_class.setdefault(key, _empty_bucket())

        bucket["total"] += 1
        overall["total"] += 1

        if served_locally(record):
            bucket["accepted_local"] += 1
            overall["accepted_local"] += 1
            saved = local_tokens_served(record)
            bucket["cloud_tokens_saved"] += saved
            overall["cloud_tokens_saved"] += saved
            if is_silent_failure(record):
                bucket["silent_failures"] += 1
                overall["silent_failures"] += 1

    work_classes = {key: _finalize(bucket) for key, bucket in per_class.items()}
    summary: Dict[str, Any] = {
        "schema": SCHEMA,
        "threshold": threshold,
        "total_records": overall["total"],
        "work_classes": work_classes,
        "overall": _finalize(overall),
    }
    breaches = gate_breaches(summary, threshold)
    summary["breaches"] = breaches
    summary["gate_passed"] = not breaches
    return summary


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


_HEADER = (
    f"{'work_class':20} {'n':>4} {'local_ok':>8} {'accept':>8} "
    f"{'silent':>7} {'sf_rate':>9} {'cloud_tok_saved':>16}"
)


def _row(name: str, m: Mapping[str, Any]) -> str:
    return (
        f"{name:20} {m['total']:>4} {m['accepted_local']:>8} "
        f"{_pct(m['accept_rate']):>8} {m['silent_failures']:>7} "
        f"{_pct(m['silent_failure_rate']):>9} {m['cloud_tokens_saved']:>16}"
    )


def format_report(summary: Mapping[str, Any]) -> str:
    """A deterministic, human-readable per-work-class table + OVERALL + gate verdict.

    Columns: ``n`` (total requests), ``local_ok`` (accepted locally — the
    silent-failure denominator), ``accept`` (accept-rate), ``silent`` (silent-
    failure count), ``sf_rate`` (silent-failure rate), ``cloud_tok_saved``.
    """
    threshold = summary["threshold"]
    lines: List[str] = [
        f"traffic window: {summary['total_records']} records   "
        f"silent-failure gate: rate < {_pct(threshold)}",
        _HEADER,
        "-" * len(_HEADER),
    ]
    for wc in sorted(summary["work_classes"]):
        lines.append(_row(wc, summary["work_classes"][wc]))
    lines.append("-" * len(_HEADER))
    lines.append(_row("OVERALL", summary["overall"]))

    breaches = summary["breaches"]
    if breaches:
        pretty = ", ".join("overall" if b == OVERALL_KEY else b for b in breaches)
        lines.append(
            f"GATE: FAIL - silent-failure rate >= {_pct(threshold)} in: {pretty}"
        )
    else:
        lines.append(
            f"GATE: PASS - silent-failure rate < {_pct(threshold)} "
            f"for every work-class and overall"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m anvil_serving.router.metrics",
        description=(
            "Replay a router traffic window (JSONL of decision/outcome records) "
            "and report per-work-class accept-rate, silent-failure rate, and "
            "cloud-tokens-saved. Exits non-zero if the silent-failure rate "
            "breaches the threshold (the CI gate)."
        ),
    )
    p.add_argument(
        "--replay",
        required=True,
        metavar="TRAFFIC_JSONL",
        help="path to the traffic-window JSONL (one decision/outcome record per line).",
    )
    p.add_argument(
        "--silent-failure-threshold",
        type=float,
        default=DEFAULT_SILENT_FAILURE_THRESHOLD,
        metavar="RATE",
        help="gate: fail if any work-class (or overall) silent-failure rate is "
        f">= this (default {DEFAULT_SILENT_FAILURE_THRESHOLD} = 1%%).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable summary as JSON instead of the table.",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Replay, report, and gate. Returns the process exit code.

    ``0`` — the silent-failure gate passed; ``1`` — a work-class or the overall
    window breached the threshold; ``2`` — a usage / I/O / parse error.
    """
    args = build_arg_parser().parse_args(argv)

    if args.silent_failure_threshold < 0:
        print("error: --silent-failure-threshold must be >= 0", file=sys.stderr)
        return 2

    try:
        records = load_records(Path(args.replay))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = aggregate(records, threshold=args.silent_failure_threshold)

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(format_report(summary))

    return 0 if summary["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
