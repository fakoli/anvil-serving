"""RealtimeService: client events -> pipeline side effects -> server events
(``anvil_serving.voice.realtime.service``). Dependency-light: a fake LLM
``stream_fn``, no real HTTP/socket/audio hardware.
"""

from __future__ import annotations

import base64
import json
import queue
import urllib.error
from types import SimpleNamespace

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import (
    AudioOut,
    EndOfResponse,
)
from anvil_serving.voice.pipeline import VoicePipeline
from anvil_serving.voice.realtime.service import RealtimeService
from anvil_serving.voice.realtime_service import (
    ProxyProcessConfig,
    RealtimeProxyProcessService,
)
from anvil_serving.voice.stages.vad import VADConfig


def _fake_stream(text, config):
    yield "Sure, "
    yield "here you go."


def _make_service():
    pipeline = VoicePipeline(
        vad_config=VADConfig(frame_ms=50, silence_ms=200),
        llm_stream_fn=_fake_stream,
    )
    pipeline.start()
    sent = []
    service = RealtimeService(pipeline=pipeline, send_event=sent.append, session_id="s1")
    return pipeline, service, sent


def test_persistent_proxy_dry_run_uses_canonical_foreground_command(tmp_path):
    config = ProxyProcessConfig(
        config_path=str(tmp_path / "voice.toml"),
        topology_path=str(tmp_path / "topology.toml"),
        profile="mini-dark-audio-proxy",
        host="127.0.0.1",
        port=8765,
        owner="gateway-host",
        pid_file=str(tmp_path / "proxy.pid"),
        log_file=str(tmp_path / "proxy.log"),
    )

    def unavailable(*_args, **_kwargs):
        raise urllib.error.URLError("not running")

    result = RealtimeProxyProcessService(config, opener=unavailable).up(dry_run=True)

    assert result["returncode"] == 0
    assert result["owner"] == "gateway-host"
    command = result["command"]
    assert command[3:7] == ["voice", "proxy", "run", "--config"]
    assert "audio" not in command
    assert "--topology" in command


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf"), True])
def test_persistent_proxy_config_rejects_invalid_timeouts(tmp_path, timeout):
    with pytest.raises(ValueError, match="positive finite"):
        ProxyProcessConfig(
            config_path=str(tmp_path / "voice.toml"),
            topology_path=str(tmp_path / "topology.toml"),
            profile=None,
            host="127.0.0.1",
            port=8765,
            ready_timeout=timeout,
        )


def test_persistent_proxy_logs_are_bounded_and_typed(tmp_path):
    log_file = tmp_path / "proxy.log"
    log_file.write_text("one\ntwo\nthree\n", encoding="utf-8")
    service = RealtimeProxyProcessService(ProxyProcessConfig(
        config_path=str(tmp_path / "voice.toml"),
        topology_path=str(tmp_path / "topology.toml"),
        profile=None,
        host="127.0.0.1",
        port=8765,
        pid_file=str(tmp_path / "proxy.pid"),
        log_file=str(log_file),
    ))

    result = service.logs(tail=2)

    assert result["action"] == "logs"
    assert result["lines"] == ["two", "three"]
    assert result["max_bytes"] > 0


def test_response_create_applies_response_scoped_instructions_and_tools_without_model_override():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {"instructions": "Session prompt.", "model": "gpt-realtime"},
                }
            )
        )
        item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "weather"}],
        }
        service.handle_client_message(
            json.dumps({"type": "conversation.item.create", "item": item})
        )
        service.handle_client_message(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        "model": "another-realtime-model",
                        "instructions": "Response prompt.",
                        "tools": [{"type": "function", "name": "openclaw_agent_consult"}],
                        "tool_choice": "auto",
                    },
                }
            )
        )

        assert sent[-1]["type"] == "response.created"
        assert pipeline.llm.config.model == "llm.voice"
        assert pipeline.llm.config.system_prompt == "Response prompt."
        assert pipeline.llm.config.tools == [
            {"type": "function", "function": {"name": "openclaw_agent_consult"}}
        ]
        assert pipeline.llm.config.tool_choice == "auto"
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def _fake_output_pipeline():
    return SimpleNamespace(
        vad_events=queue.Queue(),
        transcript_events=queue.Queue(),
        audio_out=queue.Queue(),
        cancel_scope=CancelScope(),
    )


def test_audio_without_authoritative_text_fails_transcript_closed():
    fake_pipeline = _fake_output_pipeline()
    service = RealtimeService(pipeline=fake_pipeline, send_event=lambda _event: None, session_id="s1")
    service.state.session_config = {"output_modalities": ["audio", "text"]}
    created = service._begin_response("turn-1")
    fake_pipeline.audio_out.put(
        AudioOut(turn_id="turn-1", turn_revision=0, generation=0, pcm=b"\x01\x00")
    )
    fake_pipeline.audio_out.put(EndOfResponse(turn_id="turn-1", turn_revision=0, generation=0))

    events = service.drain_pipeline_events()

    assert [event["type"] for event in events] == [
        "response.output_audio.delta",
        "error",
        "response.done",
    ]
    assert events[1]["response_id"] == created["response"]["id"]
    assert not any(event["type"].endswith("transcript.done") for event in events)


def test_function_call_output_rejects_unmatched_call_id():
    llm = SimpleNamespace(submit_tool_result=lambda *_args, **_kwargs: False)
    fake_pipeline = SimpleNamespace(audio_in=queue.Queue(), llm=llm)
    sent = []
    service = RealtimeService(pipeline=fake_pipeline, send_event=sent.append, session_id="s1")

    service.handle_client_message(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": "stale_call",
                    "output": {"text": "Too late."},
                },
            }
        )
    )

    assert sent[-1]["type"] == "error"
    assert sent[-1]["error"]["type"] == "invalid_request"


