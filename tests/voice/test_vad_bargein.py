"""Tests for anvil_serving.voice.stages.vad -- turn lifecycle + barge-in.

Dependency-light: no torch/onnxruntime, no real audio. Drives
``VADStage.process()`` directly (frame-by-frame, no background thread) with
the deterministic ``FakeVADModel`` so the silence-threshold / barge-in state
machine is exercised precisely and without timing flakiness.
"""
from __future__ import annotations


from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.stages.vad import SpeechEvent, VADConfig, VADStage

SPEECH = b"\x01\x02\x03\x04"
SILENCE = b"\x00\x00\x00\x00"


def _stage(**config_kwargs):
    scope = CancelScope()
    config = VADConfig(frame_ms=50, silence_ms=200, **config_kwargs)  # 4 silent frames end a turn
    stage = VADStage(in_queue=None, cancel_scope=scope, config=config)
    return stage, scope


# --------------------------------------------------------------------------- #
# VADConfig validation
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# FakeVADModel
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# turn lifecycle: speech onset -> silence threshold -> VADAudio(is_final=True)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# barge-in: a new speech onset while `responding` is True cancels the scope
# --------------------------------------------------------------------------- #
def test_barge_in_bumps_cancel_scope_generation():
    stage, scope = _stage()
    stage.process(SPEECH)
    for _ in range(4):
        result = stage.process(SILENCE)  # ends the turn; stage.responding -> True
    old_generation = result[-1].generation
    assert stage.responding is True

    # New speech arrives while the (fake) response is still in flight.
    events = stage.process(SPEECH)
    started = [e for e in events if isinstance(e, SpeechEvent)]
    assert [e.kind for e in started] == ["started"]
    assert started[0].barge_in is True

    new_generation = scope.current()
    assert new_generation > old_generation
    assert scope.is_stale(old_generation) is True
    assert scope.discarding is True
