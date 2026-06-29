"""Cheap inline structural verifiers — the tier-2 safety net (T007).

These are the *cheap structural verify* checks from §7 of
``docs/QUALITY-GATED-ROUTER.md``: near-zero-cost, purely local checks that run
on an assembled model response and catch the structurally-broken outputs we saw
in our eval — empty/truncated content (thinking-budget starvation), tool-call
JSON that does not validate, code that does not parse, a diff that is not
well-formed, a malformed format. On a check failure the router (T008/T009) falls
back to the next tier; T007 builds ONLY the checks plus the chain runner — not
routing, tier selection, fallback, or the streaming commit window.

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
from dataclasses import dataclass, field
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
      * ``tool_calls`` — each a dict carrying a string ``arguments`` field as the
        wire carries it (OpenAI-style ``function.arguments`` is also accepted).
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
# A fenced block: ```lang\n ... \n``` . ``lang`` is optional; body is captured
# non-greedily up to the next closing fence. Backtick fences only (cheap).
_FENCE_RE = re.compile(
    r"```[ \t]*([A-Za-z0-9_.+-]*)[ \t]*\r?\n(.*?)```",
    re.DOTALL,
)

# A unified-diff hunk header, e.g. ``@@ -1,3 +2,4 @@`` (optionally trailing a
# section heading). ``re.match`` is unanchored at the end on purpose.
_HUNK_RE = re.compile(r"^@@ -\d+(,\d+)? \+\d+(,\d+)? @@")

# Lines that introduce a (possibly new) file in a diff and close any open hunk.
_DIFF_HEADER_PREFIXES = ("diff ", "--- ", "+++ ", "index ", "Index:")

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


def iter_code_blocks(text: str) -> List[Tuple[str, str]]:
    """Return ``(lang_lowercased, body)`` for each backtick-fenced block."""
    return [(m.group(1).lower(), m.group(2)) for m in _FENCE_RE.finditer(text or "")]


def _sole_fenced_body(text: str) -> Optional[str]:
    """If ``text`` (stripped) is exactly one fenced block, return its body.

    Used by format checks so an expectation like ``json`` is satisfied whether
    the caller wrapped the payload in a ```json fence or sent it bare.
    """
    blocks = iter_code_blocks(text)
    if len(blocks) != 1:
        return None
    stripped = (text or "").strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return blocks[0][1]
    return None


def _balanced_delimiters(text: str) -> bool:
    """Cheap structural check: are ``()[]{}`` balanced and properly nested?

    A heuristic, **not** a parser — it does not understand strings or comments,
    so a brace inside a string literal counts. Used only for languages we cannot
    cheaply parse, where the task calls for a structural sanity check rather than
    a real parse.
    """
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = set(pairs.values())
    stack: List[str] = []
    for ch in text:
        if ch in opens:
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return not stack


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
    """Every tool call's ``arguments`` must be parseable JSON.

    A truncated or malformed arguments blob is the most common tool-call failure.
    An absent (``None``) or empty/whitespace arguments value is treated as the
    no-argument sentinel and accepted. ``score`` is the share of tool calls that
    parsed.

    Optionally, ``required_keys`` maps a tool name to a list of keys that must be
    present in the parsed arguments object — a cheap shallow structural check, no
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
            args = _tool_arguments(tc)
            name = _tool_name(tc) or f"#{i}"
            if args is None or (isinstance(args, str) and not args.strip()):
                ok += 1  # no-argument call
                continue
            if not isinstance(args, str):
                problems.append(f"{name}: arguments is {type(args).__name__}, not a JSON string")
                continue
            try:
                parsed = json.loads(args)
            except (ValueError, TypeError) as exc:
                problems.append(f"{name}: arguments not valid JSON ({exc})")
                continue
            missing = [
                k for k in self.required_keys.get(_tool_name(tc), [])
                if not (isinstance(parsed, dict) and k in parsed)
            ]
            if missing:
                problems.append(f"{name}: missing required key(s) {missing}")
                continue
            ok += 1

        score = ok / len(calls)
        if problems:
            return VerifyResult(self.name, False, score, "; ".join(problems))
        return VerifyResult(self.name, True, 1.0, f"{ok}/{len(calls)} tool calls valid")


class CodeParses:
    """Fenced code blocks that *should* parse must parse.

    ``python``/``py`` blocks are checked with :func:`ast.parse`, ``json`` blocks
    with :func:`json.loads`. For a block whose language we cannot cheaply parse,
    a structural check (balanced ``()[]{}``) stands in for a real parser. An
    untagged block is typed by ``expected_language`` when set, else structurally
    checked. No fenced blocks → pass. ``score`` is the share of blocks that
    passed their check.
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
                except SyntaxError as exc:
                    problems.append(f"{label}: python does not parse ({exc.msg})")
            elif effective in _PARSE_LANGS_JSON:
                try:
                    json.loads(body)
                    ok += 1
                except ValueError as exc:
                    problems.append(f"{label}: json does not parse ({exc})")
            else:
                if _balanced_delimiters(body):
                    ok += 1
                else:
                    problems.append(f"{label}: unbalanced ()[]{{}} delimiters")

        score = ok / len(blocks)
        if problems:
            return VerifyResult(self.name, False, score, "; ".join(problems))
        return VerifyResult(self.name, True, 1.0, f"{ok}/{len(blocks)} code blocks parse")


class DiffWellFormed:
    """A unified diff in the text must be structurally well-formed.

    Triggered by a ```diff fence or by ``--- ``/``+++ ``/``@@ `` markers. Hunk
    headers must match ``^@@ -\\d+(,\\d+)? \\+\\d+(,\\d+)? @@``; body lines must
    start with ``' '``/``'+'``/``'-'`` (``'\\'`` for the no-newline marker). This
    is a **structural** well-formedness check only — it deliberately does NOT try
    to apply the diff to a working tree (that needs files and is not local to the
    response). No diff present → pass.
    """

    name = "diff_well_formed"

    def verify(self, response: ResponseView) -> VerifyResult:
        diff_text = self._extract_diff(response.text or "")
        if diff_text is None:
            return VerifyResult(self.name, True, 1.0, "no diff present")

        in_hunk = False
        saw_hunk = False
        for line in diff_text.splitlines():
            if line.startswith("@@"):
                if not _HUNK_RE.match(line):
                    return VerifyResult(
                        self.name, False, 0.0, f"malformed hunk header: {line!r}")
                in_hunk = True
                saw_hunk = True
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

        if not saw_hunk:
            return VerifyResult(
                self.name, False, 0.0, "diff markers present but no valid hunk header")
        return VerifyResult(self.name, True, 1.0, "well-formed unified diff")

    @staticmethod
    def _extract_diff(text: str) -> Optional[str]:
        for lang, body in iter_code_blocks(text):
            if lang == "diff":
                return body
        if re.search(r"^(--- |\+\+\+ |@@ )", text, re.MULTILINE):
            return text
        return None


class FormatWellFormed:
    """When a whole-response format is expected, the body must be in it.

    For ``expected_format == "json"`` the whole ``text`` (or its single fenced
    block) must :func:`json.loads`. Other formats are accepted as not-checked for
    now — the structure generalizes so a new format only needs a branch here.
    No ``expected_format`` → pass (not applicable).
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
                json.loads(body)
            except (ValueError, TypeError) as exc:
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

    Pure: no I/O, no mutation of ``response`` or ``verifiers``.
    """
    if mode not in ("all", "first_fail"):
        raise ValueError(f"unknown mode: {mode!r}")
    results: List[VerifyResult] = []
    for v in verifiers:
        result = v.verify(response)
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
