#!/usr/bin/env python
"""Capacity and repeated-quality benchmark runners for local model endpoints.

Use ``anvil-serving eval benchmark capacity`` for performance evidence and
``anvil-serving eval benchmark quality`` for protocol-v3 correctness evidence.
The runtime remains stdlib-only (urllib, threads, and atomic JSON writes).
"""
import argparse
import hashlib
import json
import math
import os
import re
import time
import random
import statistics
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from .model_controls import validate_reasoning_control
except ImportError:  # direct ``python anvil_serving/benchmark.py`` compatibility
    from model_controls import validate_reasoning_control

FILLER = "def helper_%d():\n    return compute(%d)  # routine specialist context\n"


def _atomic_write_json(path, value):
    """Atomically replace a JSON artifact without leaving a truncated target."""
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("output directory does not exist: %s" % parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=parent,
                prefix=".%s." % os.path.basename(out), suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, out)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _validate_write_target(path, *, label="output"):
    """Fail before live work when a requested artifact cannot be replaced safely."""
    if not path or path == "-":
        return None
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("%s directory does not exist: %s" % (label, parent))
    if os.path.islink(out):
        raise OSError("%s path cannot be a symbolic link: %s" % (label, out))
    if os.path.exists(out) and not os.path.isfile(out):
        raise OSError("%s path is not a regular file: %s" % (label, out))
    if not os.access(parent, os.W_OK):
        raise OSError("%s directory is not writable: %s" % (label, parent))
    return out


def _console_safe(value):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(value).encode(encoding, errors="backslashreplace").decode(encoding)

# Conservative chars-per-token: real code tokenizes to ~3-4 chars/token, so dividing
# char length by this OVER-estimates the token count -> a clamp built on it never lets
# the prompt sneak past the serve's max_model_len.
CHARS_PER_TOKEN = 3.0
# Token headroom left below max_model_len when clamping (absorbs heuristic error +
# template/role overhead so prompt + generation stay inside the context window).
DEFAULT_CTX_MARGIN = 1024

def est_tokens(text):
    """Conservative (slightly high) token estimate for a string, stdlib-only."""
    return int(len(text) / CHARS_PER_TOKEN)

def ctx_cap(max_model_len, max_tokens, margin=DEFAULT_CTX_MARGIN):
    """Largest prompt-token budget that keeps prompt + generation inside the serve's
    context window. Returns None when no limit is known -> no clamp (legacy behavior)."""
    if not max_model_len:
        return None
    return max(256, int(max_model_len) - int(max_tokens) - int(margin))

def clamp_ctx(ctx, cap):
    """Clamp a sampled/fixed ctx-token target down to the serve's usable budget."""
    return ctx if cap is None else min(ctx, cap)

def make_prompt(shared_prefix, ctx_tokens, uniq, max_prompt_tokens=None,
                chars_per_token=CHARS_PER_TOKEN):
    """Build a prompt sized to ~ctx_tokens tokens, never past max_prompt_tokens.

    Sized by ONE char budget from the per-target token count — the old word-count
    heuristic under-counted (the filler tokenizes at ~2.7 tok/word, the heuristic
    assumed 1.33) and the old truncation cut at the WINDOW cap, so every sub-window
    target ballooned to the window (both bakeoff context probes sent identical
    ~262k prompts; see docs/findings/2026-07-10-...-evidence/failures.md §7).
    The default chars_per_token is conservative (real tokens land UNDER budget);
    pass a value calibrated from usage.prompt_tokens to land ON the target.
    """
    # shared_prefix is identical across requests (prefix-cache hit); filler varies
    budget = ctx_tokens if max_prompt_tokens is None else min(ctx_tokens, max_prompt_tokens)
    tail = f"\n# request {uniq}: summarize the above in one line."
    char_budget = int(budget * chars_per_token) - len(shared_prefix) - len(tail) - 1
    if char_budget <= 0:
        # shared prefix ALONE exceeds the budget (tiny window / big --shared-prefix-tokens):
        # front-truncate it — keeps its head for prefix-cache hits, never overflows.
        # max(0, ...) because a NEGATIVE slice index would keep almost the whole prefix.
        return shared_prefix[: max(0, int(budget * chars_per_token) - len(tail))] + tail
    lines = []
    filled = 0
    i = 0
    while filled < char_budget:
        s = FILLER % (uniq + i, i)
        lines.append(s); filled += len(s); i += 1
    filler = "".join(lines)[:max(0, char_budget)]
    return shared_prefix + "\n" + filler + tail

def build_body(model, prompt, max_tokens, chat_template_kwargs=None, reasoning_effort=None):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
            "stream_options": {"include_usage": True}}
    # chat_template_kwargs (e.g. {"enable_thinking": False}) is honored by SGLang/vLLM
    # for Qwen3.x / GLM so thinking-by-default models don't burn the whole token budget
    # on hidden reasoning and emit ZERO content deltas (which the report reads as a FALSE
    # 0 tok/s with TTFT==E2E).
    if chat_template_kwargs:
        body["chat_template_kwargs"] = chat_template_kwargs
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    return body

def parse_csv(values, default=None):
    """Parse repeatable/comma-separated CLI values into a flat list of strings."""
    if not values:
        return list(default or [])
    out = []
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if item:
                out.append(item)
    return out

def parse_context_targets(value):
    """Parse `--context-targets 32768,65536` into positive integer targets."""
    if not value:
        return [32768]
    targets = []
    for item in str(value).split(","):
        item = item.strip()
        if not item:
            continue
        target = int(item)
        if not 0 < target <= _MAX_CONTEXT_TARGET_TOKENS:
            raise ValueError(
                "context targets must be positive integers no greater than %d"
                % _MAX_CONTEXT_TARGET_TOKENS
            )
        targets.append(target)
        if len(targets) > _MAX_CONTEXT_TARGETS:
            raise ValueError(
                "context targets cannot contain more than %d values"
                % _MAX_CONTEXT_TARGETS
            )
    targets = targets or [32768]
    if sum(targets) > _MAX_TOTAL_CONTEXT_TARGET_TOKENS:
        raise ValueError(
            "context targets cannot request more than %d aggregate prompt tokens"
            % _MAX_TOTAL_CONTEXT_TARGET_TOKENS
        )
    return targets

BAKEOFF_TOOL = {
    "type": "function",
    "function": {
        "name": "record_weather_zip",
        "description": "Record the ZIP code the user supplied for weather lookup.",
        "parameters": {
            "type": "object",
            "properties": {"zip": {"type": "string"}},
            "required": ["zip"],
        },
    },
}

INTELLIGENCE_PROMPTS = [
    {
        "id": "unified_diff_timeout_edit",
        "prompt": (
            "You are editing app.py. Original file:\n"
            "timeout = 30\n"
            "retries = 2\n\n"
            "Return only a unified diff that changes timeout to 45 and leaves "
            "retries unchanged."
        ),
        "checks": [
            {"name": "diff_shape", "contains_all": ["---", "+++", "@@"]},
            {"name": "removes_old_timeout", "contains": "-timeout = 30"},
            {"name": "adds_new_timeout", "contains": "+timeout = 45"},
        ],
    },
    {
        "id": "parallel_timeout_triage",
        "prompt": (
            "A voice agent calls STT, an LLM, and TTS. The total turn timeout is "
            "2500 ms. Logs show STT=550 ms, LLM=1800 ms, TTS=650 ms. In one "
            "concise sentence, identify the problem and one practical fix."
        ),
        "checks": [
            {
                "name": "identifies_budget_overrun",
                "contains_any": ["timeout", "budget", "overrun", "too slow", "exceeds"],
            },
            {
                "name": "offers_latency_fix",
                "contains_any": ["faster", "reduce", "parallel", "cache", "shorter", "limit"],
            },
        ],
    },
]

SESSION_RECALL_PROMPT = [
    {"role": "user", "content": "Remember this session code: RIVER-918. Reply with ok."},
    {"role": "assistant", "content": "ok"},
    {"role": "user", "content": "What session code should be used? Reply with only the code."},
]

def _choice_messages(response):
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list):
        return []
    messages = []
    for choice in choices:
        if isinstance(choice, dict) and isinstance(choice.get("message"), dict):
            messages.append(choice["message"])
    return messages

def _message_text(message):
    content = message.get("content") if isinstance(message, dict) else ""
    return content if isinstance(content, str) else ""

