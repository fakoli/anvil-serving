"""Benchmark suite parsing and validation."""

import functools
import hashlib
import json
import os
import re

from .limits import (
    MAX_CONTEXT_TARGETS as _MAX_CONTEXT_TARGETS,
    MAX_CONTEXT_TARGET_TOKENS as _MAX_CONTEXT_TARGET_TOKENS,
    MAX_CONTROL_EVIDENCE_BYTES as _MAX_CONTROL_EVIDENCE_BYTES,
    MAX_QUALITY_COMPLETION_TOKENS as _MAX_QUALITY_COMPLETION_TOKENS,
    MAX_SUITE_EVALS as _MAX_SUITE_EVALS,
    MAX_SUITE_FILE_BYTES as _MAX_SUITE_FILE_BYTES,
    MAX_TOTAL_CONTEXT_TARGET_TOKENS as _MAX_TOTAL_CONTEXT_TARGET_TOKENS,
    MIN_CONTEXT_TARGET_TOKENS as _MIN_CONTEXT_TARGET_TOKENS,
)


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
        if not _MIN_CONTEXT_TARGET_TOKENS <= target <= _MAX_CONTEXT_TARGET_TOKENS:
            raise ValueError(
                "context targets must be integers from %d through %d"
                % (_MIN_CONTEXT_TARGET_TOKENS, _MAX_CONTEXT_TARGET_TOKENS)
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

_CHECK_KEYS = ("contains", "contains_all", "contains_any", "matches_regex")
_MAX_CHECK_REGEX_CHARS = 512
@functools.lru_cache(maxsize=128)
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
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"].strip()
        ):
            raise ValueError("evals[%d] must be an object with a non-empty string 'id'" % i)
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
        for message in messages or []:
            if (
                not isinstance(message.get("role"), str)
                or not message["role"].strip()
                or not isinstance(message.get("content"), str)
                or not message["content"]
            ):
                raise ValueError(
                    "%s: each message needs non-empty string role and content" % where
                )
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
    supplied = os.fspath(path)
    resolved = os.path.abspath(os.path.expanduser(supplied))
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
    # Resolve the supplied path only for I/O. Evidence should retain the
    # operator's portable relative reference instead of leaking the absolute
    # path of the workstation that produced it.
    evidence_reference = supplied.replace("\\", "/")
    return evidence_reference, hashlib.sha256(raw).hexdigest()

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
