"""Cheap inline structural verifiers — the tier-2 safety net (T007).

These are the *cheap structural verify* checks from §7 of
``docs/QUALITY-GATED-ROUTER.md``: near-zero-cost, purely local checks that run
on an assembled model response and catch the structurally-broken outputs we saw
in our eval — empty/truncated content (thinking-budget starvation), tool-call
JSON that does not validate, code that does not parse, a diff that is not
well-formed, a malformed format. On a check failure the router (T008/T009) falls
back to the next tier; T007 builds ONLY the checks plus the chain runner — not
routing, tier selection, fallback, or the streaming commit window.

**Governing principle.** A structural verifier returns ``passed=False`` only
when it can genuinely *prove* a defect. Input it merely cannot evaluate ("not a
language I can parse", "not actually a diff") passes; malformed/huge/adversarial
input yields a fail *verdict*, never a raised exception. A verifier must never
crash the chain — :func:`run_verifiers` additionally backstops any unforeseen
exception into a fail verdict.

**Purely local, no I/O.** Every check uses only the stdlib (``json``, ``ast``,
``re``); none of them open a socket, make an HTTP request, or call an LLM. That
property is part of the acceptance gate and is pinned by a test in
``tests/router/test_verify.py`` that patches the network surfaces to raise.

Stdlib-only by design. This module defines:

* :class:`ResponseView` — the minimal response shape the checks consume,
  decoupled from any wire dialect (T008/T009 adapt an assembled response into it).
* :class:`VerifyResult` — one check's verdict (pass/fail + score + reason).
* :class:`Verifier` — a minimal ``typing.Protocol`` seam (T011 formalizes the
  registry). Concrete checks below each implement it.
* :func:`run_verifiers` / :func:`aggregate` / :func:`all_passed` — the chainable
  runner so a caller can run a chain and fail on the first/any failure.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # Protocol is stdlib from 3.8+; runtime_checkable lets isinstance() work.
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover - 3.7 fallback, unused at >=3.9
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


# --------------------------------------------------------------------------- #
# data shapes
# --------------------------------------------------------------------------- #
@dataclass
class ResponseView:
    """The minimal, dialect-neutral view of a model response a check consumes.

    T008/T009 adapt an assembled Anthropic/OpenAI response into this; keeping it
    self-contained here (rather than coupling to ``internal.py``, which is
    request-focused) means the checks never need to know the wire dialect.

    Fields:
      * ``text`` — the assembled assistant text content.
      * ``finish_reason`` — the normalized stop reason. "stop"/"end_turn"/
        "stop_sequence"/"tool_calls" read as a clean stop; "length"/"max_tokens"
        read as truncation. ``None`` means unknown (not penalized).
      * ``tool_calls`` — each normally a dict carrying a string ``arguments``
        field as the wire carries it (OpenAI-style ``function.arguments`` is also
        accepted). Native Anthropic ``tool_use.input`` arrives already parsed as
        a dict/list — that is accepted as valid by construction.
      * ``expected_format`` — optional hint, e.g. "json", that the *whole* reply
        is expected to be in that format.
      * ``expected_language`` — optional hint, e.g. "python", used to type an
        untagged fenced code block.
    """

    text: str = ""
    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    expected_format: Optional[str] = None
    expected_language: Optional[str] = None


@dataclass
class VerifyResult:
    """One verifier's verdict.

    ``score`` is a 0.0–1.0 confidence/quality signal (1.0 = clean pass, 0.0 =
    hard fail); some checks emit a fractional score (e.g. the share of tool
    calls whose arguments parsed). ``passed`` is the hard pass/fail the chain
    aggregates on — a check may pass while still lowering ``score`` (see
    :class:`RefusalMarker`, a confidence signal that never hard-fails).
    """

    verifier: str
    passed: bool
    score: float
    reason: str


@runtime_checkable
class Verifier(Protocol):
    """The verify seam: inspect a :class:`ResponseView`, return a verdict.

    Minimal by intent — T011 formalizes a seam/registry. A verifier carries a
    stable ``name`` (used in :class:`VerifyResult.verifier` and in logs) and a
    pure, side-effect-free :meth:`verify`.
    """

    name: str

    def verify(self, response: ResponseView) -> VerifyResult:
        ...


# --------------------------------------------------------------------------- #
# small local helpers (stdlib only — no I/O)
# --------------------------------------------------------------------------- #
# A backtick-fenced block, handling both forms and adversarial bodies:
#   * multi-line: ```lang\n <body> \n``` — the closing fence must follow a
#     newline (i.e. start its own line), so an inline ``` inside a string in the
#     body does NOT prematurely close the block.
#   * single-line: ```lang <body>``` on one line (no newline after the lang tag).
# Group 1 = lang tag; group 2 = multi-line body; group 3 = single-line body.
_FENCE_RE = re.compile(
    r"```[ \t]*([A-Za-z0-9_.+-]*)[ \t]*"
    r"(?:"
    r"\r?\n(.*?)\r?\n[ \t]*```"   # multi-line body; close ``` at (indented) line start
    r"|"
    r"[ \t]*(.*?)```"             # single-line body; close ``` on the same line
    r")",
    re.DOTALL,
)

# A unified-diff hunk header, e.g. ``@@ -1,3 +2,4 @@`` (optionally trailing a
# section heading). ``re.match`` is unanchored at the end on purpose.
_HUNK_RE = re.compile(r"^@@ -\d+(,\d+)? \+\d+(,\d+)? @@")
# Same pattern, used to *detect* a real hunk header anywhere in the text.
_HUNK_SEARCH_RE = re.compile(r"^@@ -\d+(,\d+)? \+\d+(,\d+)? @@", re.MULTILINE)

# Lines that introduce a (possibly new) file in a diff and close any open hunk.
_DIFF_HEADER_PREFIXES = ("diff ", "--- ", "+++ ", "index ", "Index:")

# Languages we can *actually* parse cheaply — the only ones we will hard-fail on.
_PARSE_LANGS_PY = {"python", "py", "python3"}
_PARSE_LANGS_JSON = {"json"}

_TRUNCATION_REASONS = {"length", "max_tokens", "model_length"}

# Obvious refusal / uncertainty openers. Heuristic only — a confidence signal,
# never a hard fail. Matched case-insensitively as substrings.
_REFUSAL_MARKERS = (
    "i can't help with",
    "i cannot help with",
    "i can't assist with",
    "i cannot assist with",
    "i'm not able to",
    "i am not able to",
    "i'm unable to",
    "i am unable to",
    "i won't be able to",
    "i will not be able to",
    "as an ai language model",
    "i'm sorry, but i can't",
    "i'm sorry, but i cannot",
)


def _reject_nonspec_constant(token: str) -> Any:
    """``parse_constant`` hook: reject non-spec JSON (NaN/Infinity/-Infinity).

    ``json.loads`` accepts these by default, but strict downstream parsers (and
    most tool executors / config loaders) reject them, so for a structural
    validity gate they must not pass. Raising here makes ``json.loads`` raise.
    """
    raise ValueError(f"non-spec JSON constant: {token}")


def _strict_json_loads(s: str) -> Any:
    """``json.loads`` that rejects NaN/Infinity (spec-strict)."""
    return json.loads(s, parse_constant=_reject_nonspec_constant)


def iter_code_blocks(text: str) -> List[Tuple[str, str]]:
    """Return ``(lang_lowercased, body)`` for each backtick-fenced block."""
    out: List[Tuple[str, str]] = []
    for m in _FENCE_RE.finditer(text or ""):
        lang = (m.group(1) or "").lower()
        body = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        out.append((lang, body))
    return out


def _sole_fenced_body(text: str) -> Optional[str]:
    """If ``text`` (stripped) is *exactly* one fenced block, return its body.

    "Exactly one" means the fence spans the entire stripped text — the match
    must start at position 0 and consume to the end. A single fence followed by
    trailing prose (even prose that ends in backticks) is therefore NOT treated
    as the sole block, so a format check still validates the whole impure output.
    """
    stripped = (text or "").strip()
    m = _FENCE_RE.match(stripped)
    if m and m.end() == len(stripped):
        return m.group(2) if m.group(2) is not None else (m.group(3) or "")
    return None


def _tool_arguments(tool_call: Dict[str, Any]) -> Any:
    """Pull the wire ``arguments`` blob, top-level or nested under ``function``."""
    if "arguments" in tool_call:
        return tool_call.get("arguments")
    fn = tool_call.get("function")
    if isinstance(fn, dict):
        return fn.get("arguments")
    return None


def _tool_name(tool_call: Dict[str, Any]) -> str:
    """Best-effort tool name, top-level or nested under ``function``."""
    if "name" in tool_call:
        return str(tool_call.get("name") or "")
    fn = tool_call.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or "")
    return ""


# --------------------------------------------------------------------------- #
# concrete structural checks (each a Verifier; each purely local)
# --------------------------------------------------------------------------- #
class NonEmptyContent:
    """Fail when there is no usable content at all.

    Empty/whitespace ``text`` *and* no tool calls is the empty-content /
    thinking-budget-starvation failure we saw in the eval. A tool-only response
    (empty text but a tool call) passes — that is legitimately non-empty.
    """

    name = "non_empty_content"

    def verify(self, response: ResponseView) -> VerifyResult:
        has_text = bool((response.text or "").strip())
        has_tools = bool(response.tool_calls)
        if has_text or has_tools:
            return VerifyResult(self.name, True, 1.0, "non-empty content")
        return VerifyResult(
            self.name, False, 0.0,
            "empty content and no tool calls (thinking-budget starvation?)",
        )


class NotTruncated:
    """Fail when ``finish_reason`` indicates the output was cut off.

    "length"/"max_tokens"/"model_length" → truncated (fail). A clean stop or an
    unknown (``None``) reason passes — we only flag a *known* truncation.
    """

    name = "not_truncated"

    def verify(self, response: ResponseView) -> VerifyResult:
        reason = (response.finish_reason or "").strip().lower()
        if reason in _TRUNCATION_REASONS:
            return VerifyResult(
                self.name, False, 0.0,
                f"response truncated (finish_reason={response.finish_reason!r})",
            )
        return VerifyResult(
            self.name, True, 1.0,
            f"clean stop (finish_reason={response.finish_reason!r})",
        )


class ToolCallJSONValid:
    """Every tool call's arguments must be valid (parseable / well-structured).

    Per tool call:
      * a non-``dict`` element (e.g. ``None``/``"foo"``/``5``) is a malformed
        tool call → fail that call gracefully (never raise);
      * already-parsed ``dict``/``list`` arguments (native Anthropic
        ``tool_use.input``) are valid by construction;
      * a string is parsed with strict :func:`json.loads` (NaN/Infinity rejected,
        and ``RecursionError`` on pathological nesting is caught as a fail);
      * an absent (``None``) or empty/whitespace string is the no-argument
        sentinel — accepted *only* when no required keys are expected (otherwise
        it is missing those keys → fail).

    ``score`` is the share of tool calls that passed. Optionally, ``required_keys``
    maps a tool name to keys that must be present — a cheap shallow check, no
    external ``jsonschema`` dependency.
    """

    name = "tool_call_json_valid"

    def __init__(self, required_keys: Optional[Dict[str, Sequence[str]]] = None):
        self.required_keys = required_keys or {}

    def verify(self, response: ResponseView) -> VerifyResult:
        calls = response.tool_calls or []
        if not calls:
            return VerifyResult(self.name, True, 1.0, "no tool calls")

        ok = 0
        problems: List[str] = []
        for i, tc in enumerate(calls):
            if not isinstance(tc, dict):
                problems.append(f"#{i}: tool call is {type(tc).__name__}, not an object")
                continue

            name = _tool_name(tc) or f"#{i}"
            required = list(self.required_keys.get(_tool_name(tc), []))
            args = _tool_arguments(tc)

            parsed: Any
            if args is None:
                parsed = {}                      # no arguments provided
            elif isinstance(args, (dict, list)):
                parsed = args                    # already-parsed (Anthropic tool_use.input)
            elif isinstance(args, str):
                if not args.strip():
                    parsed = {}                  # empty no-arg sentinel
                else:
                    try:
                        parsed = _strict_json_loads(args)
                    except (ValueError, TypeError, RecursionError) as exc:
                        problems.append(f"{name}: arguments not valid JSON ({exc})")
                        continue
            else:
                problems.append(
                    f"{name}: arguments is {type(args).__name__}, not a JSON string/object")
                continue

            missing = [k for k in required if not (isinstance(parsed, dict) and k in parsed)]
            if missing:
                problems.append(f"{name}: missing required key(s) {missing}")
                continue
            ok += 1

        score = ok / len(calls)
        if problems:
            return VerifyResult(self.name, False, score, "; ".join(problems))
        return VerifyResult(self.name, True, 1.0, f"{ok}/{len(calls)} tool calls valid")


class CodeParses:
    """Fenced code blocks in a language we can parse must parse.

    We only hard-fail for languages we can *actually* validate cheaply:
    ``python``/``py`` via :func:`ast.parse`, ``json`` via strict
    :func:`json.loads`. For any other (or untagged, with no ``expected_language``)
    block we cannot prove the code is broken, so it passes — we do not run a
    brace-counter as a gate (it would false-positive on a ``}`` inside a string or
    comment). Pathological input that makes the parser raise ``RecursionError``
    is caught as a fail (unparseable = defect), never propagated. No fenced
    blocks → pass. ``score`` is the share of blocks that did not fail.
    """

    name = "code_parses"

    def verify(self, response: ResponseView) -> VerifyResult:
        blocks = iter_code_blocks(response.text or "")
        if not blocks:
            return VerifyResult(self.name, True, 1.0, "no fenced code blocks")

        ok = 0
        problems: List[str] = []
        for i, (lang, body) in enumerate(blocks):
            effective = lang or (response.expected_language or "").lower()
            label = f"block #{i}" + (f" ({effective})" if effective else "")
            if effective in _PARSE_LANGS_PY:
                try:
                    ast.parse(body)
                    ok += 1
                except (SyntaxError, ValueError, RecursionError) as exc:
                    detail = getattr(exc, "msg", None) or type(exc).__name__
                    problems.append(f"{label}: python does not parse ({detail})")
            elif effective in _PARSE_LANGS_JSON:
                try:
                    _strict_json_loads(body)
                    ok += 1
                except (ValueError, TypeError, RecursionError) as exc:
                    problems.append(f"{label}: json does not parse ({exc})")
            else:
                ok += 1  # language we cannot cheaply parse — cannot prove a defect

        score = ok / len(blocks)
        if problems:
            return VerifyResult(self.name, False, score, "; ".join(problems))
        return VerifyResult(self.name, True, 1.0, f"{ok}/{len(blocks)} code blocks parse")


class DiffWellFormed:
    r"""A unified diff in the text must be structurally well-formed.

    The text is treated as a diff **only** when it carries a real hunk header
    (``^@@ -\d+(,\d+)? \+\d+(,\d+)? @@``) or an explicit ```diff fence. Prose
    that merely starts with ``--- ``/``+++ `` (section dividers, changelog lines)
    is therefore *not* a diff → pass. When it is a diff: hunk headers must match
    that pattern and body lines must start with ``' '``/``'+'``/``'-'`` (``'\'``
    for the no-newline marker). This is a **structural** check only — it
    deliberately does NOT apply the diff to a working tree (that needs files and
    is not local to the response). No diff present → pass.
    """

    name = "diff_well_formed"

    def verify(self, response: ResponseView) -> VerifyResult:
        diff_text = self._extract_diff(response.text or "")
        if diff_text is None:
            return VerifyResult(self.name, True, 1.0, "no diff present")

        in_hunk = False
        for line in diff_text.splitlines():
            if line.startswith("@@"):
                if not _HUNK_RE.match(line):
                    return VerifyResult(
                        self.name, False, 0.0, f"malformed hunk header: {line!r}")
                in_hunk = True
                continue
            if not in_hunk:
                continue  # preamble / file headers before the first hunk
            if line == "" or line[0] in (" ", "+", "-", "\\"):
                continue  # context / added / removed / no-newline marker
            if line.startswith(_DIFF_HEADER_PREFIXES):
                in_hunk = False  # next file in a multi-file diff
                continue
            return VerifyResult(
                self.name, False, 0.0, f"malformed diff body line: {line!r}")

        return VerifyResult(self.name, True, 1.0, "well-formed unified diff")

    @staticmethod
    def _extract_diff(text: str) -> Optional[str]:
        # An explicit ```diff fence is an unambiguous claim "this is a diff".
        for lang, body in iter_code_blocks(text):
            if lang == "diff":
                return body
        # Otherwise, only a *real* hunk header makes this a diff worth checking;
        # bare ``--- ``/``+++ `` markers (prose) do not.
        if _HUNK_SEARCH_RE.search(text):
            return text
        return None


