"""Unit tests for the T008 streaming commit-window.

The contract under test (the reason this module exists):

    On a local verify-failure, ZERO partial local tokens reach the harness.

The cases below pin that from several angles: a forced verify-failure delivers a
cloud-served response containing **no** local substring (acceptance criterion 1);
a non-fail-prone class is streamed through unchanged with the window skipped even
when its output would fail verify (acceptance criterion 2); the verify-PASS path
commits the buffered local response in order; the fallback hook fires; passthrough
streams lazily and never runs verify; and the ``max_buffer_bytes`` guard falls
back. ``test_first_yielded_token_on_fallback_is_cloud`` is the airtight ordering
pin: the very first delta out is already a cloud token, proving no local delta can
precede the commit decision.

Hermetic and stdlib-only (pytest is the only test dep).
"""

from __future__ import annotations

from typing import Iterator, List

import pytest

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.commit_window import (
    FallbackEvent,
    build_response_view,
    stream_with_commit_window,
)
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.verify import ResponseView, VerifyResult


FAIL_PRONE = {"fail-prone"}


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def make_request(text: str = "do the thing") -> InternalRequest:
    return InternalRequest(model="local-test", messages=[Message("user", text)])


class AlwaysFail:
    """A verifier that hard-fails on any response — forces the fallback path
    deterministically, independent of the default-verifier internals."""

    name = "always_fail"

    def verify(self, response: ResponseView) -> VerifyResult:
        return VerifyResult(self.name, False, 0.0, "forced failure")


class AlwaysPass:
    name = "always_pass"

    def verify(self, response: ResponseView) -> VerifyResult:
        return VerifyResult(self.name, True, 1.0, "forced pass")


class Boom:
    """A verifier whose ``verify`` must never be called in passthrough — if it
    is, the test crashes loudly (proving the window ran when it must not have)."""

    name = "boom"

    def verify(self, response: ResponseView) -> VerifyResult:
        raise AssertionError("verify must not run on a non-fail-prone class")


class RecordingBackend:
    """Records each delta at the moment it is *produced*, so a test can observe
    how far the underlying generator has been driven (laziness / no buffering)."""

    def __init__(self, tokens: List[str]):
        self.tokens = list(tokens)
        self.produced: List[str] = []

    def generate(self, request: InternalRequest) -> Iterator[str]:
        for t in self.tokens:
            self.produced.append(t)
            yield t


def collect(it: Iterator[str]) -> str:
    return "".join(it)


# --------------------------------------------------------------------------- #
# acceptance criterion 2 — non-fail-prone class streams normally (window skipped)
# --------------------------------------------------------------------------- #
def test_non_fail_prone_streams_unchanged_window_skipped():
    # A class NOT in fail_prone_classes must stream straight through even when
    # its output WOULD fail verify. We pass AlwaysFail to prove the window is
    # skipped: if verify ran, this would fall back to cloud; it must not.
    local = StaticBackend(["alpha ", "beta ", "gamma"])
    cloud = StaticBackend(["CLOUD"])
    out = collect(
        stream_with_commit_window(
            make_request(),
            work_class="safe",  # not fail-prone
            local_backend=local,
            fallback_backend=cloud,
            fail_prone_classes=FAIL_PRONE,
            verifiers=[AlwaysFail()],
        )
    )
    assert out == "alpha beta gamma"      # streamed unchanged
    assert "CLOUD" not in out             # no fallback


def test_passthrough_never_runs_verify():
    # The Boom verifier raises if called; passthrough must not call verify.
    local = StaticBackend(["x", "y"])
    out = collect(
        stream_with_commit_window(
            make_request(),
            work_class="safe",
            local_backend=local,
            fallback_backend=StaticBackend(["CLOUD"]),
            fail_prone_classes=FAIL_PRONE,
            verifiers=[Boom()],
        )
    )
    assert out == "xy"


