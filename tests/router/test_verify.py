"""Unit tests for the T007 cheap inline structural verifiers.

Every concrete check gets a passing fixture and a failing fixture (acceptance
criterion 1). The ``test_no_network_*`` cases pin the purely-local property
(acceptance criterion 2): with every network surface monkeypatched to raise, the
whole verifier chain still produces results — proving the verify path makes no
network/LLM call.

Hermetic and stdlib-only (pytest is the only test dep).
"""

from __future__ import annotations

import http.client
import socket
import time
import urllib.request

import pytest

from anvil_serving.router.verify import (
    MAX_SCAN_BYTES,
    CodeParses,
    DiffWellFormed,
    FormatWellFormed,
    NonEmptyContent,
    NotTruncated,
    RefusalMarker,
    ResponseView,
    ToolCallJSONValid,
    VerifyResult,
    aggregate,
    all_passed,
    default_verifiers,
    run_verifiers,
)


# --------------------------------------------------------------------------- #
# NonEmptyContent
# --------------------------------------------------------------------------- #
def test_non_empty_content_pass_text():
    r = NonEmptyContent().verify(ResponseView(text="hello world"))
    assert r.passed and r.score == 1.0
    assert "non-empty" in r.reason


def test_non_empty_content_pass_tool_only():
    # Empty text but a tool call is legitimately non-empty.
    r = NonEmptyContent().verify(
        ResponseView(text="   ", tool_calls=[{"name": "ls", "arguments": "{}"}]))
    assert r.passed


def test_non_empty_content_fail():
    r = NonEmptyContent().verify(ResponseView(text="   \n\t "))
    assert not r.passed and r.score == 0.0
    assert "empty" in r.reason.lower()


def test_non_empty_content_fail_empty_text_and_empty_tool_calls_list():
    # An explicit empty LIST of tool calls (as opposed to None) must still
    # read as "no tool calls" -> the thinking-budget-starvation failure mode
    # must still fail, not be masked by a falsy-but-present tool_calls field.
    r = NonEmptyContent().verify(ResponseView(text="", tool_calls=[]))
    assert not r.passed and r.score == 0.0
    assert "empty" in r.reason.lower()


def test_non_empty_content_pass_multiple_tool_calls_no_text():
    # The exact live-testing shape (v0.6.1 hotfix): a local model's tool-call
    # turn with genuinely empty text content and >=1 tool call must PASS, not
    # be misread as thinking-budget starvation.
    r = NonEmptyContent().verify(
        ResponseView(
            text="",
            finish_reason="tool_calls",
            tool_calls=[
                {"name": "read_file", "arguments": '{"path": "a.py"}'},
                {"name": "ls", "arguments": "{}"},
            ],
        )
    )
    assert r.passed and r.score == 1.0


# --------------------------------------------------------------------------- #
# NotTruncated
# --------------------------------------------------------------------------- #
def test_not_truncated_pass_clean_stop():
    for reason in ("stop", "end_turn", "stop_sequence", "tool_calls", None):
        r = NotTruncated().verify(ResponseView(text="x", finish_reason=reason))
        assert r.passed, reason
        assert r.score == 1.0


def test_not_truncated_fail():
    for reason in ("length", "max_tokens"):
        r = NotTruncated().verify(ResponseView(text="x", finish_reason=reason))
        assert not r.passed and r.score == 0.0
        assert "truncat" in r.reason.lower()


# --------------------------------------------------------------------------- #
# NotTruncated x caller_max_tokens (v0.7.1 — the live contextWindow-clamp
# incident: a harness-computed max_completion_tokens floored at 1 must not
# 503 every turn and trip the circuit breaker).
# --------------------------------------------------------------------------- #
def test_not_truncated_pass_caller_capped_length():
    # The caller explicitly asked for a 1-token completion (max_tokens=1) and
    # the model stopped exactly there with non-empty content: that is
    # compliance with the caller's request, not a structural defect.
    for reason in ("length", "max_tokens", "model_length"):
        r = NotTruncated().verify(
            ResponseView(text="ok", finish_reason=reason, caller_max_tokens=1))
        assert r.passed, reason
        assert r.score == 1.0
        assert "caller-capped" in r.reason.lower() or "compliance" in r.reason.lower()


