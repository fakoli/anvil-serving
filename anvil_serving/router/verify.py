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
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from typing import Protocol, runtime_checkable


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
      * ``caller_max_tokens`` — the CALLER's explicit token cap for this request
        (``InternalRequest.max_tokens``, parsed from the wire's ``max_tokens`` /
        ``max_completion_tokens``), or ``None`` when the caller set no cap at
        all. This is the live-incident fix (v0.7.1): a harness that computes its
        own completion budget (e.g. ``contextWindow - prompt_tokens``, clamped to
        a floor of 1) and sends ``max_tokens=1`` gets EXACTLY what it asked for
        when the model stops at that cap — that is compliance, not truncation.
        :class:`NotTruncated` reads this field to tell "the model obeyed an
        explicit caller cap" apart from "the model hit an unrequested/default
        budget and got cut off mid-answer". ``None`` here means "no explicit
        caller cap was set", so a length-like stop still reads as genuine
        truncation (the tier/default budget was hit, not something the caller
        asked for).
    """

    text: str = ""
    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    expected_format: Optional[str] = None
    expected_language: Optional[str] = None
    caller_max_tokens: Optional[int] = None


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
# Upper bound on the text a scanning check will parse. Beyond it we *skip*
# structural scanning and pass (we cannot cheaply prove a defect on a multi-
# hundred-KB blob, and refusing to scan keeps the inline hot path bounded — a
# defense-in-depth cap on top of the linear-time fence regex below). Measured in
# characters (== bytes for the typical ASCII payloads we see). The cheap O(1)/
# O(n) checks (NonEmptyContent, NotTruncated, RefusalMarker, ToolCallJSONValid)
# still run, so a truncation is still caught via ``finish_reason``.
MAX_SCAN_BYTES = 256 * 1024

# A JSON document can be small enough for our byte cap while still carrying
# pathological nesting deep enough to exhaust downstream parsers or validators.
# Keep this far above normal tool-argument shapes, but below adversarial stacks.
MAX_JSON_NESTING = 2048

# A backtick-fenced block, handling both forms and adversarial bodies:
#   * multi-line: ```lang\n <body> \n``` — the closing fence must follow a
#     newline (i.e. start its own line), so an inline ``` inside a string in the
#     body does NOT prematurely close the block.
#   * single-line: ```lang <body>``` on one line (no newline after the lang tag).
# Group 1 = lang tag; group 2 = multi-line body; group 3 = single-line body.
#
# The lang tag is bounded to a realistic length ({0,40}); an *unbounded* ``*``
# here is a catastrophic-backtracking (ReDoS) vector: on input like ```` ``` ````
# + a few KB of ``[A-Za-z0-9_.+-]`` with no closing fence, the greedy class
# backtracks char-by-char while the lazy body re-scans to EOF — O(n²), seconds
# to parse a 60 KB string. Real language tags are short, so the bound is
# behavior-neutral for legitimate input while making the scan linear.
_FENCE_RE = re.compile(
    r"```[ \t]*([A-Za-z0-9_.+-]{0,40})[ \t]*"
    r"(?:"
    r"\r?\n(.*?)\r?\n[ \t]*```"   # multi-line body; close ``` at (indented) line start
    r"|"
    r"[ \t]*(.*?)```"             # single-line body; close ``` on the same line
    r")",
    re.DOTALL,
)

# A fence *delimiter* at the start of a line (after optional indent). Used to
# detect an unterminated/truncated fence: a line-start ``` that is not contained
# in any *complete* block matched by ``_FENCE_RE`` is a dangling opener.
_FENCE_DELIM_RE = re.compile(r"(?m)^[ \t]*(```)")

# A unified-diff hunk header, e.g. ``@@ -1,3 +2,4 @@`` (optionally trailing a
# section heading). ``re.match`` is unanchored at the end on purpose. The count
# groups (2 = old line count, 4 = new line count) bound each hunk's body region;
# an omitted count defaults to 1 per the unified-diff convention.
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
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


def _reject_nonfinite_float(token: str) -> float:
    """``parse_float`` hook: reject numbers that overflow to ``inf`` (or ``nan``).

    ``parse_constant`` only fires for the literal tokens ``NaN``/``Infinity``;
    a numeric literal such as ``1e999`` parses through the default ``float``
    path to ``inf`` *without* touching ``parse_constant``. A structural validity
    gate that claims to reject non-finite values must catch that too, so we parse
    floats here and reject any non-finite result.
    """
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"non-finite JSON number: {token}")
    return value


def _json_nesting_too_deep(s: str, limit: int = MAX_JSON_NESTING) -> bool:
    """Return True when bracket/brace nesting exceeds ``limit``.

    This is a cheap pre-parse guard for adversarial JSON strings. It respects
    quoted strings and escapes so brackets inside string values do not count.
    """
    depth = 0
    in_string = False
    escaped = False
    for ch in s:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > limit:
                return True
        elif ch in "]}":
            depth = max(0, depth - 1)
    return False


def _strict_json_loads(s: str) -> Any:
    """``json.loads`` that rejects non-finite values (spec-strict).

    Rejects both the literal NaN/Infinity tokens (``parse_constant``) and numeric
    literals that overflow to ``inf`` such as ``1e999`` (``parse_float``).
    """
    if _json_nesting_too_deep(s):
        raise ValueError(f"JSON nesting exceeds {MAX_JSON_NESTING}")
    return json.loads(
        s,
        parse_constant=_reject_nonspec_constant,
        parse_float=_reject_nonfinite_float,
    )


def _nonstr_text_failure(verifier_name: str, value: Any) -> Optional[VerifyResult]:
    """Guard a check against a non-``str`` ``text`` field (contract: never raise).

    ``ResponseView.text`` is typed ``str``, but an adversarial/buggy caller can
    pass ``bytes`` (or any other type), which would make the ``re``/``str``
    machinery raise ``TypeError`` *inside* a check — violating the per-check
    "adversarial input yields a fail verdict, never an exception" contract. A
    ``None`` text is the documented empty default and is allowed; any other
    non-``str`` type yields a fail verdict. Returns ``None`` when ``value`` is a
    valid (``str``/``None``) text, else the fail :class:`VerifyResult`.
    """
    if value is None or isinstance(value, str):
        return None
    return VerifyResult(verifier_name, False, 0.0, f"non-string text ({type(value).__name__})")


def iter_code_blocks(text: str) -> List[Tuple[str, str]]:
    """Return ``(lang_lowercased, body)`` for each *complete* backtick-fenced block."""
    out: List[Tuple[str, str]] = []
    for m in _FENCE_RE.finditer(text or ""):
        lang = (m.group(1) or "").lower()
        body = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        out.append((lang, body))
    return out


def _has_unterminated_fence(text: str) -> bool:
    """True if a line-start ``` opener is not contained in any *complete* block.

    A code block that was opened with ``` but never closed (the classic
    truncated/length-starved response that is cut off mid-code) leaves a dangling
    opener that :func:`iter_code_blocks` cannot match — so the block silently
    disappears and a truncation slips past. We flag it: every line-start fence
    delimiter must fall inside a span matched by :data:`_FENCE_RE`; one that does
    not is a dangling opener. Line-start only (heuristic) so an inline ``` in
    prose (e.g. "use ``` to format") is not a false positive.
    """
    # Two-pointer merge over two position-sorted streams (complete-block spans
    # and line-start delimiters): O(spans + delimiters). The previous
    # any()-containment scan was O(spans * delimiters) — quadratic on
    # adversarial input like a response of thousands of bare ``` lines, which
    # is exactly the kind of blob this hot-path check gets fed.
    spans = [(m.start(), m.end()) for m in _FENCE_RE.finditer(text or "")]
    i = 0
    for dm in _FENCE_DELIM_RE.finditer(text or ""):
        pos = dm.start(1)  # the ``` itself, after any indent
        # Advance past spans that end at or before this delimiter; both the
        # delimiter stream and the span list are sorted by position, so a span
        # skipped here can never contain a LATER delimiter either.
        while i < len(spans) and spans[i][1] <= pos:
            i += 1
        if i >= len(spans) or not (spans[i][0] <= pos < spans[i][1]):
            return True
    return False


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
        bad = _nonstr_text_failure(self.name, response.text)
        if bad is not None:
            return bad
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

    "length"/"max_tokens"/"model_length" → truncated (fail), UNLESS the CALLER
    explicitly set a token cap (:attr:`ResponseView.caller_max_tokens` is not
    ``None``) — in that case a length-like stop is exactly what was asked for
    (compliance, not truncation) and this check PASSES (v0.7.1, the live
    contextWindow-clamp incident: a harness that computes
    ``max_completion_tokens = contextWindow - prompt_tokens``, floored at 1,
    can legitimately send ``max_tokens=1``; the model honoring that cap must
    not be treated as a structural defect, or every such probe 503s and —
    worse — trips the circuit breaker on tiers that are actually healthy).

    A clean stop or an unknown (``None``) reason always passes — we only flag a
    *known* truncation. When the caller set NO cap at all
    (``caller_max_tokens is None``) a length-like stop is still a genuine
    unexpected truncation (the tier's own default budget was hit, not
    something the caller asked for) and this still fails. This check does NOT
    look at ``response.text`` — an empty caller-capped ``length`` response
    still passes NotTruncated; :class:`NonEmptyContent` is what catches
    thinking-budget starvation (empty content regardless of any cap).
    """

    name = "not_truncated"

    def verify(self, response: ResponseView) -> VerifyResult:
        fr = response.finish_reason
        # Guard a non-str finish_reason (contract: never raise). Anything that is
        # not a known truncation token reads as a clean stop.
        reason = (fr if isinstance(fr, str) else "").strip().lower()
        if reason in _TRUNCATION_REASONS:
            if response.caller_max_tokens is not None:
                return VerifyResult(
                    self.name, True, 1.0,
                    f"caller-capped stop (finish_reason={response.finish_reason!r}, "
                    f"caller_max_tokens={response.caller_max_tokens!r}): compliance, "
                    f"not truncation",
                )
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

    @classmethod
    def from_request_raw(cls, raw: Any) -> "ToolCallJSONValid":
        """Build shallow required-key checks from either supported tool dialect.

        OpenAI declares the schema at ``function.parameters``; Anthropic uses
        ``input_schema``. Only the JSON Schema ``required`` array is consumed —
        the verifier stays stdlib-only and deliberately does not pretend to be
        a complete JSON Schema implementation.
        """
        body = raw if isinstance(raw, dict) else {}
        tools = body.get("tools")
        required_keys: Dict[str, Sequence[str]] = {}
        if not isinstance(tools, (list, tuple)):
            return cls()
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            fn = tool.get("function")
            if isinstance(fn, dict):
                name = fn.get("name")
                schema = fn.get("parameters")
            else:
                name = tool.get("name")
                schema = tool.get("input_schema")
            if not isinstance(name, str) or not name.strip():
                continue
            required = schema.get("required") if isinstance(schema, dict) else None
            if isinstance(required, (list, tuple)):
                keys = [key for key in required if isinstance(key, str) and key]
                if keys:
                    required_keys[name.strip()] = keys
        return cls(required_keys=required_keys)

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

            raw_name = _tool_name(tc)
            name = raw_name or f"#{i}"
            required = list(self.required_keys.get(raw_name, []))
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