def test_passthrough_streams_lazily_does_not_buffer():
    # Pull exactly one delta from the passthrough stream and assert the
    # underlying backend has produced exactly one token so far — i.e. the window
    # did not drain/buffer the whole response (which the fail-prone path would).
    local = RecordingBackend(["one ", "two ", "three ", "four"])
    stream = stream_with_commit_window(
        make_request(),
        work_class="safe",
        local_backend=local,
        fallback_backend=StaticBackend(["CLOUD"]),
        fail_prone_classes=FAIL_PRONE,
    )
    first = next(stream)
    assert first == "one "
    assert local.produced == ["one "]  # lazy: only one token produced, not buffered
    # draining the rest still yields the remainder in order
    assert first + collect(stream) == "one two three four"


# --------------------------------------------------------------------------- #
# acceptance criterion 1 — fail-prone verify-FAIL: cloud only, no partial local
# --------------------------------------------------------------------------- #
def test_fail_prone_verify_fail_streams_cloud_no_partial_local_tokens():
    # Injected verifier fails on the local text; assert the delivered output is
    # EXACTLY the cloud deltas and that NO local substring leaks anywhere.
    local = StaticBackend(["LOCAL-1 ", "LOCAL-2 ", "LOCAL-3"])
    cloud = StaticBackend(["CLOUD-A ", "CLOUD-B"])
    out = collect(
        stream_with_commit_window(
            make_request(),
            work_class="fail-prone",
            local_backend=local,
            fallback_backend=cloud,
            fail_prone_classes=FAIL_PRONE,
            verifiers=[AlwaysFail()],
        )
    )
    assert out == "CLOUD-A CLOUD-B"     # full output equals the cloud deltas
    assert "LOCAL" not in out           # not one local token leaked


def test_fail_prone_genuine_default_verifier_failure_falls_back():
    # No injected verifier: local output genuinely fails a *default* verifier.
    # A broken python fenced block fails CodeParses (ast.parse raises).
    local = StaticBackend(["```python\n", "def broken(\n", "```"])
    cloud = StaticBackend(["recovered ", "answer"])
    out = collect(
        stream_with_commit_window(
            make_request(),
            work_class="fail-prone",
            local_backend=local,
            fallback_backend=cloud,
            fail_prone_classes=FAIL_PRONE,
        )
    )
    assert out == "recovered answer"
    for leaked in ("broken", "def", "python", "```"):
        assert leaked not in out


def test_first_yielded_token_on_fallback_is_cloud():
    # Airtight ordering pin: even when consuming one delta at a time, the FIRST
    # delta out of a failing fail-prone window is already a cloud token. This is
    # only possible if the whole local response was buffered and rejected before
    # anything was yielded — no local delta can structurally precede the commit
    # decision.
    poison = "POISON_LOCAL_TOKEN"
    local = StaticBackend([poison, " more local"])
    cloud = StaticBackend(["CLOUD_FIRST", " then more"])
    stream = stream_with_commit_window(
        make_request(),
        work_class="fail-prone",
        local_backend=local,
        fallback_backend=cloud,
        fail_prone_classes=FAIL_PRONE,
        verifiers=[AlwaysFail()],
    )
    first = next(stream)
    assert first == "CLOUD_FIRST"
    assert poison not in (first + collect(stream))


# --------------------------------------------------------------------------- #
# fail-prone verify-PASS: commit and replay the buffered local response in order
# --------------------------------------------------------------------------- #
def test_fail_prone_verify_pass_commits_buffered_local_in_order():
    deltas = ["Hello", " ", "world", " ", "ok"]
    local = StaticBackend(deltas)
    cloud = StaticBackend(["SHOULD-NOT-APPEAR"])
    pieces = list(
        stream_with_commit_window(
            make_request(),
            work_class="fail-prone",
            local_backend=local,
            fallback_backend=cloud,
            fail_prone_classes=FAIL_PRONE,
        )
    )
    # The buffered local deltas are committed and replayed in exact order.
    assert pieces == deltas
    assert "SHOULD-NOT-APPEAR" not in "".join(pieces)


def test_fail_prone_verify_pass_with_injected_passing_verifier():
    local = StaticBackend(["ok ", "good"])
    cloud = StaticBackend(["NOPE"])
    out = collect(
        stream_with_commit_window(
            make_request(),
            work_class="fail-prone",
            local_backend=local,
            fallback_backend=cloud,
            fail_prone_classes=FAIL_PRONE,
            verifiers=[AlwaysPass()],
        )
    )
    assert out == "ok good"