def test_not_truncated_fail_length_no_caller_cap():
    # No explicit caller cap (max_tokens was never set on the request) but the
    # completion still stopped at "length": this is the tier's OWN default
    # budget being hit, unrequested — still a genuine, unexpected truncation.
    r = NotTruncated().verify(
        ResponseView(text="partial answer", finish_reason="length", caller_max_tokens=None))
    assert not r.passed and r.score == 0.0
    assert "truncat" in r.reason.lower()


def test_not_truncated_pass_caller_cap_set_but_clean_stop():
    # A caller cap being present must not change the verdict for a clean stop
    # — caller_max_tokens only matters when finish_reason is length-like.
    r = NotTruncated().verify(
        ResponseView(text="done", finish_reason="stop", caller_max_tokens=4096))
    assert r.passed and r.score == 1.0


def test_not_truncated_and_non_empty_content_chain_empty_capped_length_still_fails():
    # CRITICAL INTERACTION (thinking-budget starvation, CLAUDE.md gotcha #9):
    # caller sets max_tokens=16000, the model burns the whole budget reasoning
    # and returns EMPTY content with finish_reason="length". NotTruncated alone
    # now passes (the cap was honored) but NonEmptyContent must STILL fail —
    # empty is empty regardless of any cap. Only a NON-EMPTY caller-capped
    # length response should pass the full chain.
    starved = ResponseView(text="", finish_reason="length", caller_max_tokens=16000)
    nt = NotTruncated().verify(starved)
    nec = NonEmptyContent().verify(starved)
    assert nt.passed, "caller-capped length alone must pass NotTruncated"
    assert not nec.passed, "empty content must still fail NonEmptyContent regardless of cap"
    assert not all_passed([nt, nec])


def test_not_truncated_and_non_empty_content_chain_nonempty_capped_length_passes():
    # The companion pin: a NON-EMPTY caller-capped length response passes the
    # whole chain — this is the exact live-incident shape (max_tokens=1,
    # model returns its one token, finish_reason="length").
    ok = ResponseView(text="1", finish_reason="length", caller_max_tokens=1)
    nt = NotTruncated().verify(ok)
    nec = NonEmptyContent().verify(ok)
    assert nt.passed and nec.passed
    assert all_passed([nt, nec])


def test_not_truncated_caller_cap_zero_is_still_an_explicit_cap():
    # caller_max_tokens=0 is falsy but semantically distinct from None (the
    # caller DID set an explicit field, even if the value is degenerate) — the
    # field must be compared with `is not None`, not truthiness.
    r = NotTruncated().verify(
        ResponseView(text="x", finish_reason="length", caller_max_tokens=0))
    assert r.passed, "caller_max_tokens=0 must still read as an explicit caller cap"


# --------------------------------------------------------------------------- #
# ToolCallJSONValid
# --------------------------------------------------------------------------- #
def test_tool_call_json_valid_pass():
    r = ToolCallJSONValid().verify(ResponseView(
        text="",
        tool_calls=[
            {"name": "read", "arguments": '{"path": "/etc/hosts"}'},
            {"name": "noop", "arguments": ""},          # no-arg sentinel
            {"function": {"name": "nested", "arguments": '{"ok": true}'}},  # OpenAI shape
        ],
    ))
    assert r.passed and r.score == 1.0


def test_tool_call_json_valid_fail():
    r = ToolCallJSONValid().verify(ResponseView(
        text="",
        tool_calls=[
            {"name": "read", "arguments": '{"path": "/etc/hosts"}'},   # ok
            {"name": "write", "arguments": '{"path": "/etc/'},          # truncated JSON
        ],
    ))
    assert not r.passed
    assert r.score == 0.5  # one of two parsed
    assert "write" in r.reason


