"""Tests for `BaseStage`'s start/stop thread-loop skeleton
(``anvil_serving.voice.stages.base``). Dependency-light: stdlib
``queue``/``threading`` only, no real audio/network/GPU.
"""
from __future__ import annotations

import queue
import time

import pytest

from anvil_serving.voice.messages import GenerateRequest
from anvil_serving.voice.stages.base import BaseStage


class _PassThroughStage(BaseStage):
    """Minimal concrete stage: forwards whatever it receives unchanged."""

    name = "pass-through"

    def process(self, item):
        return item


class _TimedGeneratorStage(BaseStage):
    name = "llm"

    def process(self, item):
        yield "first"
        yield "second"


class _RaisingTimedStage(BaseStage):
    name = "llm"

    def process(self, item):
        raise RuntimeError("boom")


def _drain(q: "queue.Queue", *, timeout: float = 2.0):
    return q.get(timeout=timeout)


def _wait_for_log(caplog, marker: str, *, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker in caplog.text:
            return
        time.sleep(0.02)
    pytest.fail("timed out waiting for log marker: %s\n%s" % (marker, caplog.text))


def test_start_is_idempotent_while_the_thread_is_still_alive():
    in_q: "queue.Queue" = queue.Queue()
    stage = _PassThroughStage(in_q, [])
    stage.start()
    running_thread = stage._thread
    stage.start()  # must be a no-op: same thread object, no crash
    assert stage._thread is running_thread
    stage.stop(join_timeout=2.0)


def test_core_stage_timing_log_redacts_text(caplog):
    caplog.set_level("WARNING", logger="anvil_serving.voice.stages.base")
    in_q: "queue.Queue" = queue.Queue()
    out_q: "queue.Queue" = queue.Queue()
    stage = _TimedGeneratorStage(in_q, [out_q])
    message = GenerateRequest(
        turn_id="turn-secret",
        turn_revision=0,
        generation=7,
        text="secret prompt text",
    )

    stage.start()
    try:
        in_q.put(message)
        assert _drain(out_q) == "first"
        assert _drain(out_q) == "second"
        _wait_for_log(caplog, "voice_stage_timing stage=llm")
    finally:
        stage.stop(join_timeout=2.0)

    assert "secret prompt text" not in caplog.text
    assert "turn_id=turn-secret" in caplog.text
    assert "text_chars=18" in caplog.text
    assert "output_count=2" in caplog.text
    assert "error=false" in caplog.text


def test_core_stage_exception_log_redacts_text(caplog):
    caplog.set_level("WARNING", logger="anvil_serving.voice.stages.base")
    in_q: "queue.Queue" = queue.Queue()
    stage = _RaisingTimedStage(in_q, [])
    message = GenerateRequest(
        turn_id="turn-err",
        turn_revision=0,
        generation=9,
        text="do not log this prompt",
    )

    stage.start()
    try:
        in_q.put(message)
        _wait_for_log(caplog, "process() raised on input_type=GenerateRequest")
    finally:
        stage.stop(join_timeout=2.0)

    assert "do not log this prompt" not in caplog.text
    assert "turn_id=turn-err" in caplog.text
    assert "error=true" in caplog.text