# --------------------------------------------------------------------------- #
# fallback logging hook
# --------------------------------------------------------------------------- #
def test_on_fallback_hook_fires_with_event_on_failure():
    events: List[FallbackEvent] = []
    local = StaticBackend(["bad local ", "output"])
    cloud = StaticBackend(["cloud out"])
    out = collect(
        stream_with_commit_window(
            make_request(),
            work_class="fail-prone",
            local_backend=local,
            fallback_backend=cloud,
            fail_prone_classes=FAIL_PRONE,
            verifiers=[AlwaysFail()],
            on_fallback=events.append,
        )
    )
    assert out == "cloud out"
    assert len(events) == 1
    ev = events[0]
    assert ev.work_class == "fail-prone"
    assert ev.overflowed is False
    assert ev.buffered_text == "bad local output"   # the discarded local text
    assert any(not r.passed for r in ev.verify_results)


def test_on_fallback_hook_not_called_on_pass_or_passthrough():
    events: List[FallbackEvent] = []
    # PASS path
    collect(
        stream_with_commit_window(
            make_request(),
            work_class="fail-prone",
            local_backend=StaticBackend(["fine"]),
            fallback_backend=StaticBackend(["X"]),
            fail_prone_classes=FAIL_PRONE,
            verifiers=[AlwaysPass()],
            on_fallback=events.append,
        )
    )
    # passthrough path
    collect(
        stream_with_commit_window(
            make_request(),
            work_class="safe",
            local_backend=StaticBackend(["fine"]),
            fallback_backend=StaticBackend(["X"]),
            fail_prone_classes=FAIL_PRONE,
            on_fallback=events.append,
        )
    )
    assert events == []


# --------------------------------------------------------------------------- #
# max_buffer_bytes guard — overflow is treated as verify-fail -> fallback
# --------------------------------------------------------------------------- #
def test_max_buffer_bytes_overflow_falls_back_no_local_tokens():
    events: List[FallbackEvent] = []
    # Local emits more bytes than the cap -> overflow -> fallback (cloud only),
    # with no local token delivered and no verifier run.
    local = StaticBackend(["AAAA", "BBBB", "CCCC"])  # 12 bytes total
    cloud = StaticBackend(["cloud"])
    out = collect(
        stream_with_commit_window(
            make_request(),
            work_class="fail-prone",
            local_backend=local,
            fallback_backend=cloud,
            fail_prone_classes=FAIL_PRONE,
            verifiers=[AlwaysPass()],   # would PASS — overflow must override it
            max_buffer_bytes=5,
            on_fallback=events.append,
        )
    )
    assert out == "cloud"
    for leaked in ("AAAA", "BBBB", "CCCC"):
        assert leaked not in out
    assert len(events) == 1
    assert events[0].overflowed is True
    assert events[0].verify_results == []  # buffer abandoned before verification


def test_max_buffer_bytes_under_cap_commits_normally():
    local = StaticBackend(["short"])  # 5 bytes
    out = collect(
        stream_with_commit_window(
            make_request(),
            work_class="fail-prone",
            local_backend=local,
            fallback_backend=StaticBackend(["NOPE"]),
            fail_prone_classes=FAIL_PRONE,
            verifiers=[AlwaysPass()],
            max_buffer_bytes=1024,
        )
    )
    assert out == "short"


# --------------------------------------------------------------------------- #
# small helper coverage
# --------------------------------------------------------------------------- #
def test_build_response_view_joins_buffer_losslessly():
    view = build_response_view(["a", "b", "c"], make_request())
    assert isinstance(view, ResponseView)
    assert view.text == "abc"
    assert view.tool_calls is None and view.finish_reason is None


def test_empty_classes_means_everything_passes_through():
    # With no fail-prone classes configured, nothing opens a commit window.
    local = StaticBackend(["a ", "b"])
    out = collect(
        stream_with_commit_window(
            make_request(),
            work_class="fail-prone",
            local_backend=local,
            fallback_backend=StaticBackend(["CLOUD"]),
            fail_prone_classes=set(),   # empty
            verifiers=[AlwaysFail()],   # would fail if the window were applied
        )
    )
    assert out == "a b"