def test_tool_call_json_valid_required_keys_shallow_schema():
    schema = {"write": ["path", "content"]}
    ok = ToolCallJSONValid(required_keys=schema).verify(ResponseView(
        tool_calls=[{"name": "write", "arguments": '{"path": "a", "content": "b"}'}]))
    assert ok.passed
    bad = ToolCallJSONValid(required_keys=schema).verify(ResponseView(
        tool_calls=[{"name": "write", "arguments": '{"path": "a"}'}]))
    assert not bad.passed
    assert "content" in bad.reason


def test_tool_call_json_valid_non_dict_elements_fail_gracefully():
    # Review fix #1: adversarial non-dict tool-call elements must NOT crash the
    # verifier; each is flagged as malformed and the call returns a verdict.
    r = ToolCallJSONValid().verify(ResponseView(tool_calls=[None, "foo", 5]))
    assert not r.passed and r.score == 0.0
    assert r.reason.count("not an object") == 3


def test_tool_call_json_valid_pass_already_parsed_object():
    # Review fix #6: native Anthropic tool_use.input arrives already parsed as a
    # dict/list (not a JSON string) and is valid by construction.
    r = ToolCallJSONValid().verify(ResponseView(tool_calls=[
        {"name": "write", "arguments": {"path": "a", "content": "b"}},
        {"name": "tags", "arguments": ["x", "y"]},
    ]))
    assert r.passed and r.score == 1.0
    # required_keys still apply to an already-parsed dict.
    bad = ToolCallJSONValid(required_keys={"write": ["content"]}).verify(
        ResponseView(tool_calls=[{"name": "write", "arguments": {"path": "a"}}]))
    assert not bad.passed and "content" in bad.reason


def test_tool_call_required_keys_with_empty_args_fails():
    # Review fix #7: an empty/no-arg sentinel must NOT bypass required keys.
    schema = {"write": ["path"]}
    bad = ToolCallJSONValid(required_keys=schema).verify(
        ResponseView(tool_calls=[{"name": "write", "arguments": ""}]))
    assert not bad.passed and "path" in bad.reason
    # ...but empty args are fine when nothing is required for that tool.
    ok = ToolCallJSONValid().verify(
        ResponseView(tool_calls=[{"name": "noop", "arguments": ""}]))
    assert ok.passed


def test_tool_call_deeply_nested_json_fails_no_crash():
    # Review fix #2: pathological nesting makes json.loads raise RecursionError
    # (not ValueError); the verifier must catch it as a fail, not propagate.
    deep = "[" * 100000 + "]" * 100000
    r = ToolCallJSONValid().verify(
        ResponseView(tool_calls=[{"name": "x", "arguments": deep}]))
    assert not r.passed and r.score == 0.0


def test_tool_call_json_overflow_to_infinity_rejected():
    # Review-followup fix 2: ``1e999`` overflows to ``inf`` through json's default
    # float path WITHOUT invoking parse_constant, so the old NaN/Infinity guard
    # (parse_constant only) let it through. The strict loader must reject any
    # non-finite number too.
    r = ToolCallJSONValid().verify(
        ResponseView(tool_calls=[{"name": "x", "arguments": '{"v": 1e999}'}]))
    assert not r.passed and r.score == 0.0
    assert "1e999" in r.reason


# --------------------------------------------------------------------------- #
# CodeParses
# --------------------------------------------------------------------------- #
def test_code_parses_pass_python_and_json():
    text = (
        "Here is code:\n"
        "```python\ndef f(x):\n    return x + 1\n```\n"
        "and config:\n"
        "```json\n{\"a\": [1, 2, 3]}\n```\n"
    )
    r = CodeParses().verify(ResponseView(text=text))
    assert r.passed and r.score == 1.0


def test_code_parses_pass_unknown_lang_balanced():
    # A language we cannot cheaply parse: we cannot prove it is broken -> pass.
    r = CodeParses().verify(ResponseView(text="```rust\nfn main() { foo(bar()); }\n```"))
    assert r.passed