class FormatWellFormed:
    """When a whole-response format is expected, the body must be in it.

    For ``expected_format == "json"`` the whole ``text`` (or its genuinely-sole
    fenced block) must parse with strict :func:`json.loads` (NaN/Infinity
    rejected; ``RecursionError`` on pathological nesting caught as a fail). Other
    formats are accepted as not-checked for now — the structure generalizes so a
    new format only needs a branch here. No ``expected_format`` → pass.
    """

    name = "format_well_formed"

    def verify(self, response: ResponseView) -> VerifyResult:
        fmt = (response.expected_format or "").strip().lower()
        if not fmt:
            return VerifyResult(self.name, True, 1.0, "no expected_format")
        if fmt == "json":
            body = _sole_fenced_body(response.text or "")
            if body is None:
                body = response.text or ""
            try:
                _strict_json_loads(body)
            except (ValueError, TypeError, RecursionError) as exc:
                return VerifyResult(
                    self.name, False, 0.0, f"expected json, body did not parse ({exc})")
            return VerifyResult(self.name, True, 1.0, "body is valid json")
        return VerifyResult(self.name, True, 1.0, f"format {fmt!r} not structurally checked")


class RefusalMarker:
    """Heuristic confidence signal: lower the score on obvious refusal language.

    This is **not** a hard fail — refusing can be the correct answer. It always
    ``passed=True`` and only lowers ``score`` when the text opens with an obvious
    refusal/uncertainty marker, so a router can weigh it without blocking. Pure
    substring matching; documented as a heuristic.
    """

    name = "refusal_marker"

    #: score emitted when a refusal marker is present.
    weak_score = 0.2

    def verify(self, response: ResponseView) -> VerifyResult:
        low = (response.text or "").lower()
        for marker in _REFUSAL_MARKERS:
            if marker in low:
                return VerifyResult(
                    self.name, True, self.weak_score, f"refusal/uncertainty marker: {marker!r}")
        return VerifyResult(self.name, True, 1.0, "no refusal marker")


