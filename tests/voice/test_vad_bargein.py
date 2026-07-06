"""Tests for anvil_serving.voice.stages.vad -- turn lifecycle + barge-in.

Dependency-light: no torch/onnxruntime, no real audio. Drives
``VADStage.process()`` directly (frame-by-frame, no background thread) with
the deterministic ``FakeVADModel`` so the silence-threshold / barge-in state
machine is exercised precisely and without timing flakiness.
"""
from __future__ import annotations

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import VADAudio
from anvil_serving.voice.stages.vad import FakeVADModel, SpeechEvent, VADConfig, VADStage

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
def test_silence_ms_out_of_band_rejected():
    with pytest.raises(ValueError):
        VADConfig(silence_ms=100)
    with pytest.raises(ValueError):
        VADConfig(silence_ms=300)


def test_silence_ms_band_accepted():
    VADConfig(silence_ms=150)
    VADConfig(silence_ms=250)


def test_silence_frames_computed_from_ms():
    assert VADConfig(frame_ms=50, silence_ms=200).silence_frames == 4
    assert VADConfig(frame_ms=20, silence_ms=200).silence_frames == 10


# --------------------------------------------------------------------------- #
# FakeVADModel
# --------------------------------------------------------------------------- #
def test_fake_vad_model_speech_vs_silence():
    model = FakeVADModel()
    assert model.is_speech(SPEECH) is True
    assert model.is_speech(SILENCE) is False
    assert model.is_speech(b"") is False


# --------------------------------------------------------------------------- #
# turn lifecycle: speech onset -> silence threshold -> VADAudio(is_final=True)
# --------------------------------------------------------------------------- #
def test_speech_onset_emits_started_event_no_audio_yet():
    stage, _scope = _stage()
    events = stage.process(SPEECH)
    assert events is not None
    started = [e for e in events if isinstance(e, SpeechEvent)]
    assert [e.kind for e in started] == ["started"]
    assert started[0].barge_in is False
    assert started[0].detected_monotonic_s > 0
    assert not any(isinstance(e, VADAudio) for e in events)


def test_silence_before_speech_is_a_noop():
    stage, _scope = _stage()
    assert stage.process(SILENCE) is None


def test_turn_ends_after_silence_threshold_frames():
    stage, _scope = _stage()
    assert stage.process(SPEECH) is not None  # started
    assert stage.process(SPEECH) is None      # still buffering speech
    assert stage.process(SILENCE) is None      # 1 silent frame: not enough yet
    assert stage.process(SILENCE) is None      # 2
    assert stage.process(SILENCE) is None      # 3
    result = stage.process(SILENCE)            # 4th -> threshold hit, turn ends
    assert result is not None
    vad_audio = [m for m in result if isinstance(m, VADAudio)]
    stopped = [m for m in result if isinstance(m, SpeechEvent) and m.kind == "stopped"]
    assert len(vad_audio) == 1
    assert len(stopped) == 1
    assert vad_audio[0].is_final is True
    assert vad_audio[0].pcm == SPEECH + SPEECH  # both buffered speech frames


def test_turn_id_is_stable_across_one_turn_and_changes_next_turn():
    stage, _scope = _stage()
    stage.process(SPEECH)
    for _ in range(4):
        result = stage.process(SILENCE)
    turn1 = result[-1].turn_id

    # A brand-new turn (no barge-in: stage.responding was left False by the
    # test harness calling process() directly rather than via a full
    # pipeline) gets a fresh turn_id.
    stage.responding = False
    stage.process(SPEECH)
    for _ in range(4):
        result2 = stage.process(SILENCE)
    turn2 = result2[-1].turn_id
    assert turn1 != turn2


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


def test_no_barge_in_when_not_responding_uses_begin_new_generation():
    stage, scope = _stage()
    stage.process(SPEECH)
    for _ in range(4):
        stage.process(SILENCE)
    stage.responding = False  # simulate: caller already finished playing the reply

    gen_before = scope.current()
    stage.process(SPEECH)
    assert scope.current() == gen_before + 1
    assert scope.discarding is False  # NOT a barge-in: begin_new_generation() was used


def test_downstream_output_tagged_with_old_generation_is_recognized_stale():
    # Simulates what a downstream stage (LLM/TTS) checks: a message it holds,
    # tagged with the generation active when it was produced, becomes stale
    # the instant a barge-in bumps the shared CancelScope.
    stage, scope = _stage()
    stage.process(SPEECH)
    for _ in range(4):
        result = stage.process(SILENCE)
    in_flight_generation = result[-1].generation
    assert scope.is_stale(in_flight_generation) is False  # still current

    stage.process(SPEECH)  # barge-in
    assert scope.is_stale(in_flight_generation) is True  # now superseded