def test_code_parses_fail_python_syntax_error():
    r = CodeParses().verify(ResponseView(text="```python\ndef f(:\n    pass\n```"))
    assert not r.passed and r.score == 0.0
    assert "python" in r.reason


def test_code_parses_pass_unknown_lang_unbalanced():
    # Review fix #4: we MUST NOT hard-fail a language we cannot parse just because
    # a naive brace-counter trips. Unbalanced-looking rust is unprovable -> pass.
    r = CodeParses().verify(ResponseView(text="```rust\nfn main() { foo(bar(); }\n```"))
    assert r.passed


def test_code_parses_pass_brace_in_string_or_comment():
    # Review fix #4: a brace inside a string/comment in a non-python/json language
    # is valid output; the old balanced-delimiter gate false-positived on these.
    for text in (
        '```bash\necho "}"\n```',
        '```js\nconst x = "{";\n```',
        '```c\n// }\nint main(void) { return 0; }\n```',
    ):
        r = CodeParses().verify(ResponseView(text=text))
        assert r.passed, text


def test_code_parses_pass_no_blocks():
    r = CodeParses().verify(ResponseView(text="just prose, no fences"))
    assert r.passed and r.score == 1.0


def test_code_parses_inline_backtick_in_string_passes():
    # Review fix #8: a triple-backtick inside a string in the body must not close
    # the fence early (which would truncate to an unterminated string and fail).
    text = '```python\nx = "```"\ny = 1\n```'
    r = CodeParses().verify(ResponseView(text=text))
    assert r.passed and r.score == 1.0


def test_code_parses_single_line_fence_is_evaluated():
    # Review fix #9: single-line fenced payloads (no newline after the lang tag)
    # must be captured and actually parsed — valid passes, invalid fails.
    ok = CodeParses().verify(ResponseView(text='```json {"a": 1}```'))
    assert ok.passed and ok.score == 1.0
    bad = CodeParses().verify(ResponseView(text='```json {"a": }```'))
    assert not bad.passed and "json" in bad.reason


def test_code_parses_fail_nan_json():
    # Review fix #11: non-spec JSON constants (NaN/Infinity) must be rejected.
    r = CodeParses().verify(ResponseView(text='```json\n{"x": NaN}\n```'))
    assert not r.passed
    assert "json" in r.reason


def test_code_parses_deeply_nested_json_fails_no_crash():
    # Review fix #2: deeply-nested JSON raises RecursionError in json.loads; the
    # json branch must catch it as a fail rather than crash.
    deep = "[" * 100000 + "]" * 100000
    r = CodeParses().verify(ResponseView(text="```json\n" + deep + "\n```"))
    assert not r.passed and r.score == 0.0


def test_code_parses_json_overflow_to_infinity_rejected():
    # Review-followup fix 2: numeric overflow (``1e999`` -> inf) must be rejected
    # by the json code-block branch too, not just the literal NaN/Infinity tokens.
    r = CodeParses().verify(ResponseView(text='```json\n{"v": 1e999}\n```'))
    assert not r.passed
    assert "json" in r.reason


def test_code_parses_fail_unterminated_fence():
    # Review-followup fix 5: a fence opened but never closed (a response truncated
    # mid-code-block, finish_reason unknown) used to read as "no fenced code
    # blocks" and PASS, slipping the truncation past the whole chain. The dangling
    # opener must now fail.
    r = CodeParses().verify(
        ResponseView(text="```python\ndef f(:\n    x = ", finish_reason=None))
    assert not r.passed and r.score == 0.0
    assert "unterminated" in r.reason


def test_code_parses_complete_blocks_not_flagged_unterminated():
    # The unterminated-fence check (fix 5) must NOT false-positive on genuinely
    # complete blocks, including an inline ``` inside a string body, a self-closing
    # single-line fence, and an inline ``` mention in prose.
    for text in (
        "```python\nx = 1\n```",
        '```python\nx = "```"\ny = 1\n```',   # inline ``` inside a string body
        '```json {"a": 1}```',                # single-line, self-closing
        "use ``` to open a code block",       # inline mention, not a line-start opener
    ):
        r = CodeParses().verify(ResponseView(text=text))
        assert r.passed, text


