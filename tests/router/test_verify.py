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
import urllib.request

import pytest

from anvil_serving.router.verify import (
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
    # A language we cannot cheaply parse: only the structural balance check runs.
    r = CodeParses().verify(ResponseView(text="```rust\nfn main() { foo(bar()); }\n```"))
    assert r.passed


def test_code_parses_fail_python_syntax_error():
    r = CodeParses().verify(ResponseView(text="```python\ndef f(:\n    pass\n```"))
    assert not r.passed and r.score == 0.0
    assert "python" in r.reason


def test_code_parses_fail_unknown_lang_unbalanced():
    r = CodeParses().verify(ResponseView(text="```rust\nfn main() { foo(bar(); }\n```"))
    assert not r.passed
    assert "unbalanced" in r.reason


def test_code_parses_pass_no_blocks():
    r = CodeParses().verify(ResponseView(text="just prose, no fences"))
    assert r.passed and r.score == 1.0


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


def test_diff_well_formed_fail_bad_hunk_header():
    bad = "--- a/foo\n+++ b/foo\n@@ -1,3 +1,4 @\n context\n+added\n"  # single trailing @
    r = DiffWellFormed().verify(ResponseView(text=bad))
    assert not r.passed and r.score == 0.0
    assert "hunk header" in r.reason


def test_diff_well_formed_fail_bad_body_line():
    bad = (
        "--- a/foo\n+++ b/foo\n@@ -1,2 +1,2 @@\n"
        " context line\n"
        "this line has no diff prefix\n"
    )
    r = DiffWellFormed().verify(ResponseView(text=bad))
    assert not r.passed
    assert "body line" in r.reason


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
