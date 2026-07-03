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
* ``--live`` / :func:`run_live` — the GUARDED offline batch that measures LOCAL
  tiers through their REAL backends (so ``Tier.extra_body`` is applied exactly as
  in prod), grades the fresh outputs with the INDEPENDENT Agent-SDK grader (T004),
  and writes a fingerprint-tagged candidate ``profile.json`` the operator reviews
  and promotes. It is GUARDED (refuses without real ``endpoints`` +
  ``confirm_calls_real_tiers``), LOCAL-tier-only (a cloud/Claude tier is refused —
  a Claude judge must never self-verify), and its real backend/judge seams are
  INJECTABLE, so the unit tests exercise it with fakes and CI makes ZERO
  network/subprocess calls. See :func:`run_live`.

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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .classify import WORK_CLASSES
from .profile_store import ProfileEntry, ProfileStore, default_profile_table

# --- Stable identifiers for the portable artifact -------------------------------

#: Portable ``profile.json`` schema tag — bump if the on-disk shape changes.
#: v2 (T001, ADR-0009): each entry additionally carries ``fingerprint`` (the serve
#: identity the row was measured under) and ``reasoning`` (reasoning-config
#: provenance); :func:`store_from_profile` MERGES measured rows over the seed table.
SCHEMA = "anvil-serving.router.profile_bootstrap/v2"
#: The prior schema — still accepted by :func:`store_from_profile` for a
#: backward-compatible load of a pre-T001 (v1) ``profile.json`` (whose rows simply
#: carry no ``fingerprint`` / ``reasoning``).
SCHEMA_V1 = "anvil-serving.router.profile_bootstrap/v1"
#: Schemas :func:`store_from_profile` will accept.
_ACCEPTED_SCHEMAS = (SCHEMA, SCHEMA_V1)

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
    # v2 (T001): serve identity + reasoning provenance. Both default ``None`` — the
    # replay path grades pre-committed outputs with no live serve, so it never sets
    # them; the live path (run_live, T005) populates them from the real serve.
    fingerprint: Optional[str] = None
    reasoning: Optional[Mapping[str, object]] = None

    def to_profile_entry(self) -> ProfileEntry:
        """Build the immutable store entry (validates ``decision`` on construction)."""
        return ProfileEntry(
            decision=self.decision,
            quality_score=self.quality_score,
            sample_n=self.sample_n,
            last_measured=self.last_measured,
            fingerprint=self.fingerprint,
        )

    def to_dict(self) -> dict:
        """Portable, deterministic serialization for ``profile.json`` (v2)."""
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
            "fingerprint": self.fingerprint,
            "reasoning": dict(self.reasoning) if self.reasoning is not None else None,
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