def test_code_parses_oversized_text_skipped_not_scanned():
    # Review-followup fix 1 (defense in depth): text beyond MAX_SCAN_BYTES is not
    # structurally scanned (a DoS cap on the inline hot path); the structural
    # check passes (it cannot cheaply prove a defect on a huge blob) while the
    # cheap finish_reason/empty checks still apply elsewhere.
    big = "x" * (MAX_SCAN_BYTES + 1)
    r = CodeParses().verify(ResponseView(text=big))
    assert r.passed and "too large" in r.reason


# --------------------------------------------------------------------------- #
# DiffWellFormed
# --------------------------------------------------------------------------- #
_GOOD_DIFF = (
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,3 +1,4 @@\n"
    " import os\n"
    "-x = 1\n"
    "+x = 2\n"
    "+y = 3\n"
)


def test_diff_well_formed_pass_plain_markers():
    r = DiffWellFormed().verify(ResponseView(text=_GOOD_DIFF))
    assert r.passed and r.score == 1.0


def test_diff_well_formed_pass_fenced():
    r = DiffWellFormed().verify(ResponseView(text="```diff\n" + _GOOD_DIFF + "```"))
    assert r.passed


def test_diff_well_formed_pass_no_diff():
    r = DiffWellFormed().verify(ResponseView(text="no diff here, just words"))
    assert r.passed and "no diff" in r.reason


def test_diff_well_formed_pass_prose_with_dashes():
    # Review fix #5: prose that merely starts with '--- '/'+++ ' (section
    # dividers, changelog lines) is NOT a diff and must not be hard-failed.
    for prose in (
        "--- Section: Changes ---\n+++ Added feature X\nSome prose here.\n",
        "Release notes\n--- highlights ---\n- did a thing\n- did another\n",
    ):
        r = DiffWellFormed().verify(ResponseView(text=prose))
        assert r.passed, prose
        assert "no diff" in r.reason


def test_diff_well_formed_fail_bad_hunk_header():
    # A ```diff fence is an explicit "this is a diff" claim, so a malformed hunk
    # header inside it is a provable defect (single trailing '@').
    bad = "```diff\n--- a/foo\n+++ b/foo\n@@ -1,3 +1,4 @\n context\n+added\n```"
    r = DiffWellFormed().verify(ResponseView(text=bad))
    assert not r.passed and r.score == 0.0
    assert "hunk header" in r.reason


def test_diff_well_formed_fail_bad_body_line():
    # A real hunk header makes this a diff; the un-prefixed body line is provably
    # malformed.
    bad = (
        "--- a/foo\n+++ b/foo\n@@ -1,2 +1,2 @@\n"
        " context line\n"
        "this line has no diff prefix\n"
    )
    r = DiffWellFormed().verify(ResponseView(text=bad))
    assert not r.passed
    assert "body line" in r.reason


def test_diff_well_formed_pass_diff_then_trailing_prose():
    # Review-followup fix 3: a valid hunk followed by a blank line and an
    # explanatory sentence (the extremely common "<diff>\n\n<explanation>" shape)
    # must PASS. The old code validated the WHOLE text and hard-failed any
    # un-prefixed line after the last hunk; now the hunk's declared line counts
    # bound the region and trailing prose is tolerated (as preamble already is).
    text = (
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
        "\n"
        "This explains the change.\n"
    )
    r = DiffWellFormed().verify(ResponseView(text=text))
    assert r.passed, r.reason


def test_diff_well_formed_fail_fence_without_hunk():
    # Review-followup fix 4: an explicit ```diff fence that contains no valid hunk
    # header is a diff claim we can disprove -> fail (the old code returned True
    # because with zero hunks every line was skipped as preamble).
    r = DiffWellFormed().verify(
        ResponseView(text="```diff\nthis is not a diff\njust garbage\n```"))
    assert not r.passed and r.score == 0.0
    assert "no valid hunk" in r.reason


