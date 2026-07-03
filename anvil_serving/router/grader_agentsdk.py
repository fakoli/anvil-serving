"""Independent Agent-SDK quality grader (flexibility:T004, ADR-0009).

The concrete product ``Grader`` the off-hot-path
:class:`~anvil_serving.router.calibrate.Calibrator` expects. The Calibrator's
grader is an INJECTED ``(redacted_sample) -> grade`` callable with, until now, no
product implementation; this module ships that implementation.

What it does — one sentence: it renders the committed 5-dimension DIMS ``/25``
rubric (shared with :mod:`~anvil_serving.router.profile_bootstrap`) into a judge
prompt, asks an **independent Claude judge via the Claude Agent SDK** to score the
tier's output, validates the returned per-dimension JSON (``total`` MUST equal the
sum of the dimensions), and returns a normalized ``[0, 1]``
:class:`~anvil_serving.router.calibrate.Grade`.

Two golden rules are structural, not advisory:

1. **Agent SDK, never the raw API.** The real judge shells out to the ``claude``
   CLI (the sanctioned Agent-SDK subscription path per
   :doc:`ADR-0007 </adr/0007-subscription-auth-cloud-tier>`) — never the ``anthropic``
   SDK or the raw Anthropic REST endpoint. That call lives behind an INJECTABLE seam
   (``judge=``) so tests pass a fake judge and CI makes ZERO network/LLM calls.
   The seam's real body (:func:`_claude_cli_judge`) imports ``subprocess`` lazily,
   scrubs ``ANTHROPIC_API_KEY`` from the child env (so a metered key can't outrank
   the subscription OAuth token — ADR-0007), and never uses ``--bare``.

2. **No self-verification.** The judge is Claude, so grading a Claude/cloud tier
   would be a model checking its own family (CLAUDE.md's "never self-verify").
   :meth:`AgentSDKGrader.grade` therefore STRUCTURALLY REFUSES: it resolves the
   graded tier's family and RAISES :class:`SelfVerificationError` *before any
   judge call* if that family matches the judge — or if independence cannot be
   positively proven (fail-closed). There is no flag to bypass it.

Stdlib-only at import (``json`` / ``os`` / ``re``); ``subprocess`` is imported
only inside the real-call seam, so importing this module costs nothing and the
router hot path is never touched (this grader runs OFF the hot path, in the
offline batch / async calibrator only).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .calibrate import Grade
from .profile_bootstrap import DIMS, EVAL_MAX, decision_for_score

__all__ = [
    "AgentSDKGrader",
    "GraderError",
    "SelfVerificationError",
    "JudgeProtocolError",
    "DEFAULT_JUDGE_FAMILY",
]

#: The family the default judge belongs to. The judge is Claude, so any graded
#: tier that resolves to this family is refused (self-verification).
DEFAULT_JUDGE_FAMILY = "claude"

#: Max points per rubric dimension (the ``/25`` rubric is 5 dims x 5). Derived
#: from the shared rubric so it can never drift from ``profile_bootstrap``.
_MAX_PER_DIM = int(round(EVAL_MAX / len(DIMS)))

#: Prompt-size guard: cap each serialized side (request / response) so a huge
#: exchange can't blow the judge's context. Truncation is marked in the text.
_MAX_SIDE_CHARS = 12000

#: One-line gloss per DIMS dimension, so the judge knows what each 0-5 axis means.
#: Faithful to the committed planning-capability rubric.
_DIM_GLOSS: Mapping[str, str] = {
    "decomposition_granularity": (
        "tasks are right-sized — each multi-part requirement is split into atomic, "
        "independently-shippable tasks (not mega-tasks, not over-split)."
    ),
    "requirement_coverage": (
        "every requirement in the input is covered by at least one task; nothing "
        "dropped or left implicit."
    ),
    "dependency_correctness": (
        "declared task dependencies are real and correctly directed — no spurious "
        "or missing edges, no cycles."
    ),
    "acceptance_verifiability": (
        "each task carries concrete, machine-checkable acceptance criteria / "
        "verification commands."
    ),
    "faithfulness": (
        "the plan stays faithful to the input — correct stack, no hallucinated "
        "tasks/tools/flags, no invented scope."
    ),
}


# --- Errors ---------------------------------------------------------------------


class GraderError(RuntimeError):
    """Base class for any Agent-SDK grader failure."""


class SelfVerificationError(GraderError):
    """Refusal to grade a tier whose family matches the judge (or is unprovable).

    Raised BEFORE any judge call, so no output from a judge-family tier ever
    reaches the judge. This is the structural "never self-verify" guard and has
    no bypass flag.
    """


class JudgeProtocolError(GraderError):
    """The judge returned something that isn't a valid scored-rubric JSON object.

    Per ADR-0009 this is FLAGGED (raised), never silently repaired and never a
    fallback to the raw API.
    """


# --- Family resolution (the independence guard) ---------------------------------


def _get(tier: Any, key: str) -> Any:
    """Read ``key`` from a Tier-like value (a Mapping or an attribute object)."""
    if tier is None:
        return None
    if isinstance(tier, Mapping):
        return tier.get(key)
    return getattr(tier, key, None)


def _norm_str(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _normalize_family(name: Any) -> Optional[str]:
    """Canonicalize a family label; ``None``/empty -> ``None``.

    Anything mentioning Claude/Anthropic collapses to ``"claude"`` so the judge
    family and a graded Claude tier compare equal however they were spelled.
    """
    n = _norm_str(name)
    if not n:
        return None
    if "claude" in n or "anthropic" in n:
        return "claude"
    if "openai" in n or n.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    if "gemini" in n or "google" in n:
        return "gemini"
    return n


def _family_from_model(model: Any) -> Optional[str]:
    """Best-effort family from a concrete model id (e.g. ``claude-opus-4`` -> claude)."""
    m = _norm_str(model)
    if not m:
        return None
    if "claude" in m or "anthropic" in m:
        return "claude"
    if "gpt" in m or "openai" in m or m.startswith(("o1", "o3", "o4")):
        return "openai"
    if "gemini" in m:
        return "gemini"
    for fam in ("qwen", "llama", "mistral", "deepseek", "phi", "gemma", "glm", "yi"):
        if fam in m:
            return fam
    # Unknown but non-empty -> its own family (definitively NOT the Claude judge).
    return m


def _family_of_tier(tier: Any) -> Optional[str]:
    """Resolve a graded tier's model family, or ``None`` if it can't be determined.

    Order matters: Claude is detected FIRST (explicit label, model id, or an
    ``anthropic`` dialect) so a Claude tier can never be waved through as
    something else. Only then does a ``local`` tier resolve to the safe ``local``
    family (a local serve is inherently independent of the cloud Claude judge).
    A cloud tier with no identifying signal returns ``None`` — the guard treats
    that as unprovable independence and refuses (fail-closed).
    """
    if tier is None:
        return None

    explicit = _normalize_family(_get(tier, "family"))
    if explicit:
        return explicit

    if _family_from_model(_get(tier, "model")) == "claude":
        return "claude"
    if _norm_str(_get(tier, "dialect")) == "anthropic":
        return "claude"

    model_family = _family_from_model(_get(tier, "model"))
    if model_family:
        return model_family

    if _norm_str(_get(tier, "privacy")) == "local":
        return "local"
    if _norm_str(_get(tier, "dialect")) == "openai":
        return "openai"
    return None


# --- Judge prompt render + response validation ----------------------------------


def _clip(text: Optional[str], limit: int = _MAX_SIDE_CHARS) -> str:
    if text is None:
        return "null"
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _dump(value: Any) -> str:
    return _clip(json.dumps(value, indent=2, ensure_ascii=False, default=str, sort_keys=True))


def _rubric_block() -> str:
    return "\n".join(f"- {d} (0-{_MAX_PER_DIM}): {_DIM_GLOSS[d]}" for d in DIMS)


def _schema_example() -> str:
    dims = ", ".join(f'"{d}": <int 0-{_MAX_PER_DIM}>' for d in DIMS)
    return (
        '{"scores": {' + dims + '}, '
        '"total": <int, MUST equal the sum of the five dimension scores>, '
        '"notes": "<one short sentence justifying the scores>"}'
    )


def _render_prompt(sample: Mapping[str, Any]) -> str:
    """Render the redacted sample + committed rubric into the judge prompt."""
    work_class = sample.get("work_class") or "unspecified"
    request = _dump(sample.get("request"))
    response = _dump(sample.get("response"))
    return (
        "You are an INDEPENDENT quality judge. Score ONLY the MODEL RESPONSE below "
        "against the rubric. You did not write it; judge it adversarially.\n\n"
        f"Work class: {work_class}\n\n"
        f"Rubric — score each dimension 0 to {_MAX_PER_DIM} (whole numbers):\n"
        f"{_rubric_block()}\n\n"
        "Reply with a SINGLE JSON object and nothing else, in EXACTLY this shape:\n"
        f"{_schema_example()}\n"
        f"The total is out of {int(EVAL_MAX)} and MUST equal the sum of the "
        f"{len(DIMS)} dimension scores.\n\n"
        "----- REQUEST (the task the model was given) -----\n"
        f"{request}\n\n"
        "----- MODEL RESPONSE (grade THIS) -----\n"
        f"{response}\n\n"
        "Return ONLY the JSON object described above."
    )


def _extract_json_object(raw: Any) -> Dict[str, Any]:
    """Coerce a judge reply into a JSON object (tolerant of prose / code fences)."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str):
        raise JudgeProtocolError(
            f"judge returned {type(raw).__name__}; expected JSON text or a mapping"
        )
    text = raw.strip()
    fence = re.match(r"^```[a-zA-Z0-9]*\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise JudgeProtocolError("judge output is not JSON")
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise JudgeProtocolError(f"judge output is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise JudgeProtocolError("judge JSON is not an object")
    return obj


def _validate_scores(obj: Mapping[str, Any]) -> Tuple[Dict[str, int], int, str]:
    """Validate the scored-rubric JSON; return ``(scores, total, notes)``.

    Enforces: every DIMS dimension present, each a whole number in
    ``[0, _MAX_PER_DIM]``, no unknown dimensions, and ``total`` equal to the sum
    of the dimensions — the same cross-check ``profile_bootstrap.load_judge_rows``
    applies to the committed fixtures.
    """
    scores = obj.get("scores")
    if not isinstance(scores, Mapping):
        raise JudgeProtocolError("judge JSON missing a 'scores' object")

    parsed: Dict[str, int] = {}
    for d in DIMS:
        if d not in scores:
            raise JudgeProtocolError(f"judge JSON missing dimension {d!r}")
        v = scores[d]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise JudgeProtocolError(f"dimension {d!r} score must be a number, got {v!r}")
        iv = int(v)
        if iv != v or not (0 <= iv <= _MAX_PER_DIM):
            raise JudgeProtocolError(
                f"dimension {d!r} score {v!r} is not a whole number in 0-{_MAX_PER_DIM}"
            )
        parsed[d] = iv

    extra = set(scores) - set(DIMS)
    if extra:
        raise JudgeProtocolError(f"judge JSON has unknown dimensions: {sorted(extra)}")

    if "total" not in obj:
        raise JudgeProtocolError("judge JSON missing 'total'")
    total = obj["total"]
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        raise JudgeProtocolError(f"'total' must be a number, got {total!r}")
    dim_sum = sum(parsed.values())
    if int(total) != total or int(total) != dim_sum:
        raise JudgeProtocolError(
            f"judge total {total!r} != sum(dimensions) {dim_sum}"
        )

    notes = obj.get("notes")
    return parsed, dim_sum, notes if isinstance(notes, str) else ""


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


# --- The real Agent-SDK judge seam (never called by tests) ----------------------


def _extract_cli_text(stdout: Optional[str]) -> str:
    """Pull the assistant text out of ``claude -p --output-format json`` output."""
    stdout = (stdout or "").strip()
    if not stdout:
        raise JudgeProtocolError("empty output from the `claude` judge")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout  # already plain text (older CLIs / --output-format text)
    if isinstance(payload, dict):
        for key in ("result", "text", "content", "response"):
            v = payload.get(key)
            if isinstance(v, str):
                return v
    return stdout


def _claude_cli_judge(prompt: str, *, model: Optional[str] = None, timeout: float = 180.0) -> str:
    """Real judge: shell out to the ``claude`` CLI — the Agent-SDK subscription path.

    This is the ONLY place a real model call happens, and it is the sanctioned
    Agent-SDK route from ADR-0007: a headless ``claude -p`` subprocess on the
    operator's Pro/Max subscription — NOT the raw ``anthropic`` SDK and NOT the
    raw Anthropic REST endpoint. ``ANTHROPIC_API_KEY`` is scrubbed from the child
    environment so a metered key can't outrank (and silently bill over) the
    subscription OAuth token; ``--bare`` is never used (both per ADR-0007).

    ``subprocess`` is imported lazily here so importing the module — and the
    entire test suite — never needs the CLI. Requires the ``claude`` CLI on PATH
    at call time only.
    """
    import subprocess  # lazy: only the real call needs it (never at import/in tests)

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # ADR-0007: subscription OAuth, never metered API
    argv = ["claude", "-p", "--output-format", "json"]
    if model:
        argv += ["--model", model]
    try:
        proc = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GraderError(
            "the `claude` CLI is not on PATH; the Agent-SDK judge requires it "
            "(mint a token with `claude setup-token`; see ADR-0007)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GraderError(f"the `claude` judge timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise GraderError(
            f"the `claude` judge exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
        )
    return _extract_cli_text(proc.stdout)


# --- The grader -----------------------------------------------------------------

#: A judge seam: a prompt -> the judge's raw reply (JSON text, or an already
#: parsed mapping). The default is :func:`_claude_cli_judge`; tests inject a fake.
Judge = Callable[[str], Any]

#: A tier-family resolver: a Tier-like -> its family label (or ``None``).
FamilyResolver = Callable[[Any], Optional[str]]


class AgentSDKGrader:
    """Independent Claude-via-Agent-SDK quality grader (the concrete ``Grader``).

    Callable as ``grader(redacted_sample) -> Grade`` — exactly the contract
    :class:`~anvil_serving.router.calibrate.Calibrator` injects — so it drops in
    as the Calibrator's ``grader=``. It is also the grader the offline batch
    (``run_live``, ADR-0009) calls directly.

    Parameters
    ----------
    tiers:
        The tiers this grader may score, used to resolve a sampled ``tier_id`` to
        its model family for the independence guard. A mapping ``id -> Tier`` or
        any iterable of Tier-like objects (each exposing ``id`` + ``model`` /
        ``privacy`` / ``dialect``). A sample that carries its own ``tier``
        metadata is used in preference to this registry.
    judge:
        INJECTABLE Agent-SDK seam ``prompt -> reply``. Defaults to the real
        :func:`_claude_cli_judge` (``claude`` CLI subprocess). Tests pass a fake
        so CI makes no network/LLM call.
    judge_family:
        The judge's own model family (default ``"claude"``). A graded tier that
        resolves to this family is REFUSED.
    model:
        Optional concrete judge model id forwarded to the default CLI seam.
    family_of:
        Optional override for tier-family resolution (tests / custom taxonomies).
    """

    def __init__(
        self,
        *,
        tiers: Any = None,
        judge: Optional[Judge] = None,
        judge_family: str = DEFAULT_JUDGE_FAMILY,
        model: Optional[str] = None,
        family_of: Optional[FamilyResolver] = None,
    ) -> None:
        self._tiers: Dict[str, Any] = {}
        if tiers:
            if isinstance(tiers, Mapping):
                self._tiers = dict(tiers)
            else:
                for t in tiers:
                    tid = _get(t, "id")
                    if tid:
                        self._tiers[str(tid)] = t

        if judge is not None:
            self._judge: Judge = judge
        else:
            self._judge = lambda prompt: _claude_cli_judge(prompt, model=model)

        jf = _normalize_family(judge_family)
        if jf is None:
            raise GraderError("judge_family must be a non-empty family label")
        self._judge_family = jf
        self._family_of = family_of or _family_of_tier

    # -- public API --------------------------------------------------------------

    def grade(self, sample: Mapping[str, Any]) -> Grade:
        """Score one redacted exchange; return a normalized ``[0, 1]`` :class:`Grade`.

        Refuses (raises :class:`SelfVerificationError`) BEFORE any judge call if
        the graded tier's family matches the judge's — or can't be proven
        independent. ``decision`` is left ``None`` (ADR-0009 §Decision.2): a bare
        quality number never flips the load-bearing ``deny`` gate; the offline
        batch derives the explicit decision via
        :func:`~anvil_serving.router.profile_bootstrap.decision_for_score`.
        """
        if not isinstance(sample, Mapping):
            raise GraderError(f"sample must be a mapping, got {type(sample).__name__}")

        tier, tier_id = self._resolve_tier(sample)
        self._guard_independence(tier, tier_id)  # RAISES before any judge call

        reply = self._judge(_render_prompt(sample))
        _scores, total, notes = _validate_scores(_extract_json_object(reply))
        return Grade(score=_clamp01(total / EVAL_MAX), decision=None, notes=notes)

    __call__ = grade

    def decision_for(self, sample: Mapping[str, Any]) -> str:
        """Convenience: the derived trust decision for this sample's graded score.

        Uses the SAME thresholds as ``profile_bootstrap.decision_for_score`` so an
        offline batch that wants an explicit decision (ADR-0009 §Decision.3) gets
        one consistent with the seed/replay path. Also runs the independence
        guard.
        """
        return decision_for_score(self.grade(sample).score)

    # -- internals ---------------------------------------------------------------

    def _resolve_tier(self, sample: Mapping[str, Any]) -> Tuple[Any, Optional[str]]:
        """Resolve the graded tier: prefer inline ``sample['tier']``, else registry."""
        inline = sample.get("tier")
        if inline is not None:
            return inline, (sample.get("tier_id") or _get(inline, "id"))
        tier_id = sample.get("tier_id")
        return self._tiers.get(str(tier_id)) if tier_id is not None else None, tier_id

    def _guard_independence(self, tier: Any, tier_id: Optional[str]) -> str:
        """The unbypassable no-self-verification gate.

        Refuses when the graded family equals the judge family, and (fail-closed)
        when independence can't be positively established.
        """
        family = self._family_of(tier)
        if family is not None and family == self._judge_family:
            raise SelfVerificationError(
                f"refusing to grade tier {tier_id!r}: its family {family!r} matches "
                f"the judge family {self._judge_family!r} — a judge must never grade "
                f"its own family (CLAUDE.md: never self-verify; ADR-0009 §Decision.2). "
                f"Grading this tier needs a genuinely independent non-Claude judge, "
                f"which is a human decision (it reopens the raw-API/other-provider gate)."
            )
        if family is None:
            raise SelfVerificationError(
                f"refusing to grade tier {tier_id!r}: could not positively determine "
                f"its model family, so independence from the {self._judge_family!r} "
                f"judge cannot be proven. Register the tier (id + model/privacy/"
                f"dialect) or pass explicit tier metadata."
            )
        return family