class ToolCallContractValid:
    """Tool calls must honor the caller's advertised catalog and choice.

    JSON-valid arguments are not enough: a model can emit a syntactically valid
    call to a tool the harness never advertised (for example ``open_file`` or
    the bare wrapper name ``functions``).  Forwarding that call merely pushes a
    predictable ``tool not found`` error into the agent loop.  This verifier
    proves the response obeys the request-side contract before any bytes are
    committed, allowing the router to try the next quality-gated tier instead.

    ``from_request_raw`` understands both supported wire dialects:

    * OpenAI tools: ``{type:"function", function:{name:...}}`` and choices
      ``auto`` / ``required`` / ``none`` / a forced function object.
    * Anthropic tools: ``{name:...}`` and choices ``auto`` / ``any`` / ``none`` /
      ``tool``.

    An absent tool catalog is treated as "not checkable" for compatibility
    with trusted/in-process backends that surface structured calls without a
    complete raw wire body.  An explicitly present catalog (including an empty
    one) is authoritative. Malformed request entries are ignored defensively;
    the dialect/front door remains responsible for request validation.
    """

    name = "tool_call_contract_valid"

    def __init__(
        self,
        allowed_names: Optional[Sequence[str]],
        *,
        choice_mode: str = "auto",
        required_name: Optional[str] = None,
    ) -> None:
        self.allowed_names = (
            frozenset(str(name) for name in allowed_names if str(name))
            if allowed_names is not None
            else None
        )
        self.choice_mode = choice_mode
        self.required_name = required_name

    @classmethod
    def from_request_raw(cls, raw: Any) -> "ToolCallContractValid":
        body = raw if isinstance(raw, dict) else {}
        raw_tools = body.get("tools")
        catalog_present = isinstance(raw_tools, (list, tuple))
        names: List[str] = []
        if catalog_present:
            for tool in raw_tools:
                if not isinstance(tool, dict):
                    continue
                # OpenAI function tool.
                fn = tool.get("function")
                if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                    name = fn["name"].strip()
                    if name:
                        names.append(name)
                    continue
                # Anthropic custom tool.
                if isinstance(tool.get("name"), str):
                    name = tool["name"].strip()
                    if name:
                        names.append(name)

        choice = body.get("tool_choice")
        mode = "auto"
        required_name: Optional[str] = None
        if isinstance(choice, str) and choice in ("auto", "required", "none"):
            mode = choice
        elif isinstance(choice, dict):
            ctype = choice.get("type")
            if ctype == "any":
                mode = "required"
            elif ctype in ("auto", "none"):
                mode = str(ctype)
            elif ctype in ("function", "tool"):
                fn = choice.get("function")
                candidate = (
                    fn.get("name") if isinstance(fn, dict) else choice.get("name")
                )
                if isinstance(candidate, str) and candidate.strip():
                    mode = "specific"
                    required_name = candidate.strip()

        return cls(
            names if catalog_present else None,
            choice_mode=mode,
            required_name=required_name,
        )

    def verify(self, response: ResponseView) -> VerifyResult:
        calls = response.tool_calls or []
        problems: List[str] = []

        if self.choice_mode in ("required", "specific") and not calls:
            label = (
                f"tool {self.required_name!r}" if self.required_name else "a tool call"
            )
            problems.append(f"caller required {label}, but response made no tool call")
        if self.choice_mode == "none" and calls:
            problems.append("caller forbade tool calls, but response emitted one")

        for i, tool_call in enumerate(calls):
            if not isinstance(tool_call, dict):
                problems.append(
                    f"#{i}: tool call is {type(tool_call).__name__}, not an object"
                )
                continue
            name = _tool_name(tool_call)
            if not name:
                problems.append(f"#{i}: tool call has no name")
                continue
            if self.allowed_names is not None and name not in self.allowed_names:
                problems.append(f"{name!r}: tool name was not advertised by caller")
            if self.required_name is not None and name != self.required_name:
                problems.append(
                    f"{name!r}: caller specifically required {self.required_name!r}"
                )

        if problems:
            return VerifyResult(self.name, False, 0.0, "; ".join(problems))
        if calls:
            return VerifyResult(
                self.name, True, 1.0, f"{len(calls)} tool call(s) honor caller contract"
            )
        return VerifyResult(self.name, True, 1.0, "no tool call required or emitted")


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
        bad = _nonstr_text_failure(self.name, response.text)
        if bad is not None:
            return bad
        text = response.text or ""
        if len(text) > MAX_SCAN_BYTES:
            return VerifyResult(
                self.name, True, 1.0,
                f"input too large to scan ({len(text)}B > {MAX_SCAN_BYTES}B); skipped")
        # A fence opened but never closed = a truncated/malformed code block. The
        # block itself vanishes from iter_code_blocks (no close to match), so we
        # must flag the dangling opener here or the truncation slips the chain.
        if _has_unterminated_fence(text):
            return VerifyResult(
                self.name, False, 0.0, "unterminated code fence (truncated code block?)")
        blocks = iter_code_blocks(text)
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
    is therefore *not* a diff → pass — those markers alone are too ambiguous to
    treat as an explicit claim without false-positiving on prose.

    When it is a diff, validation is scoped to the **hunk region**: each hunk
    header ``@@ -a,b +c,d @@`` declares ``b`` old-side and ``d`` new-side lines,
    so we know where the hunk body ends. Body lines must start with
    ``' '``/``'+'``/``'-'`` (``'\'`` for the no-newline marker); a non-prefixed
    line *inside* an open hunk is malformed (fail). Once a hunk has consumed its
    declared lines it is complete, and trailing prose after the last hunk is
    tolerated (just as preamble before the first hunk already is) — the common
    "<diff>\n\nThis explains the change." shape must not false-fail.

    An explicit ```diff fence that contains *no* valid hunk header is a diff
    claim we can disprove → fail. This is a **structural** check only — it
    deliberately does NOT apply the diff to a working tree (that needs files and
    is not local to the response). No diff present → pass.
    """

    name = "diff_well_formed"

    def verify(self, response: ResponseView) -> VerifyResult:
        bad = _nonstr_text_failure(self.name, response.text)
        if bad is not None:
            return bad
        text = response.text or ""
        if len(text) > MAX_SCAN_BYTES:
            return VerifyResult(
                self.name, True, 1.0,
                f"input too large to scan ({len(text)}B > {MAX_SCAN_BYTES}B); skipped")
        diff_text, explicit = self._extract_diff(text)
        if diff_text is None:
            return VerifyResult(self.name, True, 1.0, "no diff present")

        in_hunk = False           # currently consuming a hunk's declared body
        old_rem = new_rem = 0      # remaining old-/new-side lines in the open hunk
        hunks = 0
        for line in diff_text.splitlines():
            if line.startswith("@@"):
                m = _HUNK_RE.match(line)
                if not m:
                    return VerifyResult(
                        self.name, False, 0.0, f"malformed hunk header: {line!r}")
                hunks += 1
                old_rem = int(m.group(2)) if m.group(2) is not None else 1
                new_rem = int(m.group(4)) if m.group(4) is not None else 1
                in_hunk = old_rem > 0 or new_rem > 0
                continue
            if not in_hunk:
                # preamble/file headers before the first hunk, or trailing prose
                # (and inter-hunk file headers) after a hunk has completed.
                continue
            first = line[:1]
            if line == "" or first == " ":
                old_rem -= 1
                new_rem -= 1
            elif first == "+":
                new_rem -= 1
            elif first == "-":
                old_rem -= 1
            elif first == "\\":
                pass  # "\ No newline at end of file" — counts toward neither side
            elif line.startswith(_DIFF_HEADER_PREFIXES):
                in_hunk = False  # next file in a multi-file diff closes this hunk
                continue
            else:
                return VerifyResult(
                    self.name, False, 0.0, f"malformed diff body line: {line!r}")
            if old_rem <= 0 and new_rem <= 0:
                in_hunk = False  # hunk consumed its declared lines; prose may follow

        if explicit and hunks == 0:
            return VerifyResult(
                self.name, False, 0.0, "diff claimed (```diff fence) but no valid hunk header")
        return VerifyResult(self.name, True, 1.0, "well-formed unified diff")

    @staticmethod
    def _extract_diff(text: str) -> Tuple[Optional[str], bool]:
        """Return ``(diff_region, is_explicit_claim)``.

        A ```diff fence is the only *explicit* "this is a diff" claim we trust
        (bare ``--- ``/``+++ `` markers are indistinguishable from prose section
        dividers, so treating them as a claim would false-fail prose). Otherwise
        a real hunk header anywhere makes the whole text a diff worth checking.
        ``(None, False)`` means "not a diff" → the caller passes.
        """
        # An explicit ```diff fence is an unambiguous claim "this is a diff".
        for lang, body in iter_code_blocks(text):
            if lang == "diff":
                return body, True
        # Otherwise, only a *real* hunk header makes this a diff worth checking;
        # bare ``--- ``/``+++ `` markers (prose) do not.
        if _HUNK_SEARCH_RE.search(text):
            return text, False
        return None, False


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
        bad = _nonstr_text_failure(self.name, response.text)
        if bad is not None:
            return bad
        text = response.text or ""
        if len(text) > MAX_SCAN_BYTES:
            return VerifyResult(
                self.name, True, 1.0,
                f"input too large to scan ({len(text)}B > {MAX_SCAN_BYTES}B); skipped")
        fmt = (response.expected_format or "").strip().lower()
        if not fmt:
            return VerifyResult(self.name, True, 1.0, "no expected_format")
        if fmt == "json":
            body = _sole_fenced_body(text)
            if body is None:
                body = text
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
    ``passed=True`` and only lowers ``score`` when the text *opens* with an
    obvious refusal/uncertainty marker, so a router can weigh it without blocking.
    Matched as a prefix of the text's opening (after leading whitespace), NOT
    anywhere in the body: a refusal phrase merely referenced mid-explanation
    ("earlier I said I can't help with X, but here's how…") is a normal answer
    and must not be penalized. Documented as a heuristic.
    """

    name = "refusal_marker"

    #: score emitted when a refusal marker is present.
    weak_score = 0.2

    def verify(self, response: ResponseView) -> VerifyResult:
        bad = _nonstr_text_failure(self.name, response.text)
        if bad is not None:
            return bad
        # Match only at the OPENING (per the docstring). An anywhere-substring
        # match spuriously penalizes a legitimate answer that just references a
        # refusal phrase later in the body.
        opening = (response.text or "").lstrip().lower()
        for marker in _REFUSAL_MARKERS:
            if opening.startswith(marker):
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