def test_diff_well_formed_pass_fenced_diff_with_hunk():
    # Reconciles fix 3 + fix 4: a real fenced diff (a hunk IS present) still
    # passes — the zero-hunk fail must not catch a legitimate ```diff block.
    text = "```diff\n--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-a\n+b\n```"
    r = DiffWellFormed().verify(ResponseView(text=text))
    assert r.passed, r.reason


# --------------------------------------------------------------------------- #
# FormatWellFormed
# --------------------------------------------------------------------------- #
def test_format_well_formed_pass_bare_json():
    r = FormatWellFormed().verify(
        ResponseView(text='{"ok": true, "items": [1, 2]}', expected_format="json"))
    assert r.passed and r.score == 1.0


def test_format_well_formed_pass_fenced_json():
    r = FormatWellFormed().verify(
        ResponseView(text='```json\n{"ok": true}\n```', expected_format="json"))
    assert r.passed


def test_format_well_formed_pass_not_applicable():
    r = FormatWellFormed().verify(ResponseView(text="anything", expected_format=None))
    assert r.passed and "no expected_format" in r.reason


def test_format_well_formed_fail_invalid_json():
    r = FormatWellFormed().verify(
        ResponseView(text="Sure! Here you go: {not: valid}", expected_format="json"))
    assert not r.passed and r.score == 0.0
    assert "json" in r.reason


def test_format_well_formed_fail_sole_fence_with_trailing_prose():
    # Review fix #10: a fence followed by trailing prose (even prose ending in
    # backticks) is NOT the sole block, so the whole impure body is validated and
    # must fail — not just the inner JSON.
    text = "```json\n{}\n```\nasdf```"
    r = FormatWellFormed().verify(ResponseView(text=text, expected_format="json"))
    assert not r.passed and r.score == 0.0


def test_format_well_formed_fail_nan_json():
    # Review fix #11: NaN/Infinity must be rejected by the format gate too.
    r = FormatWellFormed().verify(
        ResponseView(text='{"x": Infinity}', expected_format="json"))
    assert not r.passed and "json" in r.reason


def test_format_well_formed_fail_overflow_to_infinity_json():
    # Review-followup fix 2: a numeric literal that overflows to inf (``1e999``)
    # bypasses parse_constant; the format gate must reject it as non-finite.
    r = FormatWellFormed().verify(
        ResponseView(text='{"x": 1e999}', expected_format="json"))
    assert not r.passed and "json" in r.reason


# --------------------------------------------------------------------------- #
# RefusalMarker (heuristic confidence signal — never a hard fail)
# --------------------------------------------------------------------------- #
def test_refusal_marker_clean_text_high_score():
    r = RefusalMarker().verify(ResponseView(text="Here is the function you asked for."))
    assert r.passed and r.score == 1.0


def test_refusal_marker_flags_refusal_without_failing():
    r = RefusalMarker().verify(ResponseView(text="I'm sorry, but I can't help with that."))
    assert r.passed                 # heuristic signal — does NOT hard-fail
    assert r.score < 1.0
    assert "refusal" in r.reason


def test_refusal_marker_only_matches_at_opening():
    # Review-followup fix 7: the score must drop only when the text OPENS with a
    # refusal phrase (per the docstring), not when one is merely referenced
    # mid-explanation. The old anywhere-substring match spuriously penalized a
    # normal answer that mentions a refusal phrase later in the body.
    mid = RefusalMarker().verify(ResponseView(
        text="Earlier I said I can't help with X, but here's exactly how to do it."))
    assert mid.passed and mid.score == 1.0
    assert "no refusal marker" in mid.reason
    # A refusal at the very opening (after leading whitespace) still lowers it.
    opening = RefusalMarker().verify(ResponseView(text="  I can't help with that request."))
    assert opening.passed and opening.score < 1.0
    assert "refusal" in opening.reason