def response_observation(response):
    """Extract visible and reasoning-channel evidence from one chat response.

    OpenAI-compatible engines currently expose parsed reasoning as either
    ``message.reasoning`` or ``message.reasoning_content``. Keep the field name
    so an absent channel cannot be confused with an empty channel, and retain
    the full visible answer because deterministic checks operate on that text.
    """
    choices = response.get("choices") if isinstance(response, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = _message_text(message)
    reasoning_field = None
    reasoning = ""
    for field in ("reasoning", "reasoning_content"):
        value = message.get(field)
        if isinstance(value, str):
            if reasoning_field is None:
                reasoning_field = field
            if value:
                reasoning_field = field
                reasoning = value
                break
    usage = response.get("usage") if isinstance(response, dict) else None
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
    return {
        "content": content,
        "content_excerpt": content[:200],
        "finish_reason": choice.get("finish_reason"),
        "reasoning_field": reasoning_field,
        "reasoning_chars": len(reasoning),
        "reasoning_excerpt": reasoning[:200] if reasoning else "",
        "reasoning_tokens": reasoning_tokens,
        "usage": usage,
    }

def validate_function_tool_call(message, expected_name, required_args):
    """Return a schema/usefulness result for one OpenAI-compatible tool call."""
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not tool_calls:
        return {
            "valid": False,
            "error": "response did not include tool_calls",
            "arguments": None,
        }

    first = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
    function = first.get("function") if isinstance(first, dict) else {}
    if not isinstance(function, dict):
        return {"valid": False, "error": "tool_call missing function object", "arguments": None}
    if function.get("name") != expected_name:
        return {
            "valid": False,
            "error": "wrong function name: %r" % function.get("name"),
            "arguments": None,
        }

    raw_args = function.get("arguments")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except Exception as exc:
        return {"valid": False, "error": "arguments are not valid JSON: %s" % exc, "arguments": None}
    if not isinstance(args, dict):
        return {"valid": False, "error": "arguments are not a JSON object", "arguments": None}

    for key, expected_value in required_args.items():
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            return {
                "valid": False,
                "error": "missing required string argument: %s" % key,
                "arguments": args,
            }
        if expected_value is not None and value.strip() != expected_value:
            return {
                "valid": False,
                "error": "wrong argument %s: %r" % (key, value),
                "arguments": args,
            }

    return {"valid": True, "error": None, "arguments": args}

_CHECK_KEYS = ("contains", "contains_all", "contains_any", "matches_regex")
_MAX_CHECK_REGEX_CHARS = 512
_MAX_EVAL_REPETITIONS = 20
_MAX_QUALITY_COMPLETION_TOKENS = 65536
_MAX_SUITE_EVALS = 100
_MAX_TOTAL_EVAL_ATTEMPTS = 500
_MAX_TOTAL_QUALITY_TOKENS = 2000000
_MIN_COMPARABLE_REPETITIONS = 3
_MAX_SUITE_FILE_BYTES = 4 * 1024 * 1024
_MAX_CONTROL_EVIDENCE_BYTES = 1024 * 1024
_MAX_CONTEXT_TARGETS = 16
_MAX_CONTEXT_TARGET_TOKENS = 1048576
_MAX_TOTAL_CONTEXT_TARGET_TOKENS = 4194304
_MAX_TOTAL_CAPACITY_PROMPT_TOKENS = 67108864

def _compile_safe_check_regex(pattern):
    """Compile the bounded, linear-time-ish regex subset used by eval checks.

    Python's stdlib regex engine has no search timeout. Externally-authored
    suites therefore cannot use grouping, alternation, wildcard repetition,
    or general quantifiers: all can create catastrophic backtracking over a
    retained model answer. The supported subset is intentionally enough for
    deterministic markers (anchors, boundaries, character classes, literals,
    and ``\\s*`` between fields). Anything more expressive should be a purpose-
    built validator rather than executable regex from a suite file.
    """
    if len(pattern) > _MAX_CHECK_REGEX_CHARS:
        raise ValueError("matches_regex exceeds %d characters" % _MAX_CHECK_REGEX_CHARS)
    i = 0
    in_character_class = False
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if char == "[":
            if in_character_class:
                raise ValueError("matches_regex has a nested character class")
            in_character_class = True
            i += 1
            continue
        if char == "]":
            if not in_character_class:
                raise ValueError("matches_regex has an unmatched character-class close")
            in_character_class = False
            i += 1
            continue
        if in_character_class:
            i += 1
            continue
        if char in "()|{}+?" or char == ".":
            raise ValueError("matches_regex uses an unsafe regex construct")
        if char == "*" and pattern[max(0, i - 2):i] != r"\s":
            # The sole repeated class supports optional Markdown emphasis on a
            # final answer marker. General repeated classes are unnecessary for
            # deterministic grading and can compose into expensive backtracking.
            if pattern[max(0, i - 3):i] != "[*]":
                raise ValueError(
                    "matches_regex only permits repetition as \\s* or [*]*"
                )
        i += 1
    if in_character_class:
        raise ValueError("matches_regex has an unterminated character class")
    if r"\s*\s*" in pattern:
        raise ValueError("matches_regex cannot repeat adjacent whitespace wildcards")
    try:
        return re.compile(pattern, flags=re.IGNORECASE)
    except re.error as exc:
        raise ValueError("invalid matches_regex: %s" % exc) from exc

def _validate_spec_check(where, check):
    """Reject any check shape evaluate_text_checks would silently default-pass.

    evaluate_text_checks sets ok=True and only flips it when a KNOWN assertion key
    is present — safe for the trusted in-repo INTELLIGENCE_PROMPTS, but through
    --suite-file a typo'd key ('contain_all'), a name-only check, an empty needle,
    or an empty list would make the check ALWAYS pass on any output, including the
    empty-content thinking-starvation shape (gotcha #9). Reject all of those at
    load time so a vacuous check can never become green evidence.
    """
    if not isinstance(check, dict) or not isinstance(check.get("name"), str) \
            or not check["name"].strip():
        raise ValueError("%s: each check needs a string 'name'" % where)
    keys = [k for k in _CHECK_KEYS if k in check]
    if len(keys) != 1:
        raise ValueError(
            "%s: check %r needs exactly one of "
            "contains/contains_all/contains_any/matches_regex"
            % (where, check["name"])
        )
    value = check[keys[0]]
    if keys[0] in {"contains", "matches_regex"}:
        if not isinstance(value, str) or not value:
            raise ValueError(
                "%s: check %r: %s must be a non-empty string"
                % (where, check["name"], keys[0])
            )
        if keys[0] == "matches_regex":
            try:
                _compile_safe_check_regex(value)
            except ValueError as exc:
                raise ValueError(
                    "%s: check %r: %s"
                    % (where, check["name"], exc)
                ) from exc
    elif (not isinstance(value, list) or not value
          or not all(isinstance(item, str) and item for item in value)):
        raise ValueError(
            "%s: check %r: %s must be a non-empty list of non-empty strings"
            % (where, check["name"], keys[0])
        )

def load_suite_spec(path):
    """Load + validate an externally-authored eval suite (--suite-file).

    Spec shape (deliberately compatible with the session-evals plugin's suite.json;
    its eval_emit.py validates the same constraints on emit):
    {suite, date?, work_class?, evals: [{id, prompt|messages, max_tokens?,
    visible_answer_tokens?, reasoning_headroom_tokens?, tools?,
    expect_tool?: {name, required_args}, checks?: [{name, contains|contains_all|
    contains_any|matches_regex}]}]} — checks use evaluate_text_checks semantics, expect_tool uses
    validate_function_tool_call. A malformed spec is an operator error (loud, before
    any request is sent), never benchmark evidence — so every shape the runtime
    would trip over (or worse, silently default-pass) is rejected here.
    """
    with open(os.path.expanduser(path), "rb") as f:
        source_bytes = f.read(_MAX_SUITE_FILE_BYTES + 1)
    if len(source_bytes) > _MAX_SUITE_FILE_BYTES:
        raise ValueError("suite file exceeds %d bytes" % _MAX_SUITE_FILE_BYTES)
    spec = json.loads(source_bytes.decode("utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("suite file must be a JSON object")
    suite = spec.get("suite")
    if not isinstance(suite, str) or not suite.strip():
        raise ValueError("suite file needs a non-empty 'suite' name")
    evals = spec.get("evals")
    if not isinstance(evals, list) or not evals:
        raise ValueError("suite file needs a non-empty 'evals' list")
    if len(evals) > _MAX_SUITE_EVALS:
        raise ValueError("suite file cannot contain more than %d evals" % _MAX_SUITE_EVALS)
    evidence_use = spec.get("evidence_use", "diagnostic")
    if evidence_use not in {"diagnostic", "ranking"}:
        raise ValueError("suite file evidence_use must be 'diagnostic' or 'ranking'")
    validator_strength = spec.get("validator_strength", "deterministic_marker")
    if validator_strength not in {
            "deterministic_marker", "exact_choice", "typed_structure", "independent_judge"}:
        raise ValueError(
            "suite file validator_strength must be deterministic_marker, exact_choice, "
            "typed_structure, or independent_judge"
        )
    if evidence_use == "ranking" and validator_strength == "deterministic_marker":
        raise ValueError(
            "ranking suites need validator_strength exact_choice or typed_structure; "
            "substring/regex markers are diagnostic only"
        )
    if validator_strength == "independent_judge":
        raise ValueError(
            "validator_strength independent_judge is not executable yet; use an "
            "exact_choice or typed_structure validator, or keep the suite diagnostic-only"
        )
    seen_ids = set()
    for i, item in enumerate(evals):
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError("evals[%d] must be an object with an 'id'" % i)
        where = "evals[%d] (%s)" % (i, item["id"])
        if item["id"] in seen_ids:
            raise ValueError("%s: duplicate eval id" % where)
        seen_ids.add(item["id"])
        if item.get("context_bucket") is not None:
            raise ValueError(
                "%s: context_bucket cannot be executed faithfully yet; provide the "
                "actual bounded context in messages or remove context_bucket and keep "
                "this suite diagnostic-only" % where
            )
        messages = item.get("messages")
        if messages is not None and (
                not isinstance(messages, list) or not messages
                or not all(isinstance(m, dict) for m in messages)):
            raise ValueError("%s: messages must be a non-empty list of objects" % where)
        if not messages and not (isinstance(item.get("prompt"), str) and item["prompt"]):
            raise ValueError("%s: needs a non-empty 'prompt' or 'messages'" % where)
        max_tokens = item.get("max_tokens")
        if max_tokens is not None and (
                isinstance(max_tokens, bool) or not isinstance(max_tokens, int)
                or not 0 < max_tokens <= _MAX_QUALITY_COMPLETION_TOKENS):
            raise ValueError(
                "%s: max_tokens must be an integer from 1 through %d"
                % (where, _MAX_QUALITY_COMPLETION_TOKENS)
            )
        visible_tokens = item.get("visible_answer_tokens")
        reasoning_tokens = item.get("reasoning_headroom_tokens")
        if max_tokens is not None and (visible_tokens is not None or reasoning_tokens is not None):
            raise ValueError(
                "%s: max_tokens cannot be combined with visible_answer_tokens or "
                "reasoning_headroom_tokens" % where
            )
        if visible_tokens is not None and (
                isinstance(visible_tokens, bool) or not isinstance(visible_tokens, int)
                or not 0 < visible_tokens <= _MAX_QUALITY_COMPLETION_TOKENS):
            raise ValueError(
                "%s: visible_answer_tokens must be an integer from 1 through %d"
                % (where, _MAX_QUALITY_COMPLETION_TOKENS)
            )
        if reasoning_tokens is not None and (
                isinstance(reasoning_tokens, bool) or not isinstance(reasoning_tokens, int)
                or not 0 <= reasoning_tokens <= _MAX_QUALITY_COMPLETION_TOKENS):
            raise ValueError(
                "%s: reasoning_headroom_tokens must be an integer from 0 through %d"
                % (where, _MAX_QUALITY_COMPLETION_TOKENS)
            )
        if (visible_tokens is not None or reasoning_tokens is not None) and (
                (visible_tokens or 256) + (reasoning_tokens or 0)
                > _MAX_QUALITY_COMPLETION_TOKENS):
            raise ValueError(
                "%s: visible-answer plus reasoning-headroom allocation exceeds %d"
                % (where, _MAX_QUALITY_COMPLETION_TOKENS)
            )
        if item.get("tools") is not None and not isinstance(item["tools"], list):
            raise ValueError("%s: tools must be a list" % where)
        checks = item.get("checks")
        if checks is not None and not isinstance(checks, list):
            raise ValueError("%s: checks must be a list" % where)
        for check in checks or []:
            _validate_spec_check(where, check)
        expect = item.get("expect_tool")
        if expect is not None:
            if not isinstance(expect, dict) or not isinstance(expect.get("name"), str) \
                    or not expect["name"]:
                raise ValueError("%s: expect_tool needs a string 'name'" % where)
            required_args = expect.get("required_args")
            if required_args is not None and not isinstance(required_args, dict):
                raise ValueError("%s: expect_tool.required_args must be an object" % where)
            for key, want in (required_args or {}).items():
                # null = "present, non-empty string" (plugin contract); anything
                # else must be the exact expected string — a JSON number here
                # could never match and would masquerade as a model failure.
                if want is not None and not isinstance(want, str):
                    raise ValueError(
                        "%s: expect_tool.required_args[%r] must be a string or null"
                        % (where, key)
                    )
        # an eval that asserts nothing proves nothing — reject it up front
        if not checks and not expect:
            raise ValueError("%s: needs 'checks' or 'expect_tool'" % where)
        if validator_strength == "exact_choice":
            if expect is not None or len(checks or []) != 1:
                raise ValueError(
                    "%s: exact_choice requires exactly one full-response matches_regex "
                    "check and no expect_tool" % where
                )
            pattern = checks[0].get("matches_regex")
            if not isinstance(pattern, str) or not (
                    pattern.startswith("^") and pattern.endswith("$")):
                raise ValueError(
                    "%s: exact_choice requires a matches_regex anchored with ^ and $"
                    % where
                )
            compiled = _compile_safe_check_regex(pattern)
            if compiled.search("") is not None or compiled.search(" \t\n") is not None:
                raise ValueError(
                    "%s: exact_choice validator must not match empty or whitespace-only output"
                    % where
                )
        elif validator_strength == "typed_structure":
            if expect is None:
                raise ValueError(
                    "%s: typed_structure requires expect_tool so the response is checked "
                    "against a declared function-call shape" % where
                )
            matching_tools = [
                tool.get("function")
                for tool in item.get("tools") or []
                if isinstance(tool, dict)
                and isinstance(tool.get("function"), dict)
                and tool["function"].get("name") == expect["name"]
            ]
            if len(matching_tools) != 1:
                raise ValueError(
                    "%s: typed_structure expect_tool must match exactly one declared tool"
                    % where
                )
            required_args = expect.get("required_args") or {}
            if not required_args:
                raise ValueError(
                    "%s: typed_structure requires at least one exact required argument"
                    % where
                )
            parameters = matching_tools[0].get("parameters") or {}
            properties = parameters.get("properties") or {}
            required_names = parameters.get("required") or []
            if not isinstance(properties, dict) or not isinstance(required_names, list):
                raise ValueError("%s: typed_structure tool schema is malformed" % where)
            missing = sorted(
                key for key in required_args
                if key not in properties or key not in required_names
            )
            if missing:
                raise ValueError(
                    "%s: typed_structure required_args are not required by the tool schema: %s"
                    % (where, ", ".join(missing))
                )
    spec["_source_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    return spec


def load_control_evidence(path, *, status, mechanism):
    """Load and bind a bounded, structured local control proof."""
    resolved = os.path.abspath(os.path.expanduser(path))
    try:
        with open(resolved, "rb") as handle:
            raw = handle.read(_MAX_CONTROL_EVIDENCE_BYTES + 1)
    except OSError as exc:
        raise ValueError("cannot read control evidence %s: %s" % (resolved, exc)) from exc
    if len(raw) > _MAX_CONTROL_EVIDENCE_BYTES:
        raise ValueError(
            "control evidence exceeds %d bytes" % _MAX_CONTROL_EVIDENCE_BYTES
        )
    try:
        evidence = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("control evidence must be a UTF-8 JSON object: %s" % exc) from exc
    if not isinstance(evidence, dict):
        raise ValueError("control evidence must be a JSON object")
    required = {
        "schema": "anvil-serving.control-evidence/v1",
        "status": status,
        "control_mechanism": mechanism,
    }
    for key, expected in required.items():
        if evidence.get(key) != expected:
            raise ValueError("control evidence %s must equal %r" % (key, expected))
    for key in ("source", "observed_at"):
        if not isinstance(evidence.get(key), str) or not evidence[key].strip():
            raise ValueError("control evidence requires a non-empty %s" % key)
    return resolved, hashlib.sha256(raw).hexdigest()

def evaluate_text_checks(content, checks):
    """Deterministic text checks; this never asks the candidate to grade itself."""
    normalized = content.lower()
    results = []
    for check in checks:
        ok = True
        if "contains" in check:
            ok = check["contains"].lower() in normalized
        elif "contains_all" in check:
            ok = all(item.lower() in normalized for item in check["contains_all"])
        elif "contains_any" in check:
            ok = any(item.lower() in normalized for item in check["contains_any"])
        elif "matches_regex" in check:
            ok = _compile_safe_check_regex(check["matches_regex"]).search(content) is not None
        results.append({"name": check["name"], "passed": ok})
    return results

def resolve_thinking_settings(args):
    """Resolve CLI thinking flags into request kwargs plus evidence metadata."""
    mode = getattr(args, "thinking_mode", None) or "default"
    if getattr(args, "no_thinking", False):
        mode = "disabled"

    if mode == "enabled":
        kwargs = {"enable_thinking": True}
    elif mode == "disabled":
        kwargs = {"enable_thinking": False}
    else:
        kwargs = None

    reasoning_effort = getattr(args, "reasoning_effort", None)
    if reasoning_effort is not None:
        kwargs = None
        mechanism = "reasoning_effort"
        requested = reasoning_effort
    elif kwargs is not None:
        mechanism = "chat_template_kwargs"
        requested = kwargs
    elif mode == "unsupported":
        mechanism = "unsupported"
        requested = None
    else:
        mechanism = "none"
        requested = None

    control_status = getattr(args, "control_status", None)
    if requested is None:
        control_status = mechanism
    elif control_status is None:
        control_status = "requested_unverified"
    return kwargs, reasoning_effort, {
        "mode": mode,
        "chat_template_kwargs": kwargs,
        "reasoning_effort": reasoning_effort,
        "control_mechanism": mechanism,
        "control_requested": requested,
        "control_status": control_status,
        "control_evidence": getattr(args, "control_evidence", None),
        "control_evidence_sha256": getattr(args, "control_evidence_sha256", None),
        "unsupported": mode == "unsupported",
    }


def _request_control_kwargs(chat_template_kwargs, reasoning_effort):
    """Build call kwargs without passing a new optional key to legacy fakes."""
    kwargs = {"chat_template_kwargs": chat_template_kwargs}
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    return kwargs

def eval_budget(item, args, *, default_visible=256):
    """Resolve a quality-eval completion allocation.

    OpenAI-compatible APIs expose one total completion cap. The repaired
    protocol therefore records a visible-answer target and explicit reasoning
    headroom separately, then sends their sum. This is an allocation contract,
    not a claim that the engine hard-partitions the two channels.
    """
    cli_visible = getattr(args, "visible_answer_tokens", None)
    cli_headroom = getattr(args, "reasoning_headroom_tokens", None)
    visible = cli_visible
    if visible is None:
        visible = item.get("visible_answer_tokens")
    legacy_budget = visible is None and item.get("max_tokens") is not None
    if legacy_budget:
        visible = item["max_tokens"]
    if visible is None:
        visible = default_visible
    headroom = cli_headroom
    if headroom is None:
        headroom = item.get("reasoning_headroom_tokens", 0)
    resolved = {
        "visible_answer_tokens": int(visible),
        "reasoning_headroom_tokens": int(headroom),
        "max_completion_tokens": int(visible) + int(headroom),
        "legacy_max_tokens_as_visible": bool(legacy_budget),
    }
    if not 0 < resolved["visible_answer_tokens"] <= _MAX_QUALITY_COMPLETION_TOKENS:
        raise ValueError("resolved visible-answer allocation is outside the safe range")
    if not 0 <= resolved["reasoning_headroom_tokens"] <= _MAX_QUALITY_COMPLETION_TOKENS:
        raise ValueError("resolved reasoning-headroom allocation is outside the safe range")
    if resolved["max_completion_tokens"] > _MAX_QUALITY_COMPLETION_TOKENS:
        raise ValueError(
            "resolved visible-answer plus reasoning-headroom allocation exceeds %d"
            % _MAX_QUALITY_COMPLETION_TOKENS
        )
    return resolved

def validate_eval_work_plan(args, suite_spec):
    """Reject a quality plan whose aggregate requests or retained output can explode."""
    selected = parse_csv(args.suite, default=[] if suite_spec else ["chat"])
    context_targets = parse_context_targets(args.context_targets)
    for flag, value, maximum in (
            ("--shared-prefix-tokens", args.shared_prefix_tokens, 262144),
            ("--max-model-len", args.max_model_len, 1048576),
            ("--margin", args.margin, 1048576)):
        if not 0 <= value <= maximum:
            raise ValueError("%s must be from 0 through %d" % (flag, maximum))
    if ("chat" in selected or "context" in selected) and (
            sum(context_targets) > _MAX_TOTAL_CONTEXT_TARGET_TOKENS):
        raise ValueError(
            "quality context plan exceeds %d aggregate prompt tokens"
            % _MAX_TOTAL_CONTEXT_TARGET_TOKENS
        )
    budgets = []
    if "intelligence" in selected:
        budgets.extend(eval_budget({}, args) for _ in INTELLIGENCE_PROMPTS)
    if "tool" in selected:
        budgets.append(eval_budget({}, args))
    if "session" in selected:
        budgets.append(eval_budget({}, args))
    if suite_spec:
        budgets.extend(eval_budget(item, args) for item in suite_spec["evals"])
    attempt_count = len(budgets) * args.eval_repetitions
    if attempt_count > _MAX_TOTAL_EVAL_ATTEMPTS:
        raise ValueError(
            "quality plan exceeds %d total attempts" % _MAX_TOTAL_EVAL_ATTEMPTS
        )
    requested_tokens = sum(item["max_completion_tokens"] for item in budgets)
    requested_tokens *= args.eval_repetitions
    if requested_tokens > _MAX_TOTAL_QUALITY_TOKENS:
        raise ValueError(
            "quality plan exceeds %d requested completion tokens"
            % _MAX_TOTAL_QUALITY_TOKENS
        )

def _failure_class(observation, *, checks_passed):
    has_visible_content = bool(observation["content"].strip())
    if (not has_visible_content and observation["finish_reason"] == "length"
            and (observation["reasoning_chars"] or observation["reasoning_tokens"])):
        return "reasoning_budget_exhausted"
    if (has_visible_content and observation["finish_reason"] == "length"
            and (observation["reasoning_chars"] or observation["reasoning_tokens"])):
        return "completion_budget_exhausted_after_visible_output"
    if has_visible_content and observation["finish_reason"] == "length":
        return "visible_answer_budget_exhausted"
    if observation["finish_reason"] not in {"stop", "tool_calls"}:
        return "unexpected_finish_reason"
    if checks_passed:
        return None
    if not has_visible_content:
        return "visible_answer_missing"
    return "deterministic_check_failed"

def _attempt_passed(observation, checks_passed, *, allowed_finish_reasons=("stop",)):
    """A deterministic match is not a pass when generation did not finish cleanly."""
    return bool(
        checks_passed and observation.get("finish_reason") in set(allowed_finish_reasons)
    )

def _aggregate_attempts(check, attempts, min_pass_rate):
    passed = sum(1 for attempt in attempts if attempt.get("status") == "passed")
    pass_rate = passed / len(attempts) if attempts else 0.0
    check.update({
        "attempts": attempts,
        "pass_count": passed,
        "attempt_count": len(attempts),
        "pass_rate": pass_rate,
        "required_pass_rate": min_pass_rate,
        "status": "passed" if pass_rate >= min_pass_rate else "failed",
    })
    if check["status"] != "passed" and not check.get("error"):
        errors = [
            str(attempt["error"])
            for attempt in attempts
            if attempt.get("error")
        ]
        check["error"] = errors[0] if errors else "pass rate below threshold"
    if len(attempts) == 1:
        # Preserve the v1 convenience fields while the richer attempt record is
        # adopted by notebook/report consumers.
        for key in (
            "latency_ms", "text_checks", "content", "content_excerpt",
            "finish_reason", "reasoning_field", "reasoning_chars",
            "reasoning_excerpt", "reasoning_tokens", "usage", "budget",
            "failure_class", "tool_call", "error",
            "tool_call_count", "valid_tool_call_count", "arguments",
            "validation_errors", "expected",
        ):
            if key in attempts[0]:
                check[key] = attempts[0][key]
    return check

def post_chat(base, model, key, messages, max_tokens=128, timeout=120,
              tools=None, chat_template_kwargs=None, reasoning_effort=None):
    """Non-streaming OpenAI-compatible chat call for smoke/tool probes."""
    url = base.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if chat_template_kwargs:
        body["chat_template_kwargs"] = chat_template_kwargs
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return {"latency_s": time.time() - t0, "response": data}

def detect_max_model_len(base, model=None, key=None, timeout=15):
    """Best-effort probe of <base>/models for the serve's context window. SGLang & vLLM
    expose it on the model card. Returns None on any problem so callers fall back to
    current (unclamped) behavior."""
    url = base.rstrip("/") + "/models"
    headers = {}
    if key: headers["Authorization"] = "Bearer " + key
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    entries = data.get("data") if isinstance(data, dict) else None
    if not entries:
        return None
    chosen = None
    for e in entries:
        if not isinstance(e, dict):
            continue
        if model and e.get("id") == model:
            chosen = e; break
        if chosen is None:
            chosen = e
    if not isinstance(chosen, dict):
        return None
    for k in ("max_model_len", "max_context_length", "context_length"):
        v = chosen.get(k)
        if isinstance(v, int) and v > 0:
            return v
    return None

def resolve_api_key(api_key_env=None):
    """Resolve auth for probes from an environment variable reference."""
    if api_key_env:
        value = os.environ.get(api_key_env)
        if not value:
            raise ValueError("environment variable %s is not set" % api_key_env)
        return value
    return None

def stream_chat(base, model, prompt, key, max_tokens, timeout=900,
                chat_template_kwargs=None, reasoning_effort=None):
    url = base.rstrip("/") + "/chat/completions"
    body = build_body(
        model, prompt, max_tokens, chat_template_kwargs, reasoning_effort
    )
    headers = {"Content-Type": "application/json"}
    if key: headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time(); ttft = None; out_toks = 0; usage = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"): continue
            data = line[5:].strip()
            if data == "[DONE]": break
            try: chunk = json.loads(data)
            except Exception: continue
            if chunk.get("usage"): usage = chunk["usage"]
            for ch in chunk.get("choices", []):
                delta = ch.get("delta", {})
                if delta.get("content"):
                    if ttft is None: ttft = time.time() - t0
                    out_toks += 1
    return dict(ttft=ttft if ttft is not None else (time.time()-t0),
                e2e=time.time()-t0, out_toks=out_toks, usage=usage)

def cached_fraction(usage):
    if not usage: return None
    det = usage.get("prompt_tokens_details") or {}
    cached = det.get("cached_tokens")
    pt = usage.get("prompt_tokens")
    if cached is None or not pt: return None
    return cached / pt

def pctile(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return 0
    i = min(len(xs)-1, int(round((len(xs)-1)*q/100)))
    return xs[i]

def _result_metrics(results):
    ttfts = [r.get("ttft") for r in results if isinstance(r, dict)]
    e2es = [r.get("e2e") for r in results if isinstance(r, dict)]
    out_tot = sum(r.get("out_toks") or 0 for r in results if isinstance(r, dict))
    return {
        "ttft_p50_ms": pctile(ttfts, 50) * 1000.0,
        "ttft_p95_ms": pctile(ttfts, 95) * 1000.0,
        "e2e_p50_ms": pctile(e2es, 50) * 1000.0,
        "e2e_p95_ms": pctile(e2es, 95) * 1000.0,
        "output_tokens": out_tot,
    }

# measured subagent ctx percentiles (from role_split): rough inverse-CDF sampler
SUBAGENT_CTX = [(0.0,16000),(0.218,32768),(0.602,65536),(0.906,131072),(0.995,262144)]
def sample_ctx():
    r = random.random()
    for p, v in SUBAGENT_CTX:
        if r <= p: return v
    return 262144

def run_bakeoff(a, api_key):
    """Run selected bakeoff suites against one already-loaded endpoint.

    This mode intentionally never starts, stops, unloads, or reloads models. It
    only sends OpenAI-compatible requests to the supplied base URL and records
    both successful sub-checks and failures in one JSON artifact.
    """
    started_at = time.time()
    suite_spec = getattr(a, "suite_spec", None)
    # --suite-file alone runs ONLY the external suite: the default chat/context
    # probe must be opted into (--suite chat) so an unrelated probe failure can
    # never pollute external-suite evidence (or trip the notebook no_failures gate).
    suites = parse_csv(a.suite, default=[] if suite_spec else ["chat"])
    known_suites = {"chat", "context", "tool", "session", "intelligence", "voice"}
    unknown_suites = sorted(set(suites) - known_suites)
    if unknown_suites:
        raise ValueError("unknown quality suite(s): %s" % ", ".join(unknown_suites))
    context_targets = parse_context_targets(a.context_targets)
    max_model_len = a.max_model_len or detect_max_model_len(a.base_url, a.model, api_key)
    cap = ctx_cap(max_model_len, a.max_tokens, a.margin)
    ctk, reasoning_effort, thinking_section = resolve_thinking_settings(a)
    control_kwargs = _request_control_kwargs(ctk, reasoning_effort)
    failures = []
    chat_results = []
    context_results = []

    should_run_context = "chat" in suites or "context" in suites
    if should_run_context:
        shared = (FILLER % (0, 0)) * max(1, int(a.shared_prefix_tokens * 0.75) // 6)
        chars_per_token = CHARS_PER_TOKEN
        for target in context_targets:
            clamped = clamp_ctx(target, cap)
            prompt = make_prompt(
                shared, clamped, target, max_prompt_tokens=cap,
                chars_per_token=chars_per_token,
            )
            row = {
                "target_tokens": target,
                "clamped_tokens": clamped,
                "attempted_context_tokens": clamped,
                # estimate with the SAME rate the prompt was sized with — this field is
                # the operator's diagnostic when a row fails (no usage comes back), so
                # it must not drift from the fixed constant once calibration advances.
                "estimated_prompt_tokens": int(len(prompt) / chars_per_token),
                "chars_per_token": round(chars_per_token, 3),
                "status": "pending",
            }
            try:
                result = stream_chat(
                    a.base_url, a.model, prompt, api_key, a.max_tokens,
                    timeout=a.timeout, **control_kwargs,
                )
                row.update({
                    "status": "passed",
                    "ttft_ms": result["ttft"] * 1000.0,
                    "e2e_ms": result["e2e"] * 1000.0,
                    "output_tokens": result["out_toks"],
                    "usage": result.get("usage"),
                })
                chat_results.append(result)
                # Calibrate sizing from the serve's REAL tokenizer count so later
                # targets land ON target instead of ~15% under the conservative default.
                usage = result.get("usage") or {}
                if usage.get("prompt_tokens"):
                    measured = len(prompt) / usage["prompt_tokens"]
                    if 1.0 <= measured <= 10.0:  # ignore bogus usage
                        chars_per_token = measured
            except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                row.update({"status": "failed", "error": str(exc)})
                failures.append({
                    "suite": "context" if "context" in suites else "chat",
                    "target_tokens": target,
                    "error": str(exc),
                })
            context_results.append(row)

    tool_section = {"status": "not_run", "checks": []}
    if "tool" in suites:
        check = {
            "name": "openai_tool_call_smoke",
            "status": "pending",
            "expected_function": "record_weather_zip",
            "expected_arguments": {"zip": "98101"},
        }
        budget = eval_budget({}, a)
        attempts = []
        for attempt_index in range(a.eval_repetitions):
            try:
                result = post_chat(
                    a.base_url,
                    a.model,
                    api_key,
                    [{"role": "user", "content": "Call record_weather_zip with zip 98101."}],
                    max_tokens=budget["max_completion_tokens"],
                    timeout=a.timeout,
                    tools=[BAKEOFF_TOOL],
                    **control_kwargs,
                )
                response = result.get("response", {})
                messages = _choice_messages(response)
                observation = response_observation(response)
                validations = [
                    validate_function_tool_call(
                        message, "record_weather_zip", {"zip": "98101"}
                    )
                    for message in messages
                ]
                valid = [item for item in validations if item["valid"]]
                passed = _attempt_passed(
                    observation, bool(valid), allowed_finish_reasons=("tool_calls", "stop")
                )
                attempt = {
                    "attempt": attempt_index + 1,
                    "status": "passed" if passed else "failed",
                    "latency_ms": result["latency_s"] * 1000.0,
                    "tool_call_count": sum(
                        len(message.get("tool_calls") or []) for message in messages
                    ),
                    "valid_tool_call_count": len(valid),
                    "arguments": valid[0]["arguments"] if valid else None,
                    "validation_errors": [
                        item["error"] for item in validations if item["error"]
                    ],
                    "budget": budget,
                    **observation,
                }
                attempt["failure_class"] = _failure_class(
                    observation, checks_passed=bool(valid)
                )
                if not valid:
                    attempt["error"] = (
                        attempt["validation_errors"][0]
                        if attempt["validation_errors"]
                        else "response did not include valid tool_calls"
                    )
                elif not passed:
                    attempt["error"] = "tool response did not finish cleanly"
                attempts.append(attempt)
            except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                attempts.append({
                    "attempt": attempt_index + 1,
                    "status": "failed",
                    "error": str(exc),
                    "failure_class": "request_error",
                    "budget": budget,
                })
        _aggregate_attempts(check, attempts, a.eval_min_pass_rate)
        if check["status"] != "passed":
            check["error"] = check.get("error") or "pass rate below threshold"
            failures.append({
                "suite": "tool",
                "error": check["error"],
                "failure_classes": sorted({
                    item.get("failure_class") for item in attempts
                    if item.get("failure_class")
                }),
                "pass_rate": check["pass_rate"],
            })
        tool_section = {"status": check["status"], "checks": [check]}

    session_section = {"status": "not_run", "checks": []}
    if "session" in suites:
        check = {"name": "single_request_multiturn_recall", "status": "pending"}
        budget = eval_budget({}, a)
        attempts = []
        for attempt_index in range(a.eval_repetitions):
            try:
                result = post_chat(
                    a.base_url,
                    a.model,
                    api_key,
                    SESSION_RECALL_PROMPT,
                    max_tokens=budget["max_completion_tokens"],
                    timeout=a.timeout,
                    **control_kwargs,
                )
                response = result.get("response", {})
                observation = response_observation(response)
                marker_passed = "RIVER-918" in observation["content"].replace(" ", "")
                passed = _attempt_passed(observation, marker_passed)
                attempt = {
                    "attempt": attempt_index + 1,
                    "status": "passed" if passed else "failed",
                    "latency_ms": result["latency_s"] * 1000.0,
                    "expected": "RIVER-918",
                    "budget": budget,
                    **observation,
                }
                attempt["failure_class"] = _failure_class(
                    observation, checks_passed=marker_passed
                )
                if not marker_passed:
                    attempt["error"] = "response did not recall session code"
                elif not passed:
                    attempt["error"] = "session response did not finish cleanly"
                attempts.append(attempt)
            except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                attempts.append({
                    "attempt": attempt_index + 1,
                    "status": "failed",
                    "error": str(exc),
                    "failure_class": "request_error",
                    "budget": budget,
                })
        _aggregate_attempts(check, attempts, a.eval_min_pass_rate)
        if check["status"] != "passed":
            check["error"] = check.get("error") or "pass rate below threshold"
            failures.append({
                "suite": "session",
                "error": check["error"],
                "failure_classes": sorted({
                    item.get("failure_class") for item in attempts
                    if item.get("failure_class")
                }),
                "pass_rate": check["pass_rate"],
            })
        session_section = {"status": check["status"], "checks": [check]}

    intelligence_section = {"status": "not_run", "checks": []}
    if "intelligence" in suites:
        checks = []
        for spec in INTELLIGENCE_PROMPTS:
            check = {
                "id": spec["id"],
                "status": "pending",
                "validator": "deterministic_text_checks",
            }
            budget = eval_budget({}, a)
            attempts = []
            for attempt_index in range(a.eval_repetitions):
                try:
                    result = post_chat(
                        a.base_url,
                        a.model,
                        api_key,
                        [{"role": "user", "content": spec["prompt"]}],
                        max_tokens=budget["max_completion_tokens"],
                        timeout=a.timeout,
                        **control_kwargs,
                    )
                    observation = response_observation(result.get("response", {}))
                    text_checks = evaluate_text_checks(observation["content"], spec["checks"])
                    checks_passed = all(item["passed"] for item in text_checks)
                    passed = _attempt_passed(observation, checks_passed)
                    attempts.append({
                        "attempt": attempt_index + 1,
                        "status": "passed" if passed else "failed",
                        "latency_ms": result["latency_s"] * 1000.0,
                        "text_checks": text_checks,
                        "budget": budget,
                        "failure_class": _failure_class(
                            observation, checks_passed=checks_passed
                        ),
                        **observation,
                    })
                except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                    attempts.append({
                        "attempt": attempt_index + 1,
                        "status": "failed",
                        "error": str(exc),
                        "failure_class": "request_error",
                        "budget": budget,
                    })
            _aggregate_attempts(check, attempts, a.eval_min_pass_rate)
            if check["status"] != "passed":
                failure_classes = sorted({
                    attempt.get("failure_class") for attempt in attempts
                    if attempt.get("failure_class")
                })
                if not check.get("error"):
                    check["error"] = "pass rate below threshold"
                failures.append({
                    "suite": "intelligence",
                    "prompt_id": spec["id"],
                    "error": check["error"],
                    "failure_classes": failure_classes,
                    "pass_rate": check["pass_rate"],
                })
            checks.append(check)
        intelligence_section = {
            "status": "passed" if checks and all(c["status"] == "passed" for c in checks) else "failed",
            "checks": checks,
        }

    # --suite-file: externally-authored evals through the SAME check engine as the
    # built-in intelligence/tool suites (spec validated up front in main()).
    external_suites = {}
    spec = suite_spec
    if spec:
        checks = []
        for item in spec["evals"]:
            validators = []
            if item.get("checks"):
                validators.append("deterministic_text_checks")
            if item.get("expect_tool"):
                validators.append("tool_call")
            check = {
                "id": item["id"],
                "status": "pending",
                "validator": "+".join(validators),
            }
            request_messages = item.get("messages") or [
                {"role": "user", "content": item["prompt"]}
            ]
            budget = eval_budget(item, a)
            attempts = []
            for attempt_index in range(a.eval_repetitions):
                try:
                    result = post_chat(
                        a.base_url,
                        a.model,
                        api_key,
                        request_messages,
                        max_tokens=budget["max_completion_tokens"],
                        timeout=a.timeout,
                        tools=item.get("tools"),
                        **control_kwargs,
                    )
                    response = result.get("response", {})
                    messages = _choice_messages(response)
                    observation = response_observation(response)
                    text_checks = evaluate_text_checks(
                        observation["content"], item.get("checks") or []
                    )
                    errors = [c["name"] for c in text_checks if not c["passed"]]
                    attempt = {
                        "attempt": attempt_index + 1,
                        "latency_ms": result["latency_s"] * 1000.0,
                        "text_checks": text_checks,
                        "budget": budget,
                        **observation,
                    }
                    tool_failed = False
                    expect = item.get("expect_tool")
                    if expect:
                        validations = [
                            validate_function_tool_call(
                                message, expect["name"], expect.get("required_args") or {}
                            )
                            for message in messages
                        ]
                        valid = [value for value in validations if value["valid"]]
                        attempt["tool_call"] = {
                            "valid": bool(valid),
                            "arguments": valid[0]["arguments"] if valid else None,
                            "validation_errors": [
                                value["error"] for value in validations if value["error"]
                            ],
                        }
                        if not valid:
                            tool_failed = True
                            errors.extend(
                                attempt["tool_call"]["validation_errors"]
                                or ["response did not include tool_calls"]
                            )
                    checks_passed = not errors
                    allowed_finishes = ("tool_calls", "stop") if expect else ("stop",)
                    passed = _attempt_passed(
                        observation, checks_passed,
                        allowed_finish_reasons=allowed_finishes,
                    )
                    attempt["status"] = "passed" if passed else "failed"
                    failure_class = _failure_class(
                        observation, checks_passed=checks_passed
                    )
                    if tool_failed and failure_class in {
                            "deterministic_check_failed", "visible_answer_missing"}:
                        failure_class = "tool_call_failed"
                    attempt["failure_class"] = failure_class
                    if errors:
                        attempt["error"] = "; ".join(errors)
                    attempts.append(attempt)
                except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                    attempts.append({
                        "attempt": attempt_index + 1,
                        "status": "failed",
                        "error": str(exc),
                        "failure_class": "request_error",
                        "budget": budget,
                    })
            _aggregate_attempts(check, attempts, a.eval_min_pass_rate)
            if check["status"] != "passed":
                failure_classes = sorted({
                    attempt.get("failure_class") for attempt in attempts
                    if attempt.get("failure_class")
                })
                if not check.get("error"):
                    check["error"] = "pass rate below threshold"
                failures.append({
                    "suite": spec["suite"],
                    "eval_id": item["id"],
                    "error": check["error"],
                    "failure_classes": failure_classes,
                    "pass_rate": check["pass_rate"],
                })
            checks.append(check)
        external_suites[spec["suite"]] = {
            "status": "passed" if all(c["status"] == "passed" for c in checks) else "failed",
            "source": a.suite_file,
            "source_sha256": spec["_source_sha256"],
            "date": spec.get("date"),
            "work_class": spec.get("work_class"),
            "evidence_use": spec.get("evidence_use", "diagnostic"),
            "validator_strength": spec.get(
                "validator_strength", "deterministic_marker"
            ),
            "checks": checks,
        }

    voice_section = {
        "status": "not_run",
        "stt_latency_ms": a.stt_latency_ms,
        "llm_latency_ms": None,
        "tts_latency_ms": a.tts_latency_ms,
        "total_turn_latency_ms": a.voice_latency_ms,
    }
    if "voice" in suites:
        if a.voice_latency_ms is None:
            voice_section["status"] = "skipped"
            voice_section["reason"] = "voice latency metrics were not supplied"
        else:
            voice_section["status"] = "recorded"

    metrics = _result_metrics(chat_results)
    wall_ms = (time.time() - started_at) * 1000.0
    passed_contexts = [
        r["target_tokens"] for r in context_results if r.get("status") == "passed"
    ]
    intelligence_checks = intelligence_section.get("checks") or []
    intelligence_pass_rate = None
    if intelligence_checks:
        intelligence_pass_rate = (
            sum(1 for check in intelligence_checks if check.get("status") == "passed")
            / len(intelligence_checks)
        )
    evidence = {
        "schema": "anvil-serving.fast-tier-bakeoff/v1",
        "run_id": time.strftime("fast-bakeoff-%Y%m%dT%H%M%SZ", time.gmtime(started_at)),
        "identity": {
            "candidate_id": a.candidate_id,
            "config_id": a.config_id,
            "model": a.model,
            "base_url": a.base_url,
            "engine": a.engine,
            "gpu": a.gpu,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        },
        "source_recipe": {
            "ref": a.source_recipe,
            "serve_command": a.serve_command,
        },
        "selection": {
            "suites": suites,
            "context_targets": context_targets,
            "requests_per_context": 1,
            "endpoint_already_loaded": True,
        },
        "evaluation_protocol": {
            "version": 3,
            "repetitions": a.eval_repetitions,
            "minimum_pass_rate": a.eval_min_pass_rate,
            "minimum_comparable_repetitions": _MIN_COMPARABLE_REPETITIONS,
            "visible_answer_tokens": a.visible_answer_tokens or 256,
            "reasoning_headroom_tokens": (
                a.reasoning_headroom_tokens
                if a.reasoning_headroom_tokens is not None else 0
            ),
            "budget_semantics": (
                "visible_answer_tokens plus reasoning_headroom_tokens are sent as one "
                "max_tokens cap; the endpoint does not hard-partition the channels"
            ),
            "records_full_visible_answer": True,
            "records_finish_reason": True,
            "records_reasoning_channel_metadata": True,
        },
        "timing": {
            "wall_ms": wall_ms,
            "chat": metrics,
        },
        "context": {
            "max_model_len": max_model_len,
            "cap_tokens": cap,
            "targets": context_results,
        },
        "tool": tool_section,
        "session": session_section,
        "intelligence": intelligence_section,
        "suites": external_suites,
        "thinking": thinking_section,
        "voice": voice_section,
        "score_inputs": {
            "voice_latency_ms": a.voice_latency_ms,
            "tool_call_passed": tool_section.get("status") == "passed",
            "session_recall_passed": session_section.get("status") == "passed",
            "intelligence_pass_rate": intelligence_pass_rate,
            "usable_context_tokens": max(passed_contexts) if passed_contexts else None,
            "ttft_p50_ms": metrics["ttft_p50_ms"],
            "e2e_p50_ms": metrics["e2e_p50_ms"],
            "thinking_mode": thinking_section["mode"],
            "operational_fit_notes": [
                "endpoint was already loaded; benchmark did not start or stop serves"
            ],
        },
        "failures": failures,
    }
    evidence["identity"] = {
        key: value for key, value in evidence["identity"].items() if value is not None
    }
    if a.evidence_out:
        _atomic_write_json(a.evidence_out, evidence)
        print("wrote bakeoff evidence: " + a.evidence_out)
    else:
        print(json.dumps(evidence, indent=2, sort_keys=True))

    # Persist into the bakeoff notebook (in ADDITION to --evidence-out, so the
    # existing behavior never regresses). Requires --notebook-task/-hardware to
    # key the comparison; missing them is a loud error, not a silent skip.
    if getattr(a, "notebook", None):
        if not a.notebook_task or not a.notebook_hardware:
            print("error: --notebook requires --notebook-task and --notebook-hardware",
                  file=sys.stderr)
            return 2
        from .external_benchmarks import store as _nb_store

        row_id = _nb_store.record_bakeoff_run(
            a.notebook, evidence,
            task=a.notebook_task, hardware=a.notebook_hardware,
            evidence_path=a.evidence_out,
        )
        print("recorded bakeoff run %s into notebook %s (row %d)"
              % (evidence["run_id"], a.notebook, row_id))
    return 1 if failures else 0

def _serve_recipes():
    """Import the shared serve-recipe helpers for package or direct script execution."""
    try:
        from . import serve_recipes as sr  # package import
    except ImportError:
        import serve_recipes as sr  # bare-script import (same dir on sys.path)
    return sr

def build_recipe(a, summary, *, capture=None, hardware=None):
    """Assemble a serve-recipe dict from a completed benchmark run + a live container.

    The reproducible half (image/env/flags/port + gpu_uuid) comes from
    `capture_from_container`; the hardware name/VRAM from `capture_hardware`; the
    [recipe.measured] numbers from THIS run's `summary`; and [recipe.intent] from the
    --recipe-* flags. `capture` / `hardware` are injectable for hermetic tests.
    """
    sr = _serve_recipes()
    capture = capture or sr.capture_from_container
    hardware = hardware or sr.capture_hardware

    cap = capture(a.recipe_from_container) if a.recipe_from_container else {}
    serve = dict(cap.get("serve") or {})
    hw = dict(cap.get("hardware") or {})
    gpu_uuid = hw.get("gpu_uuid")
    # Only capture hardware when the GPU is KNOWN — capture_hardware(None) would pick the
    # FIRST GPU row, silently recording the wrong card on a multi-GPU box (Copilot review).
    gpu = hardware(gpu_uuid) if gpu_uuid else {}

    recipe = {"model": a.recipe_model or a.model, "status": a.recipe_status}
    recipe["source"] = "measured via anvil-serving eval benchmark run (%s)" % summary.get("run_id", "")

    hardware_block = {}
    if gpu.get("gpu"): hardware_block["gpu"] = gpu["gpu"]
    if gpu.get("vram_total_gb") is not None: hardware_block["vram_total_gb"] = gpu["vram_total_gb"]
    if gpu_uuid: hardware_block["gpu_uuid"] = gpu_uuid
    if hardware_block: recipe["hardware"] = hardware_block

    ctx = summary.get("context_tokens") or summary.get("max_context_tokens")
    if ctx and "context_tokens" not in serve:
        serve["context_tokens"] = ctx
    if serve: recipe["serve"] = serve

    metrics = summary.get("metrics") or {}
    measured = {}
    tps = metrics.get("throughput_tok_s")
    if tps is not None:
        # throughput_tok_s is the AGGREGATE across all concurrent streams. Only label
        # it single-stream when concurrency==1; otherwise record it as aggregate + the
        # concurrency, so a generated recipe never mislabels a ~20-way number as the
        # single-stream stat the registry treats as its headline. (critic SHOULD-FIX)
        conc = summary.get("concurrency") or 1
        if conc == 1:
            measured["throughput_single_tok_s"] = round(tps, 1)
        else:
            measured["throughput_aggregate_tok_s"] = round(tps, 1)
            measured["concurrency"] = conc
    ttft = metrics.get("ttft_p50_ms")
    if ttft is not None:
        measured["ttft_p50_ms"] = round(ttft, 1)
    if ctx:
        measured["context_tokens"] = ctx
    if measured: recipe["measured"] = measured

    intent = {}
    if a.recipe_intent:
        suited = [s.strip() for s in a.recipe_intent.split(",") if s.strip()]
        if suited: intent["suited"] = suited
    if a.recipe_mode:
        intent["mode"] = a.recipe_mode
    if intent: recipe["intent"] = intent

    return recipe

def emit_recipe(a, summary, *, capture=None, hardware=None, append=None):
    """Render + persist the recipe for this benchmark run (stdout if --recipe-out '-')."""
    sr = _serve_recipes()
    recipe = build_recipe(a, summary, capture=capture, hardware=hardware)
    if a.recipe_out == "-":
        print(sr.format_recipe(recipe), end="")
    else:
        try:
            (append or sr.append_recipe)(a.recipe_out, recipe)
        except OSError as exc:
            print("could not write serve recipe to %s: %s" % (a.recipe_out, exc), file=sys.stderr)
            return recipe
        print("recorded serve recipe for %s -> %s" % (recipe["model"], a.recipe_out))
    return recipe

def main(argv=None, *, prog=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    workload = argv.pop(0) if argv and argv[0] in {"capacity", "quality"} else None
    if prog is None:
        prog = "anvil-serving eval benchmark"
        if workload is not None:
            prog += " " + workload
    if workload is not None:
        prog = "anvil-serving eval benchmark %s" % workload
    if argv and argv[0] == "external":
        from .external_benchmarks import cli as external_bench
        return external_bench.main(
            argv[1:], prog="anvil-serving eval benchmark external"
        )

    if workload == "capacity":
        description = (
            "Measure bounded endpoint latency, throughput, context, and prefix-cache behavior."
        )
        examples = (
            "Examples:\n"
            "  anvil-serving eval benchmark capacity --tier heavy --requests 10 "
            "--concurrency 1 --output heavy-capacity.json --confirm\n"
            "  anvil-serving eval benchmark capacity --base-url "
            "http://127.0.0.1:30002/v1 --model MODEL --engine vllm "
            "--gpu dark-heavy --output run.json --confirm"
        )
    elif workload == "quality":
        description = (
            "Run repeated, bounded quality suites and retain comparison-grade evidence."
        )
        examples = (
            "Examples:\n"
            "  anvil-serving eval benchmark quality --tier heavy --suite-file suite.json "
            "--candidate-id MODEL --config-id heavy-v1 --control-status verified "
            "--control-evidence evidence/control.json --output quality.json --confirm\n"
            "  anvil-serving eval benchmark quality --base-url "
            "http://127.0.0.1:30002/v1 --model MODEL --engine vllm --gpu dark-heavy "
            "--suite intelligence --candidate-id MODEL --config-id direct "
            "--output quality.json --confirm"
        )
    else:
        description = "Benchmark a direct endpoint or a serves-manifest tier."
        examples = (
            "Compatibility parser: prefer `eval benchmark capacity` or "
            "`eval benchmark quality`."
        )

    def visible_for(*workloads):
        return workload is None or workload in workloads

    def help_for(text, *workloads):
        return text if visible_for(*workloads) else argparse.SUPPRESS

    ap = argparse.ArgumentParser(
        prog=prog,
        description=description + "\n\n" + examples,
        epilog=(
            "Configuration precedence: command flags, referenced serves manifest, then "
            "the bundled reference manifest. Direct targets require both --base-url and --model."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    endpoint = ap.add_argument_group("direct endpoint input")
    endpoint.add_argument("--base-url", help="OpenAI-compatible endpoint base URL")
    endpoint.add_argument("--model", help="served model id")
    endpoint.add_argument(
        "--engine",
        help="engine identity recorded in evidence (inferred from --tier when available)",
    )
    endpoint.add_argument(
        "--gpu",
        help="stable GPU or hardware-role identity recorded in comparable evidence",
    )
    manifest = ap.add_argument_group("serves manifest input")
    manifest.add_argument("--manifest", help="serves manifest TOML (used with --tier)")
    manifest.add_argument("--tier", help="serve name in the manifest; fills endpoint and model")
    recipe = ap.add_argument_group("serve recipe input")
    recipe.add_argument("--recipe", help="recorded recipe model selector")
    recipe.add_argument("--registry", help="serve-recipe registry used with --recipe")
    ap.add_argument("--api-key-env", default=None,
                    help="read the bearer token from this environment variable")
    ap.add_argument("--requests", type=int, default=60,
                    help=help_for("number of requests (1..10000; default %(default)s)", "capacity"))
    ap.add_argument("--concurrency", type=int, default=20,
                    help=help_for("parallel requests (1..256; default %(default)s)", "capacity"))
    ap.add_argument("--burst", type=int, default=0,
                    help=help_for("shared-prefix burst size (0..256; 0 disables)", "capacity"))
    ap.add_argument("--shared-prefix-tokens", type=int, default=8000,
                    help=help_for("shared prefix size in estimated tokens", "capacity", "quality"))
    ap.add_argument("--ctx-tokens", type=int, default=0,
                    help=help_for("fixed context; 0 samples the measured distribution", "capacity"))
    ap.add_argument("--max-tokens", type=int, default=64,
                    help=help_for("capacity generation cap; quality context-probe cap", "capacity", "quality"))
    ap.add_argument("--max-model-len", type=int, default=0,
                    help=help_for("context window override; 0 discovers /v1/models. Requests are "
                                  "clamped below the limit using --margin.",
                                  "capacity", "quality"))
    ap.add_argument("--margin", type=int, default=DEFAULT_CTX_MARGIN,
                    help=help_for("token headroom below max_model_len (default %(default)s)",
                                  "capacity", "quality"))
    ap.add_argument("--no-thinking", action="store_true",
                    help="compatibility alias for --thinking-mode disabled; valid only for "
                         "chat-template-controlled model families")
    ap.add_argument("--thinking-mode", choices=("default", "enabled", "disabled", "unsupported"),
                    default=None,
                    help="record/request thinking behavior for benchmark evidence. "
                         "disabled maps to chat_template_kwargs={'enable_thinking': False}; "
                         "enabled maps to {'enable_thinking': True}; unsupported records that "
                         "the serve has no supported thinking control.")
    ap.add_argument("--reasoning-effort", choices=("none", "minimal", "low", "medium", "high"),
                    default=None,
                    help="send the OpenAI-compatible reasoning_effort field for model families "
                         "that do not use chat_template_kwargs (for example GPT-OSS or Mistral). "
                         "Cannot be combined with --no-thinking or an explicit thinking mode.")
    ap.add_argument("--visible-answer-tokens", type=int, default=None,
                    help=help_for("override suite visible-answer allocation (default 256)", "quality"))
    ap.add_argument("--reasoning-headroom-tokens", type=int, default=None,
                    help=help_for("reasoning headroom added to visible output (default 0)", "quality"))
    ap.add_argument("--eval-repetitions", type=int, default=3,
                    help=help_for("attempts per quality check (1..20; default %(default)s)", "quality"))
    ap.add_argument("--eval-min-pass-rate", type=float, default=1.0,
                    help=help_for("minimum attempt pass rate (0..1; default %(default)s)", "quality"))
    capacity_output_flags = ("--json-out", "--output") if workload == "capacity" else ("--json-out",)
    ap.add_argument(*capacity_output_flags, dest="json_out", default=None,
                    help=help_for("write the capacity artifact atomically", "capacity"))
    # --- GENERATE a serve recipe as a side effect of benchmarking a live serve ------
    # (READ them back with `anvil-serving models recipes list|show`.) All optional.
    ap.add_argument("--recipe-out", default=None,
                    help=help_for("after the run, record a [[recipe]] block: PATH to append to the "
                         "serve-recipe registry, or '-' for stdout. Captures the live serve's "
                         "reproducible docker config + THIS run's measured numbers.", "legacy"))
    ap.add_argument("--recipe-from-container", default=None, metavar="NAME",
                    help=help_for("docker container to capture for legacy recipe output", "legacy"))
    ap.add_argument("--recipe-intent", default=None, metavar="CSV",
                    help=help_for("legacy recipe intent CSV", "legacy"))
    ap.add_argument("--recipe-mode", default=None,
                    help=help_for("legacy recipe mode", "legacy"))
    ap.add_argument("--recipe-status", default="verified",
                    help=help_for("legacy recipe provenance status", "legacy"))
    ap.add_argument("--recipe-model", default=None, metavar="NAME",
                    help=help_for("legacy recipe model", "legacy"))
    # --- Fast-tier bakeoff evidence mode: target an already-loaded endpoint -----
    ap.add_argument("--bakeoff", action="store_true",
                    help=help_for("legacy quality-mode selector", "legacy"))
    ap.add_argument("--candidate-id", default=None,
                    help=help_for("candidate identifier recorded in quality evidence", "quality"))
    ap.add_argument("--config-id", default=None,
                    help=help_for("serve/config identifier recorded in quality evidence", "quality"))
    ap.add_argument("--context-targets", default="32768",
                    help=help_for("comma-separated quality context targets", "quality"))
    ap.add_argument("--suite", action="append",
                    help=help_for("repeatable/comma-separated: chat, context, tool, session, "
                                  "intelligence, voice", "quality"))
    ap.add_argument("--suite-file", default=None, metavar="SPECS_JSON",
                    help=help_for("externally-authored quality suite; runs only that suite unless "
                                  "--suite also selects built-in checks", "quality"))
    quality_output_flags = ("--evidence-out", "--output") if workload == "quality" else ("--evidence-out",)
    ap.add_argument(*quality_output_flags, dest="evidence_out", default=None,
                    help=help_for("write the quality artifact atomically", "quality"))
    ap.add_argument("--notebook", default=None,
                    help=help_for("also append the run to this bakeoff notebook", "quality"))
    ap.add_argument("--notebook-task", default=None,
                    help=help_for("notebook task key", "quality"))
    ap.add_argument("--notebook-hardware", default=None,
                    help=help_for("notebook hardware key", "quality"))
    ap.add_argument("--source-recipe", default=None,
                    help=help_for("immutable recipe/config source reference", "quality"))
    ap.add_argument(
        "--control-status",
        choices=("verified", "supported", "requested_unverified"),
        help=help_for(
            "verification state for an explicit thinking/reasoning control; verified or "
            "supported requires --control-evidence", "quality"
        ),
    )
    ap.add_argument(
        "--control-evidence",
        help=help_for("stable path or URL proving the declared thinking-control status",
                      "quality"),
    )
    ap.add_argument("--serve-command", default=None,
                    help=help_for("serve command recorded for reproduction", "quality"))
    ap.add_argument("--voice-latency-ms", type=float, default=None,
                    help=help_for("external total voice latency in milliseconds", "quality"))
    ap.add_argument("--stt-latency-ms", type=float, default=None,
                    help=help_for("external STT latency in milliseconds", "quality"))
    ap.add_argument("--tts-latency-ms", type=float, default=None,
                    help=help_for("external TTS latency in milliseconds", "quality"))
    ap.add_argument("--timeout", "--timeout-seconds", dest="timeout", type=float, default=900.0,
                    help="request timeout in seconds (default %(default)s)")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="validate and print the resolved workload without sending requests or writing",
    )
    a = ap.parse_args(argv)
    if workload == "quality":
        a.bakeoff = True
    elif workload == "capacity":
        a.bakeoff = False
    canonical_forbidden = {
        "capacity": {
            "--bakeoff", "--suite", "--suite-file", "--candidate-id", "--config-id",
            "--evidence-out", "--notebook", "--voice-latency-ms", "--stt-latency-ms",
            "--tts-latency-ms", "--control-status", "--control-evidence",
        },
        "quality": {
            "--bakeoff", "--requests", "--concurrency", "--burst", "--ctx-tokens",
            "--json-out", "--recipe-out", "--recipe-from-container", "--recipe-intent",
            "--recipe-mode", "--recipe-status", "--recipe-model",
        },
    }
    if workload is not None:
        supplied_flags = {token.partition("=")[0] for token in argv if token.startswith("--")}
        forbidden = sorted(supplied_flags & canonical_forbidden[workload])
        if forbidden:
            ap.error("%s does not accept %s" % (workload, ", ".join(forbidden)))
    for label, target in (
            ("JSON output", a.json_out),
            ("evidence output", a.evidence_out),
            ("recipe output", a.recipe_out),
            ("notebook", a.notebook)):
        try:
            _validate_write_target(target, label=label)
        except OSError as exc:
            ap.error(str(exc))
    from .eval import resolve_endpoint_target
    try:
        a.base_url, a.model, selected = resolve_endpoint_target(
            tier=a.tier,
            manifest=a.manifest,
            base_url=a.base_url,
            model=a.model,
            recipe=a.recipe,
            registry=a.registry,
        )
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    if selected:
        a.engine = a.engine or selected.get("engine")
        a.gpu = a.gpu or selected.get("gpu_role")
        if a.source_recipe is None and a.tier:
            a.source_recipe = "%s#%s" % (a.manifest or "resolved-manifest", a.tier)
        elif a.source_recipe is None and a.recipe:
            a.source_recipe = selected.get("source_recipe")
    try:
        api_key = resolve_api_key(a.api_key_env)
    except ValueError as exc:
        ap.error(str(exc))

    visible_answer_tokens = (
        a.visible_answer_tokens if a.visible_answer_tokens is not None else 256
    )
    reasoning_headroom_tokens = (
        a.reasoning_headroom_tokens if a.reasoning_headroom_tokens is not None else 0
    )
    if not 0 < visible_answer_tokens <= _MAX_QUALITY_COMPLETION_TOKENS:
        ap.error(
            "--visible-answer-tokens must be from 1 through %d"
            % _MAX_QUALITY_COMPLETION_TOKENS
        )
    if not 0 <= reasoning_headroom_tokens <= _MAX_QUALITY_COMPLETION_TOKENS:
        ap.error(
            "--reasoning-headroom-tokens must be from 0 through %d"
            % _MAX_QUALITY_COMPLETION_TOKENS
        )
    if (visible_answer_tokens + reasoning_headroom_tokens
            > _MAX_QUALITY_COMPLETION_TOKENS):
        ap.error(
            "visible-answer plus reasoning-headroom allocation cannot exceed %d"
            % _MAX_QUALITY_COMPLETION_TOKENS
        )
    if not 0 < a.eval_repetitions <= _MAX_EVAL_REPETITIONS:
        ap.error("--eval-repetitions must be from 1 through %d" % _MAX_EVAL_REPETITIONS)
    if not math.isfinite(a.eval_min_pass_rate) or not 0 < a.eval_min_pass_rate <= 1:
        ap.error("--eval-min-pass-rate must be greater than 0 and at most 1")
    explicit_thinking = a.no_thinking or a.thinking_mode not in (None, "default")
    if a.reasoning_effort is not None and explicit_thinking:
        ap.error(
            "--reasoning-effort cannot be combined with --no-thinking or an explicit "
            "--thinking-mode"
        )
    if a.control_status in {"verified", "supported"} and not a.control_evidence:
        ap.error("--control-status verified/supported requires --control-evidence")
    if a.control_status is not None and not (
            explicit_thinking or a.reasoning_effort is not None):
        ap.error("--control-status requires an explicit thinking or reasoning control")
    if a.control_evidence and a.control_status not in {"verified", "supported"}:
        ap.error("--control-evidence requires --control-status verified or supported")
    try:
        validate_reasoning_control(
            a.model,
            thinking_mode=a.thinking_mode,
            no_thinking=a.no_thinking,
            reasoning_effort=a.reasoning_effort,
        )
    except ValueError as exc:
        ap.error(str(exc))
    a.control_evidence_sha256 = None
    if a.control_evidence:
        mechanism = (
            "reasoning_effort" if a.reasoning_effort is not None
            else "chat_template_kwargs"
        )
        try:
            a.control_evidence, a.control_evidence_sha256 = load_control_evidence(
                a.control_evidence,
                status=a.control_status,
                mechanism=mechanism,
            )
        except ValueError as exc:
            ap.error("--control-evidence: %s" % exc)

    if a.suite_file and not a.bakeoff:
        ap.error("--suite-file requires --bakeoff (it runs through the bakeoff evidence engine)")
    if not math.isfinite(a.timeout) or not 0 < a.timeout <= 3600:
        ap.error("--timeout must be greater than 0 and at most 3600 seconds")

    if a.bakeoff:
        known_suites = {"chat", "context", "tool", "session", "intelligence", "voice"}
        selected_suites = parse_csv(
            a.suite,
            default=[] if a.suite_file or workload == "quality" else ["chat"],
        )
        if workload == "quality" and not selected_suites and not a.suite_file:
            ap.error("quality requires --suite-file or at least one explicit --suite")
        unknown_suites = sorted(set(selected_suites) - known_suites)
        if unknown_suites:
            ap.error("--suite: unknown value(s): %s" % ", ".join(unknown_suites))
        if "voice" in selected_suites and a.voice_latency_ms is None:
            ap.error("--suite voice requires --voice-latency-ms")
        if not a.candidate_id or not a.config_id:
            ap.error(
                "quality requires --candidate-id and --config-id"
                if workload == "quality"
                else "--bakeoff requires --candidate-id and --config-id"
            )
        a.suite_spec = None
        if a.suite_file:
            # validate BEFORE any request is sent: a malformed spec is an operator
            # error (exit 2 + message), never partial evidence.
            try:
                a.suite_spec = load_suite_spec(a.suite_file)
            except (OSError, ValueError) as exc:
                ap.error("--suite-file: %s" % exc)
        if a.notebook:
            if not a.notebook_task or not a.notebook_hardware:
                ap.error("--notebook requires --notebook-task and --notebook-hardware")
            if not a.suite_spec:
                ap.error(
                    "--notebook requires an explicit ranking --suite-file with a "
                    "strong validator"
                )
            if (a.suite_spec.get("evidence_use") != "ranking"
                    or a.suite_spec.get("validator_strength") not in {
                        "exact_choice", "typed_structure"
                    }):
                ap.error(
                    "--notebook requires evidence_use=ranking and validator_strength "
                    "exact_choice or typed_structure"
                )
        try:
            validate_eval_work_plan(a, a.suite_spec)
        except ValueError as exc:
            ap.error(str(exc))
        if a.dry_run:
            print(json.dumps({
                "schema": "anvil-serving.eval-plan/v1",
                "workload": "quality",
                "target": {
                    "base_url": a.base_url,
                    "model": a.model,
                    "engine": a.engine,
                    "gpu": a.gpu,
                    "tier": a.tier,
                    "manifest": a.manifest,
                },
                "quality": {
                    "suites": selected_suites,
                    "suite_file": a.suite_file,
                    "repetitions": a.eval_repetitions,
                    "minimum_pass_rate": a.eval_min_pass_rate,
                    "visible_answer_tokens": visible_answer_tokens,
                    "reasoning_headroom_tokens": reasoning_headroom_tokens,
                },
                "output": a.evidence_out,
                "deferred": ["endpoint identity", "model requests", "artifact write"],
            }, indent=2, sort_keys=True, ensure_ascii=True))
            return 0
        return run_bakeoff(a, api_key)

    bounds = (
        ("--requests", a.requests, 1, 10000),
        ("--concurrency", a.concurrency, 1, 256),
        ("--burst", a.burst, 0, 256),
        ("--shared-prefix-tokens", a.shared_prefix_tokens, 0, 262144),
        ("--ctx-tokens", a.ctx_tokens, 0, 1048576),
        ("--max-tokens", a.max_tokens, 1, 65536),
        ("--max-model-len", a.max_model_len, 0, 1048576),
        ("--margin", a.margin, 0, 1048576),
    )
    for flag, value, minimum, maximum in bounds:
        if not minimum <= value <= maximum:
            ap.error("%s must be from %d through %d" % (flag, minimum, maximum))
    if a.dry_run:
        print(json.dumps({
            "schema": "anvil-serving.eval-plan/v1",
            "workload": "capacity",
            "target": {
                "base_url": a.base_url,
                "model": a.model,
                "engine": a.engine,
                "gpu": a.gpu,
                "tier": a.tier,
                "manifest": a.manifest,
            },
            "capacity": {
                "requests": a.burst or a.requests,
                "concurrency": a.burst or a.concurrency,
                "context_tokens": a.ctx_tokens or "measured-distribution",
                "max_tokens": a.max_tokens,
                "timeout_seconds": a.timeout,
            },
            "output": a.json_out,
            "deferred": ["endpoint identity", "context-window probe", "requests", "artifact write"],
        }, indent=2, sort_keys=True, ensure_ascii=True))
        return 0

    # Resolve the serve's context window: explicit flag wins; else best-effort probe /v1/models.
    max_model_len = a.max_model_len or detect_max_model_len(a.base_url, a.model, api_key)
    cap = ctx_cap(max_model_len, a.max_tokens, a.margin)
    ctk, reasoning_effort, thinking = resolve_thinking_settings(a)
    control_kwargs = _request_control_kwargs(ctk, reasoning_effort)

    n = a.burst if a.burst else a.requests
    conc = a.burst if a.burst else a.concurrency
    planned_context = a.ctx_tokens or min(cap or 262144, 262144)
    if n * planned_context > _MAX_TOTAL_CAPACITY_PROMPT_TOKENS:
        ap.error(
            "capacity workload exceeds %d aggregate prompt tokens; reduce "
            "--requests/--burst or --ctx-tokens"
            % _MAX_TOTAL_CAPACITY_PROMPT_TOKENS
        )
    shared = (FILLER % (0, 0)) * max(1, int(a.shared_prefix_tokens * 0.75) // 6)

    def run_request(i):
        ctx = clamp_ctx(a.ctx_tokens or sample_ctx(), cap)
        prompt = make_prompt(
            shared, ctx, i if not a.burst else 0, max_prompt_tokens=cap
        )
        return stream_chat(
            a.base_url, a.model, prompt, api_key, a.max_tokens,
            timeout=a.timeout, **control_kwargs,
        )

    capnote = f" max_model_len={max_model_len}(ctx<={cap})" if cap is not None else ""
    thinknote = "" if thinking["mode"] == "default" else f" thinking={thinking['mode']}"
    if reasoning_effort is not None:
        thinknote += f" reasoning_effort={reasoning_effort}"
    print(_console_safe(
        f"BENCH {a.base_url} model={a.model}  n={n} concurrency={conc} "
        f"{'BURST(shared-prefix)' if a.burst else 'mixed'} "
        f"max_tokens={a.max_tokens}{capnote}{thinknote}"
    ))
    started_at = time.time()
    t0 = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=conc) as ex:
        # Futures retain only integer indices; each worker builds at most one
        # prompt, so memory scales with concurrency rather than request count.
        futs = [ex.submit(run_request, i) for i in range(n)]
        for f in as_completed(futs):
            try: results.append(f.result())
            except Exception as e: print("  req error:", e)
    wall = time.perf_counter() - t0
    ttfts = [r["ttft"] for r in results]; e2es = [r["e2e"] for r in results]
    out_tot = sum(r["out_toks"] for r in results)
    cfs = [cached_fraction(r["usage"]) for r in results]
    cfs = [c for c in cfs if c is not None]
    print("-"*60)
    print(f"completed:        {len(results)}/{n} in {wall:.1f}s")
    print(f"TTFT  p50/p95:    {pctile(ttfts,50):.2f}s / {pctile(ttfts,95):.2f}s")
    print(f"E2E   p50/p95:    {pctile(e2es,50):.2f}s / {pctile(e2es,95):.2f}s")
    print(f"throughput:       {(out_tot / wall if wall else 0.0):.0f} output tok/s (aggregate)")
    summary = {
        "schema": "anvil-serving.benchmark/v1",
        "run_id": time.strftime("benchmark-%Y%m%dT%H%M%SZ", time.gmtime(started_at)),
        "base_url": a.base_url,
        "model": a.model,
        "requests": n,
        "completed": len(results),
        "concurrency": conc,
        "context_tokens": a.ctx_tokens or None,
        "max_context_tokens": max_model_len,
        "max_tokens": a.max_tokens,
        "serve_flags": {
            "shared_prefix_burst": bool(a.burst),
            "no_thinking": bool(a.no_thinking),
            "thinking_mode": thinking["mode"],
            "reasoning_effort": reasoning_effort,
        },
        "metrics": {
            "ttft_p50_ms": pctile(ttfts, 50) * 1000.0,
            "ttft_p95_ms": pctile(ttfts, 95) * 1000.0,
            "e2e_p50_ms": pctile(e2es, 50) * 1000.0,
            "e2e_p95_ms": pctile(e2es, 95) * 1000.0,
            "throughput_tok_s": (out_tot / wall) if wall else 0.0,
            "output_tokens": out_tot,
            "prefix_cache_hit_avg": statistics.mean(cfs) if cfs else None,
        },
    }
    if cfs:
        print(f"prefix-cache hit: {statistics.mean(cfs)*100:.1f}% avg cached prompt tokens (KEY KPI)")
    else:
        print("prefix-cache hit: endpoint did not return prompt_tokens_details.cached_tokens")
    print("-"*60)
    print("Tip: run once cold, then immediately again -- TTFT should drop sharply on the 2nd run if prefix cache works.")
    if a.json_out:
        _atomic_write_json(a.json_out, summary)
        print("wrote JSON summary: " + a.json_out)
    if len(results) != n:
        if a.recipe_out:
            print(
                "skipping serve recipe: benchmark completed %d/%d requests" % (len(results), n),
                file=sys.stderr,
            )
        return 1
    # Benchmarking a serve ALSO records its reproducible recipe when asked.
    if a.recipe_out:
        emit_recipe(a, summary)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
