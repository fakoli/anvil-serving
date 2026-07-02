"""Bootstrap the quality profile from the committed shadow-eval (harness-router:T015).

The routing policy (:mod:`anvil_serving.router.policy`) consults a
:class:`~anvil_serving.router.profile_store.ProfileStore` — a table keyed by
``(tier_id, work_class)`` carrying a trust *decision* + advisory *quality
score* + *sample count*. T005 ships a HAND-AUTHORED seed
(:func:`~anvil_serving.router.profile_store.default_profile`). This module is the
**offline bootstrap** that replaces those authored anchors with numbers
*measured* from the committed planning-capability eval — the data-grounded seed
the router carries before any live calibration (T016).

Two modes, sharply separated:

* ``--replay <eval-data-dir> --out <profile.json>`` — the DEFAULT, CI-safe path.
  Walks the COMMITTED eval fixtures under ``docs/findings/eval-data/``, mirrors
  the ``aggregate.py`` rubric to compute a per-``(tier, work_class)`` quality
  score + sample count, writes a portable, byte-stable ``profile.json``, and can
  build a populated ``ProfileStore``. No network, no clock, binds nothing.
* ``--live`` / :func:`run_live` — the INTEGRATION step that would call the REAL
  serving tiers to grade fresh outputs. It is GUARDED and **never run by the unit
  test / CI** (it raises immediately rather than touching a tier); it is the
  documented hand-off to live calibration (T016). See :func:`run_live`.

How the eval maps to ``(tier, work_class)`` (read from the real fixture shapes):

* **work class** comes from the eval DIRECTORY name. ``2026-06-28-planning-capability``
  → strip the ISO-date prefix (``2026-06-28``, kept as provenance) → ``planning``
  (a canonical :data:`~anvil_serving.router.classify.WORK_CLASSES` value). See
  :func:`work_class_from_eval_dir`.
* **tier** comes from the eval's model label via :data:`MODEL_TO_TIER`
  (``frontier→cloud``, ``heavy→heavy-local``, ``fast→fast-local``) — the same
  three tiers the store's hand-authored anchors use.
* **quality score** mirrors ``aggregate.py``: de-anonymize the blind-judge files
  with ``anon_map.json``, pool every judge row for a model within the work class,
  average the ``/25`` totals, and normalize to the store's ``[0, 1]`` scale
  (``total_avg / 25``). For the committed planning eval this reproduces the known
  aggregates — frontier ``24.75/25 → 0.99``, fast ``16.0/25 → 0.64``, heavy
  ``13.25/25 → 0.53``.
* **sample count** is the number of pooled judge observations (2 PRDs × 2 judges
  = 4 for the committed eval).
* **decision** is derived from the normalized score by documented thresholds
  (:data:`_ALLOW_AT` / :data:`_DENY_BELOW`) chosen to REPRODUCE the seed verdicts
  for the eval-measured classes (planning: cloud ``allow``, both locals
  ``deny`` — i.e. "planning must go cloud"). See :func:`decision_for_score`.

Stdlib-only and deterministic: output is sorted by ``(tier_id, work_class)`` with
no timestamps or ``time.now()`` (the only date written is the committed eval's
own date, parsed from the directory name), so two replays are byte-identical.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .classify import WORK_CLASSES
from .profile_store import ProfileEntry, ProfileStore

# --- Stable identifiers for the portable artifact -------------------------------

#: Portable ``profile.json`` schema tag — bump if the on-disk shape changes.
SCHEMA = "anvil-serving.router.profile_bootstrap/v1"

# --- Eval rubric (mirrors docs/.../aggregate.py) --------------------------------

#: The blind-judge dimensions, scored 0-5 each. Mirrors ``aggregate.py``'s
#: ``DIMS`` — the single rubric both the eval and this bootstrap normalize over.
DIMS: Tuple[str, ...] = (
    "decomposition_granularity",
    "requirement_coverage",
    "dependency_correctness",
    "acceptance_verifiability",
    "faithfulness",
)
#: Max points per dimension, so the judge total tops out at ``len(DIMS) * 5``.
_MAX_PER_DIM = 5
#: The eval's full-marks total (``25`` for the 5-dimension rubric). This is the
#: denominator that normalizes a graded total onto the store's ``[0, 1]`` scale —
#: the ``aggregate.py`` "total_avg_of_25".
EVAL_MAX: float = float(len(DIMS) * _MAX_PER_DIM)

# --- Eval model label -> router tier id -----------------------------------------

#: The eval anonymizes three candidates by capability label; the router keys the
#: profile by serving-tier id. This is the one place that mapping is declared.
MODEL_TO_TIER: Mapping[str, str] = {
    "frontier": "cloud",
    "heavy": "heavy-local",
    "fast": "fast-local",
}

# --- Score -> decision thresholds -----------------------------------------------
#
# Derived decisions must REPRODUCE the hand-authored seed verdicts
# (``profile_store._SEED_VERDICTS``) for the classes the eval measured. The
# committed planning eval normalizes to cloud=0.99, fast=0.64, heavy=0.53, and
# the seed says planning -> {cloud: allow, fast-local: deny, heavy-local: deny}.
# A band of allow>=0.85 / deny<0.70 yields exactly that, and is consistent with
# the seed's other classes (e.g. a 0.70-0.85 local lands on allow-with-verify).
_ALLOW_AT = 0.85
_DENY_BELOW = 0.70

#: Decimal places for the normalized quality score in the portable artifact.
_SCORE_DP = 4
#: Decimal places for the eval ``/25`` average (mirrors ``aggregate.py``'s
#: ``round(x, 3)``).
_TOTAL_DP = 3


def decision_for_score(score: float) -> str:
    """Map a normalized ``[0, 1]`` quality score to a closed-set trust decision.

    ``score >= _ALLOW_AT`` -> ``allow``; ``score < _DENY_BELOW`` -> ``deny``;
    otherwise ``allow-with-verify``. The band is chosen to reproduce the T005
    seed verdicts for the eval-measured classes (see the module thresholds note).
    """
    if score >= _ALLOW_AT:
        return "allow"
    if score < _DENY_BELOW:
        return "deny"
    return "allow-with-verify"


# --- (tier, work-class) identification from the fixture layout -------------------

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")
# Trailing descriptors an eval dir slug may carry after the work-class token.
_SLUG_SUFFIXES = ("-capability", "-eval")


def work_class_from_eval_dir(dir_name: str) -> Tuple[str, Optional[str]]:
    """Derive ``(work_class, eval_date)`` from an eval directory name.

    ``2026-06-28-planning-capability`` -> ``("planning", "2026-06-28")``. The
    leading ISO date (if present) is stripped and returned as provenance; the
    remaining slug is matched against the canonical
    :data:`~anvil_serving.router.classify.WORK_CLASSES` taxonomy (longest token
    first, so ``multi-file-refactor-...`` resolves before any shorter prefix).
    If no taxonomy token matches, the de-dated, de-suffixed slug is returned
    verbatim (still deterministic, but outside the known taxonomy).
    """
    eval_date: Optional[str] = None
    slug = dir_name
    m = _DATE_PREFIX_RE.match(dir_name)
    if m:
        eval_date, slug = m.group(1), m.group(2)

    for wc in sorted(WORK_CLASSES, key=len, reverse=True):
        if slug == wc or slug.startswith(wc + "-"):
            return wc, eval_date

    # Fallback: strip a known trailing descriptor and use the slug as-is.
    for suffix in _SLUG_SUFFIXES:
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            break
    return slug, eval_date


# --- Replay: read committed fixtures, mirror aggregate.py -----------------------


def discover_eval_dirs(eval_data_root: Path) -> List[Path]:
    """Return committed eval directories under ``eval_data_root``, sorted.

    An eval directory is an immediate child that holds a ``grading/`` subdir with
    an ``anon_map.json`` and at least one ``judge_*.json`` — the artifacts the
    replay needs. Sorting keeps discovery deterministic.
    """
    if not eval_data_root.is_dir():
        raise FileNotFoundError(f"eval-data dir not found: {eval_data_root}")
    found: List[Path] = []
    for child in sorted(eval_data_root.iterdir()):
        grading = child / "grading"
        if (
            child.is_dir()
            and grading.is_dir()
            and (grading / "anon_map.json").is_file()
            and any(grading.glob("judge_*.json"))
        ):
            found.append(child)
    return found


def load_judge_rows(grading_dir: Path) -> List[dict]:
    """De-anonymize the blind-judge files into flat rows (mirrors ``aggregate.py``).

    Each row is ``{prd, judge, model, total}`` where ``model`` is the real label
    recovered from ``anon_map.json`` (``{prd: {model: letter}}``, inverted) and
    ``total`` is the judge's ``/25`` total for that candidate. The total is
    cross-checked against the dimension sum so a corrupt fixture fails loudly
    rather than silently skewing a score.
    """
    anon = json.loads((grading_dir / "anon_map.json").read_text(encoding="utf-8"))
    # {pid: {letter: model}}
    inv = {
        pid: {letter: model for model, letter in per_model.items()}
        for pid, per_model in anon.items()
    }
    rows: List[dict] = []
    for jf in sorted(grading_dir.glob("judge_*.json")):
        j = json.loads(jf.read_text(encoding="utf-8"))
        pid = j["prd"]
        jn = j["judge"]
        for letter, cand in j["candidates"].items():
            model = inv[pid][letter]
            total = cand["total"]
            dim_sum = sum(cand["scores"][d] for d in DIMS)
            if dim_sum != total:
                raise ValueError(
                    f"{jf.name}: candidate {letter!r} total {total} != "
                    f"sum(dimensions) {dim_sum}"
                )
            rows.append({"prd": pid, "judge": jn, "model": model, "total": total})
    return rows


@dataclass(frozen=True)
class BootstrapEntry:
    """One measured ``(tier, work_class)`` row, before/while populating the store.

    Carries both the store-facing fields (``decision`` / ``quality_score`` /
    ``sample_n`` / ``last_measured``) and the eval provenance (``model_label``,
    ``eval_total_avg``, ``source_evals``) that the portable artifact records.
    """

    tier_id: str
    work_class: str
    model_label: str
    decision: str
    quality_score: float
    sample_n: int
    eval_total_avg: float
    eval_max: float
    source_evals: Tuple[str, ...]
    last_measured: Optional[str]

    def to_profile_entry(self) -> ProfileEntry:
        """Build the immutable store entry (validates ``decision`` on construction)."""
        return ProfileEntry(
            decision=self.decision,
            quality_score=self.quality_score,
            sample_n=self.sample_n,
            last_measured=self.last_measured,
        )

    def to_dict(self) -> dict:
        """Portable, deterministic serialization for ``profile.json``."""
        return {
            "tier_id": self.tier_id,
            "work_class": self.work_class,
            "model_label": self.model_label,
            "decision": self.decision,
            "quality_score": self.quality_score,
            "sample_n": self.sample_n,
            "eval_total_avg": self.eval_total_avg,
            "eval_max": self.eval_max,
            "last_measured": self.last_measured,
            "source_evals": list(self.source_evals),
        }


def build_entries(eval_data_root: Path) -> List[BootstrapEntry]:
    """Replay the committed fixtures into per-``(tier, work_class)`` entries.

    Pools every de-anonymized judge row across all discovered eval dirs that map
    to the same ``(tier, work_class)``, averages the ``/25`` totals, normalizes to
    ``[0, 1]``, and derives the trust decision. Returned sorted by
    ``(tier_id, work_class)`` for byte-stable output.
    """
    # (tier_id, work_class) -> accumulator
    acc: Dict[Tuple[str, str], dict] = {}
    for eval_dir in discover_eval_dirs(eval_data_root):
        work_class, eval_date = work_class_from_eval_dir(eval_dir.name)
        for row in load_judge_rows(eval_dir / "grading"):
            model = row["model"]
            tier_id = MODEL_TO_TIER.get(model)
            if tier_id is None:
                raise ValueError(
                    f"{eval_dir.name}: model label {model!r} has no tier mapping "
                    f"in MODEL_TO_TIER {dict(MODEL_TO_TIER)}"
                )
            slot = acc.setdefault(
                (tier_id, work_class),
                {
                    "model_label": model,
                    "totals": [],
                    "evals": set(),
                    "dates": set(),
                },
            )
            slot["totals"].append(row["total"])
            slot["evals"].add(eval_dir.name)
            if eval_date is not None:
                slot["dates"].add(eval_date)

    entries: List[BootstrapEntry] = []
    for (tier_id, work_class), slot in acc.items():
        totals = slot["totals"]
        sample_n = len(totals)
        total_avg = round(sum(totals) / sample_n, _TOTAL_DP)
        quality_score = round(total_avg / EVAL_MAX, _SCORE_DP)
        # Provenance date: the latest committed eval date for this pair (a stable
        # value parsed from the dir name, NOT a wall-clock read).
        last_measured = max(slot["dates"]) if slot["dates"] else None
        entries.append(
            BootstrapEntry(
                tier_id=tier_id,
                work_class=work_class,
                model_label=slot["model_label"],
                decision=decision_for_score(quality_score),
                quality_score=quality_score,
                sample_n=sample_n,
                eval_total_avg=total_avg,
                eval_max=EVAL_MAX,
                source_evals=tuple(sorted(slot["evals"])),
                last_measured=last_measured,
            )
        )
    entries.sort(key=lambda e: (e.tier_id, e.work_class))
    return entries


def build_profile(eval_data_root: Path) -> dict:
    """Build the portable profile document (the ``profile.json`` payload)."""
    entries = build_entries(eval_data_root)
    return {
        "schema": SCHEMA,
        "mode": "replay",
        "eval_max": EVAL_MAX,
        "entries": [e.to_dict() for e in entries],
    }


def serialize_profile(profile: dict) -> str:
    """Deterministic JSON text (sorted keys, trailing newline) for byte-stability."""
    return json.dumps(profile, indent=2, sort_keys=True) + "\n"


def write_profile(eval_data_root: Path, out_path: Path) -> dict:
    """Build the profile from the fixtures and write it to ``out_path``.

    Returns the in-memory profile document (also written, byte-stable).
    """
    profile = build_profile(eval_data_root)
    out_path.write_text(serialize_profile(profile), encoding="utf-8")
    return profile


# --- Populate / load a ProfileStore --------------------------------------------


def store_from_profile(profile: dict) -> ProfileStore:
    """Build a :class:`ProfileStore` from a profile document (round-trips ``profile.json``)."""
    schema = profile.get("schema")
    if schema != SCHEMA:
        # A wrong/future-schema document must fail loudly here, not as an
        # opaque KeyError (or worse, load silently with missing semantics).
        raise ValueError(
            f"profile schema mismatch: expected {SCHEMA!r}, got {schema!r}; "
            f"regenerate the profile with this version's profile_bootstrap"
        )
    table: Dict[Tuple[str, Optional[str]], ProfileEntry] = {}
    for row in profile["entries"]:
        key = (row["tier_id"], row["work_class"])
        table[key] = ProfileEntry(
            decision=row["decision"],
            quality_score=row["quality_score"],
            sample_n=row["sample_n"],
            last_measured=row.get("last_measured"),
        )
    return ProfileStore(table)


def bootstrap_store(eval_data_root: Path) -> ProfileStore:
    """Replay the committed fixtures straight into a populated :class:`ProfileStore`.

    This is the in-process entry point routing uses to seed itself from the eval
    without round-tripping through disk.
    """
    return store_from_profile(build_profile(eval_data_root))


def load_profile_store(profile_path: Path) -> ProfileStore:
    """Load a previously-written ``profile.json`` into a :class:`ProfileStore`."""
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    return store_from_profile(profile)


# --- Live integration path (NOT run by CI) --------------------------------------


class LiveBootstrapNotConfigured(RuntimeError):
    """Raised by :func:`run_live` — the live path is a guarded integration step."""


def run_live(
    *,
    endpoints: Optional[Mapping[str, str]] = None,
    eval_data_root: Optional[Path] = None,
    out_path: Optional[Path] = None,
    confirm_calls_real_tiers: bool = False,
) -> ProfileStore:
    """INTEGRATION STEP — calls the REAL serving tiers. NOT exercised by CI.

    This is the live counterpart to ``--replay``: instead of reading committed,
    pre-graded fixtures, it would (1) for each work-class prompt set under
    ``eval_data_root/<eval>/prompts/``, POST to each tier ``endpoints[tier_id]``
    (an OpenAI-compatible URL — e.g. the :mod:`anvil_serving.router.serve` front
    door), (2) grade the fresh outputs with the same rubric, and (3) feed them
    through the SAME aggregation/normalization as :func:`build_entries`.

    It is **guarded and never invoked by the unit test / CI**: it binds nothing
    and refuses to run unless an operator passes real ``endpoints`` *and*
    ``confirm_calls_real_tiers=True``. Even when confirmed, the network-calling
    body is intentionally not implemented here — wiring it to live tiers is the
    T016 live-calibration task, which owns request budgets, retries, and
    held-out validation. Until then this raises, by design, so no test path can
    accidentally reach out to a tier.
    """
    if not confirm_calls_real_tiers or not endpoints:
        raise LiveBootstrapNotConfigured(
            "run_live is the live integration step: it calls real serving tiers "
            "and is never run by the unit test / CI. Pass real `endpoints` and "
            "`confirm_calls_real_tiers=True` to run it. Use --replay for the "
            "offline, CI-safe bootstrap."
        )
    raise NotImplementedError(
        "Live tier calibration lands in T016 (Optuna x GuideLLM inner loop with a "
        "correctness-preflight gate and held-out validation). The replay path "
        "(--replay) is the committed offline bootstrap."
    )


# --- CLI ------------------------------------------------------------------------


def _format_summary(profile: dict) -> str:
    """A compact human-readable scoreboard for the ``--replay`` console output."""
    lines = [
        f"{'tier':12} {'work_class':20} {'model':9} {'score':6} {'decision':18} {'n':>3} {'avg/25':>7}",
    ]
    for e in profile["entries"]:
        lines.append(
            f"{e['tier_id']:12} {e['work_class']:20} {e['model_label']:9} "
            f"{e['quality_score']:<6} {e['decision']:18} {e['sample_n']:>3} "
            f"{e['eval_total_avg']:>7}"
        )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m anvil_serving.router.profile_bootstrap",
        description=(
            "Bootstrap the (tier, work-class) quality profile from the committed "
            "shadow-eval (--replay), or run the guarded live integration step "
            "(--live)."
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--replay",
        metavar="EVAL_DATA_DIR",
        help="offline, CI-safe bootstrap: replay committed eval fixtures under "
        "this directory (e.g. docs/findings/eval-data/).",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="INTEGRATION step: call the real serving tiers (guarded; never run "
        "by CI). Requires --endpoint and --i-understand-this-calls-real-tiers.",
    )
    p.add_argument(
        "--out",
        metavar="PROFILE_JSON",
        help="write the portable profile.json here (required for --replay).",
    )
    p.add_argument(
        "--endpoint",
        action="append",
        default=[],
        metavar="TIER=URL",
        help="(--live only) a tier endpoint, e.g. cloud=https://...; repeatable.",
    )
    p.add_argument(
        "--i-understand-this-calls-real-tiers",
        dest="confirm_live",
        action="store_true",
        help="(--live only) explicit confirmation that the run hits real tiers.",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.live:
        endpoints = {}
        for spec in args.endpoint:
            tier, sep, url = spec.partition("=")
            if not sep or not tier or not url:
                print(f"error: --endpoint expects TIER=URL, got {spec!r}", file=sys.stderr)
                return 2
            endpoints[tier] = url
        try:
            run_live(
                endpoints=endpoints or None,
                out_path=Path(args.out) if args.out else None,
                confirm_calls_real_tiers=args.confirm_live,
            )
        except (LiveBootstrapNotConfigured, NotImplementedError) as exc:
            # Both the not-configured guard AND the confirmed-but-unimplemented
            # body (live calibration lands in T016) exit cleanly, not as a crash.
            print(f"--live not ready: {exc}", file=sys.stderr)
            return 2
        return 0

    # --replay
    if not args.out:
        print("error: --replay requires --out PROFILE_JSON", file=sys.stderr)
        return 2
    eval_data_root = Path(args.replay)
    out_path = Path(args.out)
    try:
        profile = write_profile(eval_data_root, out_path)
    except Exception as exc:
        # Missing/corrupt fixtures are an operator error, not a crash: match
        # the clean exit-2 style of every other error path in this CLI.
        print(f"error: could not build profile from {eval_data_root}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out_path} ({len(profile['entries'])} entries)")
    print(_format_summary(profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