# --------------------------------------------------------------------------- #
# chain runner + aggregate
# --------------------------------------------------------------------------- #
def test_run_verifiers_all_collects_every_result():
    good = ResponseView(text="ok", finish_reason="stop")
    results = run_verifiers(good, default_verifiers(), mode="all")
    assert len(results) == len(default_verifiers())
    assert all_passed(results)
    agg = aggregate(results)
    assert agg.passed and agg.score == 1.0


def test_run_verifiers_mixed_chain_fails_aggregate():
    # Passes NonEmptyContent but fails NotTruncated (truncated) and CodeParses.
    bad = ResponseView(
        text="```python\ndef f(:\n```",
        finish_reason="length",
    )
    results = run_verifiers(bad, default_verifiers(), mode="all")
    assert not all_passed(results)
    agg = aggregate(results)
    assert not agg.passed
    # The first failing check in run order is NotTruncated.
    assert agg.reason.startswith("not_truncated")
    # At least the two structural failures are present.
    failed = {r.verifier for r in results if not r.passed}
    assert {"not_truncated", "code_parses"} <= failed


def test_run_verifiers_first_fail_short_circuits():
    bad = ResponseView(text="", finish_reason="length")  # empty -> first verifier fails
    results = run_verifiers(bad, default_verifiers(), mode="first_fail")
    assert len(results) == 1
    assert results[0].verifier == "non_empty_content"
    assert not results[0].passed


def test_run_verifiers_rejects_unknown_mode():
    with pytest.raises(ValueError):
        run_verifiers(ResponseView(text="x"), default_verifiers(), mode="bogus")


class _BoomVerifier:
    name = "boom"

    def verify(self, response):
        raise RuntimeError("kaboom")


def test_run_verifiers_backstops_a_crashing_verifier():
    # Review fix #3: ANY exception a verifier raises is backstopped into a fail
    # verdict so the chain still returns one result per verifier — the contract.
    results = run_verifiers(ResponseView(text="x"), [_BoomVerifier(), NonEmptyContent()])
    assert len(results) == 2
    assert results[0].verifier == "boom"
    assert not results[0].passed and results[0].score == 0.0
    assert "verifier error: RuntimeError" in results[0].reason
    assert results[1].passed  # the chain continued past the crash


def test_chain_never_raises_on_adversarial_input():
    # The whole chain must return results (never raise) on hostile inputs:
    # non-dict tool calls and pathological nesting.
    deep = "[" * 100000 + "]" * 100000
    adversarial = [
        ResponseView(tool_calls=[None, "foo", 5]),
        ResponseView(tool_calls=[{"name": "x", "arguments": deep}]),
        ResponseView(text="```json\n" + deep + "\n```"),
        ResponseView(text='{"x": NaN}', expected_format="json"),
    ]
    for fx in adversarial:
        results = run_verifiers(fx, default_verifiers(), mode="all")
        assert len(results) == len(default_verifiers())


def test_fence_regex_no_redos_on_pathological_input():
    # Review-followup fix 1 (starred): an opening fence followed by a long
    # lang-tag-like run with no closing fence used to make _FENCE_RE backtrack
    # O(n^2) — seconds to parse 40-60 KB, and the default chain re-scans the same
    # text several times, stalling the "near-zero-cost" inline serving hot path.
    # The bounded lang tag makes the scan linear. This must complete quickly and
    # not hang. (Revert the {0,40} bound and a single scan alone takes ~5s, the
    # full chain ~15s — well over the bound below.)
    patho = ResponseView(text="```" + "a" * 40000, finish_reason="length")
    start = time.perf_counter()
    results = run_verifiers(patho, default_verifiers(), mode="all")
    elapsed = time.perf_counter() - start
    assert len(results) == len(default_verifiers())
    assert elapsed < 1.0, f"fence scan took {elapsed:.2f}s — quadratic backtracking regression?"