# --------------------------------------------------------------------------- #
# chain runner
# --------------------------------------------------------------------------- #
def default_verifiers() -> List[Verifier]:
    """The standard tier-2 structural chain, in run order."""
    return [
        NonEmptyContent(),
        NotTruncated(),
        ToolCallJSONValid(),
        CodeParses(),
        DiffWellFormed(),
        FormatWellFormed(),
        RefusalMarker(),
    ]


def run_verifiers(
    response: ResponseView,
    verifiers: Sequence[Verifier],
    *,
    mode: str = "all",
) -> List[VerifyResult]:
    """Run a chain of verifiers over ``response`` and collect their verdicts.

    ``mode``:
      * ``"all"`` (default) — run every verifier; return all results.
      * ``"first_fail"`` — short-circuit at the first hard fail; return the
        results gathered so far (including the failing one). Useful when a caller
        wants to fall back the moment any structural check trips.

    Pure: no I/O, no mutation of ``response`` or ``verifiers``. **Contract:** a
    verifier must never crash the chain — any exception a verifier raises is
    backstopped into a fail verdict (defense-in-depth on top of each check's own
    guards), so this always returns one :class:`VerifyResult` per verifier run.
    """
    if mode not in ("all", "first_fail"):
        raise ValueError(f"unknown mode: {mode!r}")
    results: List[VerifyResult] = []
    for v in verifiers:
        try:
            result = v.verify(response)
        except Exception as exc:  # noqa: BLE001 - contract: never let a verifier crash the chain
            name = getattr(v, "name", v.__class__.__name__)
            result = VerifyResult(name, False, 0.0, f"verifier error: {type(exc).__name__}")
        results.append(result)
        if mode == "first_fail" and not result.passed:
            break
    return results


def all_passed(results: Sequence[VerifyResult]) -> bool:
    """True if every result is a hard pass."""
    return all(r.passed for r in results)


def aggregate(results: Sequence[VerifyResult], *, name: str = "chain") -> VerifyResult:
    """Fold per-check results into one overall verdict.

    ``passed`` = every check passed. ``score`` = the minimum score across checks
    (the weakest signal dominates, so a passing-but-low :class:`RefusalMarker`
    still pulls the aggregate score down without flipping ``passed``). ``reason``
    names the first failing check, or "all checks passed".
    """
    if not results:
        return VerifyResult(name, True, 1.0, "no verifiers run")
    passed = all(r.passed for r in results)
    score = min(r.score for r in results)
    first_fail = next((r for r in results if not r.passed), None)
    reason = (
        f"{first_fail.verifier}: {first_fail.reason}" if first_fail else "all checks passed"
    )
    return VerifyResult(name, passed, score, reason)