def test_audio_turn_streams_assistant_transcript_when_session_requests_text():
    pipeline, service, _sent = _make_service()
    try:
        service.handle_client_message(
            json.dumps({"type": "session.update", "session": {"modalities": ["audio", "text"]}})
        )
        speech = base64.b64encode(b"\x01\x02\x03\x04" * 40).decode("ascii")
        service.handle_client_message(
            json.dumps({"type": "input_audio_buffer.append", "audio": speech})
        )
        service.handle_client_message(json.dumps({"type": "input_audio_buffer.commit"}))

        events = _drain_until_done(service)
        response_id = next(
            event["response"]["id"] for event in events if event["type"] == "response.created"
        )
        transcript_deltas = [
            event for event in events if event["type"] == "response.output_audio_transcript.delta"
        ]
        transcript_done = next(
            event for event in events if event["type"] == "response.output_audio_transcript.done"
        )
        response_done_index = next(
            index for index, event in enumerate(events) if event["type"] == "response.done"
        )
        transcript_done_index = events.index(transcript_done)

        assert "".join(event["delta"] for event in transcript_deltas) == "Sure, here you go."
        assert all(event["response_id"] == response_id for event in transcript_deltas)
        assert transcript_done["transcript"] == "Sure, here you go."
        assert transcript_done["response_id"] == response_id
        user_item_id = next(
            event["item"]["id"]
            for event in events
            if event["type"] == "conversation.item.created"
        )
        assert transcript_done["item_id"] != user_item_id
        assert all(event["item_id"] == transcript_done["item_id"] for event in transcript_deltas)
        assert transcript_done_index < response_done_index
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


# --------------------------------------------------------------------------- #
# PUNCH-LIST #3 -- realtime input-side lifecycle
# --------------------------------------------------------------------------- #
def _drain_until_done(service, *, timeout=3.0):
    import time

    events = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        events += service.drain_pipeline_events()
        if any(e["type"] == "response.done" for e in events):
            break
        time.sleep(0.05)
    return events


# --------------------------------------------------------------------------- #
# F5 -- audio_in backpressure: drop-oldest once the cap is reached, never
# grows without bound, and the enqueue itself never blocks.
# --------------------------------------------------------------------------- #


def test_response_cancel_bumps_the_shared_cancel_scope():
    pipeline, service, sent = _make_service()
    try:
        before = pipeline.cancel_scope.current()
        service.handle_client_message(json.dumps({"type": "response.cancel"}))
        assert pipeline.cancel_scope.current() == before + 1
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_response_cancel_with_nothing_in_flight_emits_no_terminal_event():
    """A ``response.cancel`` with no ``response.create`` in progress (the
    scenario the test above exercises) must NOT emit a spurious
    ``response.done`` -- there is no matching ``response.created`` to pair
    it against."""
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(json.dumps({"type": "response.cancel"}))
        assert not any(e["type"] == "response.done" for e in sent)
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_barge_in_drops_stale_audio_and_emits_exactly_one_terminal_done():
    """Regression for B1 (stale-generation drop in ``drain_pipeline_events``)
    and B2 (a cancelled turn must still terminate on the wire), exercised
    together as the review requires: drive a real response, queue an
    ``audio_out`` item under its generation, cancel (bumping the
    generation), queue ANOTHER (now doubly-stale) ``audio_out`` item, then
    drain -- neither stale item may surface as ``response.output_audio.delta``,
    and exactly one terminal ``response.done`` (status ``cancelled``) must
    have been sent for the interrupted turn, carrying the SAME ``response.id``
    its own ``response.created`` minted (PUNCH-LIST #3).
    """
    pipeline, service, sent = _make_service()
    try:
        item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }
        service.handle_client_message(
            json.dumps({"type": "conversation.item.create", "item": item})
        )
        service.handle_client_message(json.dumps({"type": "response.create"}))
        turn_id = service.state.current_turn_id
        assert turn_id is not None
        response_id = service.state.current_response_id
        assert response_id
        generation = pipeline.cancel_scope.current()

        # An AudioOut synthesized under the pre-cancel generation, already
        # queued before the barge-in lands.
        pipeline.audio_out.put(
            AudioOut(
                turn_id=turn_id, turn_revision=0, generation=generation, pcm=b"pre-cancel-tail"
            )
        )

        service.handle_client_message(json.dumps({"type": "response.cancel"}))
        assert pipeline.cancel_scope.current() == generation + 1
        assert service.state.current_turn_id is None  # cleared once terminated
        assert service.state.current_response_id is None

        # A second, doubly-stale item landing on the queue AFTER the cancel
        # (e.g. a slow TTS stage still finishing the superseded turn).
        pipeline.audio_out.put(
            AudioOut(
                turn_id=turn_id, turn_revision=0, generation=generation, pcm=b"post-cancel-tail"
            )
        )

        drained = service.drain_pipeline_events()
        audio_deltas = [e for e in drained if e["type"] == "response.output_audio.delta"]
        assert audio_deltas == [], "stale-generation audio must be dropped, got: %r" % (
            audio_deltas,
        )

        all_events = sent + drained
        done_events = [e for e in all_events if e["type"] == "response.done"]
        assert len(done_events) == 1, "expected exactly one terminal response.done, got: %r" % (
            done_events,
        )
        assert done_events[0]["response"]["status"] == "cancelled"
        assert done_events[0]["response"]["turn_id"] == turn_id
        assert done_events[0]["response"]["id"] == response_id
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)