def test_checks_return_fail_verdict_on_non_str_text():
    # Review-followup fix 6: a non-str text (e.g. bytes) used to raise TypeError
    # directly inside a text-consuming check (only run_verifiers backstopped it),
    # breaking the per-check "adversarial input yields a fail verdict, never an
    # exception" contract for direct callers. Each must now return a fail verdict.
    for cls in (NonEmptyContent, CodeParses, DiffWellFormed, FormatWellFormed, RefusalMarker):
        r = cls().verify(ResponseView(text=b"```x code"))
        assert not r.passed, cls.__name__
        assert "non-string text" in r.reason, cls.__name__
    # A non-str finish_reason must likewise not crash NotTruncated (an unknown
    # reason reads as a clean stop).
    assert NotTruncated().verify(ResponseView(text="x", finish_reason=5)).passed
    # And the whole chain stays exception-free with non-str text + finish_reason.
    results = run_verifiers(
        ResponseView(text=b"```x", finish_reason=b"length"),
        default_verifiers(), mode="all")
    assert len(results) == len(default_verifiers())


def test_aggregate_min_score_without_flipping_pass():
    # A passing-but-low RefusalMarker pulls aggregate score down, not pass/fail.
    r = ResponseView(text="I'm unable to do that, but here is an idea.", finish_reason="stop")
    results = run_verifiers(r, default_verifiers(), mode="all")
    agg = aggregate(results)
    assert agg.passed            # refusal marker never hard-fails
    assert agg.score < 1.0       # but it lowers the aggregate score


# --------------------------------------------------------------------------- #
# acceptance criterion 2: the verify path makes NO network/LLM call
# --------------------------------------------------------------------------- #
def _forbid_network(monkeypatch):
    """Replace every network surface with something that raises if touched.

    If any verifier tried to open a socket, make an HTTP connection, or fetch a
    URL (e.g. to call an LLM judge), construction/use of these would raise and
    the test would fail. That the chain instead returns results proves the verify
    path is purely local/structural — acceptance criterion 2.
    """
    def boom(*_a, **_k):
        raise AssertionError("verify path attempted a network/LLM call")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(http.client, "HTTPConnection", boom)
    monkeypatch.setattr(http.client, "HTTPSConnection", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def test_verify_path_makes_no_network_call(monkeypatch):
    _forbid_network(monkeypatch)

    # A representative spread of fixtures exercising every check (pass and fail).
    fixtures = [
        ResponseView(text="hello", finish_reason="stop"),
        ResponseView(text="", finish_reason="stop"),
        ResponseView(text="x", finish_reason="length"),
        ResponseView(tool_calls=[{"name": "w", "arguments": '{"a":'}]),
        ResponseView(text="```python\ndef f(:\n```"),
        ResponseView(text="```json\n{\"a\": 1}\n```"),
        ResponseView(text=_GOOD_DIFF),
        ResponseView(text="--- a\n+++ b\n@@ bad @@\n nope\n"),
        ResponseView(text='{"k": 1}', expected_format="json"),
        ResponseView(text="not json", expected_format="json"),
        ResponseView(text="I cannot help with that request."),
        # adversarial inputs must also stay purely-local (no crash, no I/O):
        ResponseView(tool_calls=[None, "foo", 5]),
        ResponseView(tool_calls=[{"name": "w", "arguments": {"already": "parsed"}}]),
        ResponseView(text="--- Section ---\n+++ prose +++\n"),
    ]

    produced = 0
    for fx in fixtures:
        results = run_verifiers(fx, default_verifiers(), mode="all")
        assert results, "verifier chain produced no results"
        # Every result is a well-formed VerifyResult with a sane score.
        for res in results:
            assert isinstance(res, VerifyResult)
            assert 0.0 <= res.score <= 1.0
        produced += len(results)

    # Sanity: we really ran the whole chain over every fixture.
    assert produced == len(fixtures) * len(default_verifiers())


def test_no_network_guard_actually_bites(monkeypatch):
    # Guards against the guard being a no-op: with network forbidden, really
    # opening a socket must raise. (If this passed silently, the criterion-2 test
    # above would be vacuous.)
    _forbid_network(monkeypatch)
    with pytest.raises(AssertionError):
        socket.socket()