def store_from_profile(profile: dict, *, merge_over_seed: bool = True) -> ProfileStore:
    """Build a :class:`ProfileStore` from a profile document (round-trips ``profile.json``).

    With ``merge_over_seed=True`` (the default; T001 / ADR-0009) the measured rows
    are overlaid on the hand-authored seed table
    (:func:`~anvil_serving.router.profile_store.default_profile_table`): every
    ``(tier, work_class)`` the profile did NOT measure keeps its seed verdict,
    instead of silently re-verdicting unmeasured classes to the store's fail-closed
    default. Pass ``merge_over_seed=False`` for the raw measured-only table
    (the pre-T001 behaviour).

    Accepts the current v2 schema and, for a backward-compatible load, a pre-T001
    v1 document (whose rows simply carry no ``fingerprint`` / ``reasoning``).
    """
    schema = profile.get("schema")
    if schema not in _ACCEPTED_SCHEMAS:
        # A wrong/future-schema document must fail loudly here, not as an
        # opaque KeyError (or worse, load silently with missing semantics).
        raise ValueError(
            f"profile schema mismatch: expected one of {_ACCEPTED_SCHEMAS!r}, "
            f"got {schema!r}; regenerate the profile with this version's "
            f"profile_bootstrap"
        )
    # Seed table first (a fresh dict), then overlay the measured rows so an
    # unmeasured pair keeps its seed verdict rather than the store's fail-closed
    # default. merge_over_seed=False restores the raw measured-only table.
    table: Dict[Tuple[str, Optional[str]], ProfileEntry] = (
        default_profile_table() if merge_over_seed else {}
    )
    for row in profile["entries"]:
        key = (row["tier_id"], row["work_class"])
        table[key] = ProfileEntry(
            decision=row["decision"],
            quality_score=row["quality_score"],
            sample_n=row["sample_n"],
            last_measured=row.get("last_measured"),
            fingerprint=row.get("fingerprint"),
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


# --- Live path: guarded offline calibration batch (fakes-injected in CI) --------


class LiveBootstrapNotConfigured(RuntimeError):
    """Raised by :func:`run_live` — the live path is a guarded integration step."""


#: A tier's privacy label for a cloud serve (mirrors ``config.PRIVACY_CLOUD``,
#: referenced by literal so this module need not import ``config`` just to filter).
_CLOUD_PRIVACY = "cloud"

#: Default per-request output budget for the live batch. ``>= 4096`` tokens keeps
#: a thinking-by-default local model from spending its whole budget reasoning and
#: returning empty content (CLAUDE.md gotcha #6/#9); a tier that pins thinking off
#: via ``extra_body`` is unaffected. Overridable per call.
_LIVE_MAX_TOKENS = 4096


def _tier_attr(tier: Any, name: str) -> Any:
    """Read ``name`` from a Tier-like value (a Mapping or an attribute object)."""
    if isinstance(tier, Mapping):
        return tier.get(name)
    return getattr(tier, name, None)


def _tier_reasoning(tier: Any) -> Optional[Dict[str, Any]]:
    """The tier's reasoning-config provenance (its ``extra_body``) as a plain dict.

    This is the thinking-on/off (and friends) knob the REAL backend applies to the
    upstream body; recording it on the measured row makes the score's reasoning
    regime explicit (a thinking-ON serve and a thinking-OFF serve are different
    quality regimes — CLAUDE.md gotcha #6/#9). ``None`` when the tier sets none.
    """
    extra_body = _tier_attr(tier, "extra_body")
    return dict(extra_body) if isinstance(extra_body, Mapping) else None


def _warn(msg: str) -> None:
    print(f"[anvil-serving] {msg}", file=sys.stderr, flush=True)


def _grade_score(grade: Any) -> float:
    """Normalize a grader return (``Grade`` / number / ``{"score": ...}``) to ``[0,1]``.

    The shipped grader (:class:`~anvil_serving.router.grader_agentsdk.AgentSDKGrader`)
    returns a :class:`~anvil_serving.router.calibrate.Grade`; this also accepts a
    bare score or a score mapping so an injected fake grader stays simple.
    """
    score = getattr(grade, "score", None)
    if score is None and isinstance(grade, Mapping):
        score = grade.get("score")
    if score is None and isinstance(grade, (int, float)) and not isinstance(grade, bool):
        score = grade
    if score is None:
        raise TypeError(
            f"grader returned {type(grade).__name__}; expected a Grade, a number, "
            f"or a mapping with a 'score' key"
        )
    return max(0.0, min(1.0, float(score)))


def _live_now() -> str:
    """Wall-clock ISO-8601 UTC ``last_measured`` stamp (injectable for tests)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _live_request(tier: Any, work_class: str, prompt: str, max_tokens: int) -> Any:
    """Build the dialect-neutral :class:`InternalRequest` for one live measurement.

    Uses the tier's served ``model`` (so a local serve gets its ``--served-model-name``,
    not the routing token) and its wire ``dialect``; the tier's REAL backend then
    applies ``extra_body`` (thinking-off etc.) to the upstream body byte-identically
    to prod (that merge is the backend's job — genericity:T003).
    """
    from .internal import InternalRequest, Message

    return InternalRequest(
        model=_tier_attr(tier, "model") or work_class,
        messages=[Message(role="user", content=prompt)],
        max_tokens=max_tokens,
        stream=False,
        dialect=_tier_attr(tier, "dialect") or "",
        raw={},
    )


def _collect_output(backend: Any, request: Any) -> str:
    """Drive a backend to completion and join its text deltas into one string."""
    return "".join(backend.generate(request))


def load_live_prompts(eval_data_root: Path) -> Dict[str, List[str]]:
    """Read the committed per-work-class prompt sets from the eval fixtures.

    For each immediate child of ``eval_data_root`` that has a ``prompts/`` subdir,
    derive its work-class from the directory name (:func:`work_class_from_eval_dir`)
    and read every ``prompts/*.txt`` file as one prompt for that class. Pure file
    reads — no network — so it is safe to call from a hermetic test. Returns a
    ``{work_class: [prompt, ...]}`` mapping (empty if no prompt dirs are present).
    """
    eval_data_root = Path(eval_data_root)
    if not eval_data_root.is_dir():
        raise FileNotFoundError(f"eval-data dir not found: {eval_data_root}")
    prompts: Dict[str, List[str]] = {}
    for child in sorted(eval_data_root.iterdir()):
        prompt_dir = child / "prompts"
        if not (child.is_dir() and prompt_dir.is_dir()):
            continue
        work_class, _date = work_class_from_eval_dir(child.name)
        for pf in sorted(prompt_dir.glob("*.txt")):
            prompts.setdefault(work_class, []).append(pf.read_text(encoding="utf-8"))
    return prompts


def run_live(
    *,
    tiers: Optional[Sequence[Any]] = None,
    prompts: Optional[Mapping[str, Sequence[str]]] = None,
    endpoints: Optional[Mapping[str, str]] = None,
    eval_data_root: Optional[Path] = None,
    out_path: Optional[Path] = None,
    confirm_calls_real_tiers: bool = False,
    backend_factory: Optional[Callable[[Any], Any]] = None,
    grader: Optional[Callable[[Mapping[str, Any]], Any]] = None,
    judge: Optional[Callable[[str], Any]] = None,
    now: Optional[Callable[[], str]] = None,
    max_tokens: int = _LIVE_MAX_TOKENS,
    mode: Optional[str] = None,
) -> ProfileStore:
    """GUARDED offline calibration batch — measures LOCAL tiers through their REAL
    backends and writes a reviewable candidate ``profile.json`` (ADR-0009 phase 4).

    The live counterpart to ``--replay``: instead of reading committed pre-graded
    fixtures, for each LOCAL tier x committed work-class prompt it (1) builds the
    tier's REAL backend — so ``Tier.extra_body`` (e.g. thinking-off) is applied
    byte-identically to prod, (2) generates the output, (3) grades it with the
    INDEPENDENT :class:`~anvil_serving.router.grader_agentsdk.AgentSDKGrader` (T004),
    (4) folds the grade into a fresh :class:`ProfileStore` via
    :meth:`~anvil_serving.router.profile_store.ProfileStore.record_grade` with an
    explicit decision (:func:`decision_for_score`) and the serve fingerprint, and
    (5) writes a fingerprint-tagged v2 ``profile.json``. The written file is a
    CANDIDATE the operator reviews and promotes (via
    :data:`~anvil_serving.router.config.RouterConfig.profile_path`); **live routing
    is not touched** — nothing here is auto-promoted.

    Two structural safeties, both non-negotiable:

    * **Guarded.** It binds nothing and refuses (:class:`LiveBootstrapNotConfigured`)
      unless the operator passes real ``endpoints`` *and* ``confirm_calls_real_tiers=
      True`` — so no unit test / CI path can reach a tier by accident.
    * **LOCAL tiers only; never self-verify.** A ``privacy == "cloud"`` tier is
      filtered out and never measured (the Claude judge grading a Claude/cloud tier
      is self-verification — CLAUDE.md). As defense-in-depth, if a mislabeled tier
      still reaches the grader, the grader raises
      :class:`~anvil_serving.router.grader_agentsdk.SelfVerificationError`, which is
      surfaced and skipped cleanly rather than crashing the batch.

    Injectable seams keep the tests hermetic (ZERO network / subprocess): ``tiers``
    (the local :class:`~anvil_serving.router.config.Tier` set — the real path passes
    the operator's configured tiers), ``prompts`` (``{work_class: [prompt, ...]}``;
    absent -> :func:`load_live_prompts` reads the committed fixtures under
    ``eval_data_root``), ``backend_factory`` (tier -> Backend; default builds the
    REAL backend), ``grader`` (a ready grader) or ``judge`` (the Agent-SDK judge seam
    a default :class:`AgentSDKGrader` is built around), and ``now`` (the
    ``last_measured`` clock). With nothing injected the DEFAULTS are the real path,
    exercised only in production.
    """
    # --- guard: never run unless endpoints + explicit confirmation (unchanged) ---
    if not confirm_calls_real_tiers or not endpoints:
        raise LiveBootstrapNotConfigured(
            "run_live is the live integration step: it calls real serving tiers "
            "and is never run by the unit test / CI. Pass real `endpoints` and "
            "`confirm_calls_real_tiers=True` to run it. Use --replay for the "
            "offline, CI-safe bootstrap."
        )

    # --- resolve the committed work-class prompt set ---
    if prompts is None:
        if eval_data_root is None:
            raise LiveBootstrapNotConfigured(
                "run_live needs a prompt set: pass `prompts` ({work_class: [...]}) "
                "or `eval_data_root` (committed prompts are read from its "
                "<eval>/prompts/*.txt)."
            )
        prompts = load_live_prompts(Path(eval_data_root))
    prompt_sets: Dict[str, List[str]] = {
        wc: [p for p in ps] for wc, ps in prompts.items() if ps
    }
    if not prompt_sets:
        raise LiveBootstrapNotConfigured("run_live has no work-class prompts to measure")

    # --- resolve the tiers; LOCAL only (cloud/Claude tiers are structurally refused) ---
    all_tiers = list(tiers or ())
    if not all_tiers:
        raise LiveBootstrapNotConfigured(
            "run_live needs the local tiers to measure: pass `tiers` (the operator's "
            "configured LOCAL Tier objects). The CLI --live stub cannot supply them; "
            "drive the live batch programmatically (or via the T016 calibration loop)."
        )
    local_tiers = [t for t in all_tiers if _tier_attr(t, "privacy") != _CLOUD_PRIVACY]
    refused = [
        _tier_attr(t, "id") for t in all_tiers if _tier_attr(t, "privacy") == _CLOUD_PRIVACY
    ]
    if refused:
        _warn(
            f"run_live: NOT measuring cloud tier(s) {refused} — a Claude judge must "
            f"never grade a cloud/Claude tier (no self-verification); local tiers only."
        )

    # Safety net (Copilot / critic review): `endpoints` must COVER every local tier
    # we are about to dial. Otherwise it is a vestigial confirmation token — an
    # operator could pass unrelated endpoints (or omit one) and still hit a real
    # backend they never confirmed. Require each measured tier's id in `endpoints`.
    uncovered = [
        tid
        for t in local_tiers
        if (tid := _tier_attr(t, "id")) is not None and tid not in endpoints
    ]
    if uncovered:
        raise LiveBootstrapNotConfigured(
            f"run_live: `endpoints` does not cover local tier(s) {uncovered} being "
            f"measured — list each measured tier's id in `endpoints` (its confirmed "
            f"serving URL) so the batch never dials a tier you did not confirm."
        )

    now_fn = now or _live_now

    # Default grader: the INDEPENDENT Agent-SDK grader over these tiers. Its judge
    # seam defaults to the real `claude` CLI; tests inject `judge` (a fake) or a
    # ready `grader`, so CI makes no LLM/subprocess call. (Lazy import: grader_agentsdk
    # imports THIS module, so a top-level import here would be circular.)
    if grader is None:
        from .grader_agentsdk import AgentSDKGrader

        grader = AgentSDKGrader(tiers=all_tiers, judge=judge)

    # The grader's structural no-self-verification refusal (defense-in-depth for a
    # mislabeled tier that slipped the privacy filter).
    from .grader_agentsdk import SelfVerificationError

    # Default backend factory: build each tier's REAL backend so `extra_body` is
    # applied byte-identically to prod. (Lazy import: serve pulls in the whole
    # router; only the real path needs it.)
    if backend_factory is None:
        from .serve import build_backend_for_tier

        def backend_factory(t: Any) -> Any:  # noqa: E306 - local default seam
            timeout = _tier_attr(t, "timeout")
            return build_backend_for_tier(t, timeout=timeout if timeout is not None else 120.0)

    from .fingerprint import serve_fingerprint

    # Measured-only accumulator: record_grade folds each grade into a FRESH row so
    # the candidate reflects MEASURED numbers (not blended with the authored seed).
    acc_store = ProfileStore({})
    # Side metadata the store row does not carry (model label, fingerprint, reasoning).
    meta: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for tier in local_tiers:
        tier_id = str(_tier_attr(tier, "id"))
        # Thread the active serving mode into the candidate fingerprint (ADR-0011 /
        # flexibility:T013) so a profile measured under `--mode flexibility` MATCHES
        # the live flexibility-mode serve's mode-tagged fingerprint (else every row
        # would read stale after promotion). mode=None reproduces the pre-T013 digest.
        fingerprint = serve_fingerprint(tier, mode=mode)
        reasoning = _tier_reasoning(tier)
        model_label = _tier_attr(tier, "model") or tier_id
        try:
            backend = backend_factory(tier)
        except Exception as exc:  # noqa: BLE001 - a bad tier is surfaced, never fatal
            _warn(f"run_live: skipping tier {tier_id!r}: backend build failed: {exc}")
            continue

        refused_tier = False
        for work_class, class_prompts in prompt_sets.items():
            if refused_tier:
                break
            for prompt in class_prompts:
                try:
                    output = _collect_output(backend, _live_request(
                        tier, work_class, prompt, max_tokens
                    ))
                except Exception as exc:  # noqa: BLE001 - a generate failure is surfaced
                    _warn(
                        f"run_live: tier {tier_id!r} / {work_class!r} generate failed, "
                        f"skipping this prompt: {exc}"
                    )
                    continue
                sample = {
                    "tier_id": tier_id,
                    "work_class": work_class,
                    "tier": tier,  # so the grader guards on the REAL tier family
                    "request": {"prompt": prompt},
                    "response": output,
                }
                try:
                    grade = grader(sample)
                except SelfVerificationError as exc:
                    # A mislabeled cloud/Claude tier that slipped the privacy filter:
                    # REFUSED by the grader. Skip the whole tier cleanly — never grade
                    # a judge's own family, never crash the batch.
                    _warn(
                        f"run_live: refusing to measure tier {tier_id!r} "
                        f"(no self-verification): {exc}"
                    )
                    refused_tier = True
                    break
                score = _grade_score(grade)
                acc_store.record_grade(
                    tier_id,
                    work_class,
                    score=score,
                    decision=decision_for_score(score),
                    last_measured=now_fn(),
                    submitted_fingerprint=fingerprint,
                )
                meta.setdefault(
                    (tier_id, work_class),
                    {
                        "model_label": model_label,
                        "fingerprint": fingerprint,
                        "reasoning": reasoning,
                    },
                )

    # Build the fingerprint-tagged v2 candidate from the measured rows.
    entries: List[BootstrapEntry] = []
    for (tier_id, work_class), m in meta.items():
        row = acc_store.entry(tier_id, work_class)
        if row is None:  # pragma: no cover - a measured pair always has a row
            continue
        score = round(row.quality_score, _SCORE_DP)
        entries.append(
            BootstrapEntry(
                tier_id=tier_id,
                work_class=work_class,
                model_label=m["model_label"],
                decision=decision_for_score(score),
                quality_score=score,
                sample_n=row.sample_n,
                eval_total_avg=round(score * EVAL_MAX, _TOTAL_DP),
                eval_max=EVAL_MAX,
                source_evals=("live",),
                last_measured=row.last_measured,
                fingerprint=m["fingerprint"],
                reasoning=m["reasoning"],
            )
        )
    entries.sort(key=lambda e: (e.tier_id, e.work_class))
    if not entries:
        _warn("run_live: measured no local rows (no local tiers, or all were skipped)")

    profile = {
        "schema": SCHEMA,
        "mode": "live",
        "eval_max": EVAL_MAX,
        "entries": [e.to_dict() for e in entries],
    }
    if out_path is not None:
        Path(out_path).write_text(serialize_profile(profile), encoding="utf-8")

    # Return the routable candidate store (measured rows MERGED OVER the seed, so an
    # unmeasured class keeps its seed verdict) — the same load shape as --replay.
    return store_from_profile(profile)


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
        except LiveBootstrapNotConfigured as exc:
            # The CLI --live flag confirms intent but cannot supply the LOCAL Tier
            # objects (+ prompts) run_live measures, so it stops at the guard and
            # exits cleanly: the live batch is driven programmatically (or by the
            # T016 calibration loop), never dialing a tier from this stub.
            print(f"--live not fully configured: {exc}", file=sys.stderr)
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
