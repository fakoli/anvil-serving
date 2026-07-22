"""RealtimeService: client events -> pipeline side effects -> server events
(``anvil_serving.voice.realtime.service``). Dependency-light: a fake LLM
``stream_fn``, no real HTTP/socket/audio hardware.
"""

from __future__ import annotations

import base64
import json
import queue
import threading
import urllib.error
from types import SimpleNamespace

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import (
    AudioOut,
    EndOfResponse,
    LLMToolCall,
    SpokenText,
    Transcription,
    TTSSynthesisFailed,
)
from anvil_serving.voice.pipeline import VoicePipeline
from anvil_serving.voice.realtime.service import RealtimeService
from anvil_serving.voice.realtime.service import (
    RealtimeProxyLogs,
    RealtimeProxyService,
    RealtimeProxyStopTimeoutError,
)
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


class _BlockingServer:
    def __init__(self):
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.closed = False

    def serve_forever(self):
        self.started.set()
        self.stopped.wait(2)

    def shutdown(self):
        self.stopped.set()

    def server_close(self):
        self.closed = True


def test_realtime_proxy_lifecycle_is_mini_owned_and_bounded():
    server = _BlockingServer()
    proxy = RealtimeProxyService(lambda: server, port=8765)

    started = proxy.start()
    assert started.owner == "mini"
    assert started.running is True
    assert server.started.wait(1)
    assert proxy.status().host == "127.0.0.1"
    assert proxy.stop(timeout=1).running is False
    assert server.closed is True
    assert proxy.logs() == RealtimeProxyLogs()


def test_realtime_proxy_rejects_non_mini_ownership():
    with pytest.raises(ValueError, match="owner"):
        RealtimeProxyService(lambda: _BlockingServer(), owner="dark")


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "100.87.34.66"])
def test_realtime_proxy_rejects_non_loopback_and_wildcard_binds(host):
    with pytest.raises(ValueError, match="127.0.0.1"):
        RealtimeProxyService(lambda: _BlockingServer(), host=host)


def test_realtime_proxy_close_failure_is_typed_and_does_not_block_restart():
    close_error = OSError("close failed")

    class CloseFailingServer(_BlockingServer):
        def server_close(self):
            self.closed = True
            raise close_error

    first = CloseFailingServer()
    second = _BlockingServer()
    servers = iter((first, second))
    proxy = RealtimeProxyService(lambda: next(servers))

    proxy.start()
    assert first.started.wait(1)
    stopped = proxy.stop(timeout=1)
    assert stopped.running is False
    assert stopped.stopping is False
    assert stopped.close_error is close_error
    assert first.closed is True

    restarted = proxy.restart(timeout=1)
    assert restarted.running is True
    assert restarted.close_error is None
    assert second.started.wait(1)
    assert proxy.stop(timeout=1).running is False
    assert second.closed is True


def test_realtime_proxy_foreground_run_reports_running_and_can_be_stopped():
    server = _BlockingServer()
    proxy = RealtimeProxyService(lambda: server)
    runner = threading.Thread(target=proxy.run)

    runner.start()
    assert server.started.wait(1)
    assert proxy.status().running is True
    assert proxy.stop(timeout=1).running is False
    runner.join(timeout=1)
    assert not runner.is_alive()


def test_realtime_proxy_restart_fails_if_old_runner_times_out():
    release = threading.Event()
    factory_calls = []

    class HungServer(_BlockingServer):
        def serve_forever(self):
            self.started.set()
            release.wait(2)

        def shutdown(self):
            pass

    server = HungServer()

    def factory():
        factory_calls.append(1)
        return server

    proxy = RealtimeProxyService(factory)
    proxy.start()
    assert server.started.wait(1)
    with pytest.raises(RealtimeProxyStopTimeoutError):
        proxy.stop(timeout=0.01)
    with pytest.raises(RealtimeProxyStopTimeoutError):
        proxy.restart(timeout=0.01)
    assert len(factory_calls) == 1
    release.set()
    assert server.stopped.wait(0.01) is False
    for _ in range(100):
        if not proxy.status().running:
            break
        threading.Event().wait(0.01)
    assert proxy.status().running is False


def test_realtime_proxy_stop_times_out_when_shutdown_blocks():
    release_shutdown = threading.Event()
    stop_finished = threading.Event()
    stop_errors = queue.Queue()

    class BlockingShutdownServer(_BlockingServer):
        def shutdown(self):
            release_shutdown.wait()
            super().shutdown()

    server = BlockingShutdownServer()
    proxy = RealtimeProxyService(lambda: server)
    proxy.start()
    assert server.started.wait(1)

    def stop_proxy():
        try:
            proxy.stop(timeout=0.01)
        except BaseException as exc:
            stop_errors.put(exc)
        finally:
            stop_finished.set()

    stopper = threading.Thread(target=stop_proxy)
    try:
        stopper.start()
        assert stop_finished.wait(1), "stop() remained blocked in server.shutdown()"
        error = stop_errors.get_nowait()
        assert isinstance(error, RealtimeProxyStopTimeoutError)
        assert proxy.status().running is True
        assert proxy.status().stopping is True
    finally:
        release_shutdown.set()
        stopper.join(timeout=1)
        server.stopped.wait(1)


def test_realtime_proxy_immediate_start_stop_has_initialized_server():
    server = _BlockingServer()
    proxy = RealtimeProxyService(lambda: server)

    proxy.start()
    assert proxy.stop(timeout=1).running is False
    assert server.closed is True


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "1"])
def test_realtime_proxy_stop_rejects_invalid_timeout(timeout):
    proxy = RealtimeProxyService(lambda: _BlockingServer())

    with pytest.raises(ValueError, match="timeout must be positive"):
        proxy.stop(timeout=timeout)


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


def test_session_update_merges_config_and_echoes():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(
            json.dumps({"type": "session.update", "session": {"voice": "alloy"}})
        )
        assert service.state.session_config == {"voice": "alloy"}
        assert sent[-1]["type"] == "session.updated"
        assert sent[-1]["session"] == {"voice": "alloy"}
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_session_update_configures_llm_instructions_and_tools_without_model_override():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "model": "gpt-realtime",
                        "instructions": "Use OpenClaw session context.",
                        "tools": [
                            {
                                "type": "function",
                                "name": "openclaw_agent_consult",
                                "description": "Ask OpenClaw.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"question": {"type": "string"}},
                                },
                            }
                        ],
                        "tool_choice": "auto",
                    },
                }
            )
        )
        assert sent[-1]["type"] == "session.updated"
        assert pipeline.llm.config.model == "chat-fast"
        assert pipeline.llm.config.system_prompt == "Use OpenClaw session context."
        assert pipeline.llm.config.tools == [
            {
                "type": "function",
                "function": {
                    "name": "openclaw_agent_consult",
                    "description": "Ask OpenClaw.",
                    "parameters": {
                        "type": "object",
                        "properties": {"question": {"type": "string"}},
                    },
                },
            }
        ]
        assert pipeline.llm.config.tool_choice == "auto"
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


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
        assert pipeline.llm.config.model == "chat-fast"
        assert pipeline.llm.config.system_prompt == "Response prompt."
        assert pipeline.llm.config.tools == [
            {"type": "function", "function": {"name": "openclaw_agent_consult"}}
        ]
        assert pipeline.llm.config.tool_choice == "auto"
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_unknown_event_type_yields_error_event_not_a_crash():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(json.dumps({"type": "not.a.real.event"}))
        assert sent[-1]["type"] == "error"
        assert sent[-1]["error"]["type"] == "invalid_request"
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_invalid_json_yields_error_event_not_a_crash():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message("{not json")
        assert sent[-1]["type"] == "error"
        assert sent[-1]["error"]["type"] == "invalid_json"
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_text_conversation_item_bridges_straight_to_generate_request():
    pipeline, service, sent = _make_service()
    try:
        item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello there"}],
        }
        service.handle_client_message(
            json.dumps({"type": "conversation.item.create", "item": item})
        )
        assert sent[-1]["type"] == "conversation.item.created"
        assert service.state.pending_text == ["hello there"]

        service.handle_client_message(json.dumps({"type": "response.create"}))
        assert sent[-1]["type"] == "response.created"
        response_id = sent[-1]["response"]["id"]
        assert response_id  # PUNCH-LIST #3: a real, non-empty response id
        assert service.state.pending_text == []  # consumed

        # Give the (already-running) pipeline threads a moment to process the
        # GenerateRequest we pushed straight onto pipeline.llm.in_queue.
        events = []
        import time

        deadline = time.time() + 3.0
        while time.time() < deadline:
            events += service.drain_pipeline_events()
            if any(e["type"] == "response.done" for e in events):
                break
            time.sleep(0.05)

        # Audio-only is the default. The LLM sideband must not change the
        # established wire contract until the client explicitly requests text.
        transcript_deltas = [
            e for e in events if e["type"] == "response.output_audio_transcript.delta"
        ]
        assert transcript_deltas == []
        audio_deltas = [e for e in events if e["type"] == "response.output_audio.delta"]
        assert audio_deltas, f"expected at least one audio delta, got: {events}"
        # PUNCH-LIST #3: every delta for this response carries the SAME id
        # response.created minted.
        assert all(e["response_id"] == response_id for e in audio_deltas)
        done_events = [e for e in events if e["type"] == "response.done"]
        assert done_events
        assert done_events[0]["response"]["id"] == response_id
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_text_modality_streams_the_actual_llm_text_and_a_terminal_before_response_done():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(
            json.dumps({"type": "session.update", "session": {"modalities": ["audio", "text"]}})
        )
        service.handle_client_message(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "say hello"}],
                    },
                }
            )
        )
        service.handle_client_message(json.dumps({"type": "response.create"}))
        response_id = sent[-1]["response"]["id"]

        events = _drain_until_done(service)
        transcript_deltas = [
            e for e in events if e["type"] == "response.output_audio_transcript.delta"
        ]
        transcript_done = [
            e for e in events if e["type"] == "response.output_audio_transcript.done"
        ]
        response_done_index = next(
            index for index, event in enumerate(events) if event["type"] == "response.done"
        )
        transcript_done_index = next(
            index
            for index, event in enumerate(events)
            if event["type"] == "response.output_audio_transcript.done"
        )

        assert [event["delta"] for event in transcript_deltas] == ["Sure, here you go."]
        assert all(event["response_id"] == response_id for event in transcript_deltas)
        assert len(transcript_done) == 1
        assert transcript_done[0]["event_id"]
        assert transcript_done[0]["transcript"] == "Sure, here you go."
        assert transcript_done[0]["turn_id"] == transcript_deltas[0]["turn_id"]
        assert transcript_done[0]["response_id"] == response_id
        assert transcript_done_index < response_done_index
        assert events[response_done_index]["response"]["id"] == response_id
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def _fake_output_pipeline():
    return SimpleNamespace(
        vad_events=queue.Queue(),
        transcript_events=queue.Queue(),
        audio_out=queue.Queue(),
        cancel_scope=CancelScope(),
    )


def test_spoken_text_is_joined_with_boundaries_and_terminal_precedes_response_done():
    fake_pipeline = _fake_output_pipeline()
    service = RealtimeService(pipeline=fake_pipeline, send_event=lambda _event: None, session_id="s1")
    service.state.session_config = {"output_modalities": ["audio", "text"]}
    created = service._begin_response("turn-1")
    generation = fake_pipeline.cancel_scope.current()
    fake_pipeline.audio_out.put(
        SpokenText(turn_id="turn-1", turn_revision=0, generation=generation, text="Hello world.")
    )
    fake_pipeline.audio_out.put(
        SpokenText(
            turn_id="turn-1",
            turn_revision=0,
            generation=generation,
            text="How are you?",
            joiner=" ",
        )
    )
    fake_pipeline.audio_out.put(
        EndOfResponse(turn_id="turn-1", turn_revision=0, generation=generation)
    )

    events = service.drain_pipeline_events()

    assert [event["type"] for event in events] == [
        "response.output_audio_transcript.delta",
        "response.output_audio_transcript.delta",
        "response.output_audio_transcript.done",
        "response.done",
    ]
    assert [event["delta"] for event in events[:2]] == ["Hello world.", " How are you?"]
    assert events[2]["transcript"] == "Hello world. How are you?"
    for event in events[:3]:
        assert event["response_id"] == created["response"]["id"]
        assert event["item_id"] == "item_%s" % created["response"]["id"]
        assert event["output_index"] == 0
        assert event["content_index"] == 0


def test_tts_failure_emits_correlated_error_without_claiming_unspoken_text():
    fake_pipeline = _fake_output_pipeline()
    service = RealtimeService(pipeline=fake_pipeline, send_event=lambda _event: None, session_id="s1")
    service.state.session_config = {"output_modalities": ["audio", "text"]}
    created = service._begin_response("turn-1")
    fake_pipeline.audio_out.put(
        TTSSynthesisFailed(turn_id="turn-1", turn_revision=0, generation=0)
    )
    fake_pipeline.audio_out.put(
        EndOfResponse(turn_id="turn-1", turn_revision=0, generation=0)
    )

    events = service.drain_pipeline_events()

    assert [event["type"] for event in events] == ["error", "response.done"]
    assert events[0]["error"]["type"] == "assistant_transcript_unavailable"
    assert events[0]["response_id"] == created["response"]["id"]
    assert events[1]["response"]["status"] == "failed"
    assert not any(event["type"].endswith("transcript.done") for event in events)


def test_assistant_transcript_limit_is_bounded_and_fails_closed_once():
    fake_pipeline = _fake_output_pipeline()
    service = RealtimeService(
        pipeline=fake_pipeline,
        send_event=lambda _event: None,
        session_id="s1",
        max_assistant_transcript_chars=5,
    )
    service.state.session_config = {"output_modalities": ["audio", "text"]}
    service._begin_response("turn-1")
    for text in ("12345", "6", "7"):
        fake_pipeline.audio_out.put(
            SpokenText(turn_id="turn-1", turn_revision=0, generation=0, text=text)
        )
    fake_pipeline.audio_out.put(
        EndOfResponse(turn_id="turn-1", turn_revision=0, generation=0)
    )

    events = service.drain_pipeline_events()

    assert [event["type"] for event in events] == [
        "response.output_audio_transcript.delta",
        "error",
        "response.done",
    ]
    assert service.state.current_assistant_transcript == ["12345"]
    assert service.state.current_assistant_transcript_chars == 5


def test_response_state_reset_allows_a_second_audio_turn_transcript():
    fake_pipeline = _fake_output_pipeline()
    service = RealtimeService(pipeline=fake_pipeline, send_event=lambda _event: None, session_id="s1")
    service.state.session_config = {"output_modalities": ["audio", "text"]}

    first = service._begin_response("turn-1")["response"]["id"]
    fake_pipeline.audio_out.put(
        SpokenText(turn_id="turn-1", turn_revision=0, generation=0, text="First.")
    )
    fake_pipeline.audio_out.put(EndOfResponse(turn_id="turn-1", turn_revision=0, generation=0))
    first_events = service.drain_pipeline_events()
    service.mark_response_done_sent(first)

    second = service._begin_response("turn-2")["response"]["id"]
    fake_pipeline.audio_out.put(
        SpokenText(turn_id="turn-2", turn_revision=0, generation=0, text="Second.")
    )
    fake_pipeline.audio_out.put(EndOfResponse(turn_id="turn-2", turn_revision=0, generation=0))
    second_events = service.drain_pipeline_events()

    assert next(e for e in first_events if e["type"].endswith("transcript.done"))["transcript"] == "First."
    second_done = next(e for e in second_events if e["type"].endswith("transcript.done"))
    assert second_done["transcript"] == "Second."
    assert second_done["response_id"] == second


def test_output_modalities_response_override_is_canonical():
    fake_pipeline = SimpleNamespace(
        vad_events=queue.Queue(),
        transcript_events=queue.Queue(),
        audio_out=queue.Queue(),
        cancel_scope=CancelScope(),
    )
    service = RealtimeService(pipeline=fake_pipeline, send_event=lambda _event: None, session_id="s1")
    service.state.session_config = {"output_modalities": ["audio"]}
    created = service._begin_response(
        "turn-1", response_config={"output_modalities": ["audio", "text"]}
    )
    fake_pipeline.audio_out.put(
        SpokenText(turn_id="turn-1", turn_revision=0, generation=0, text="Canonical.")
    )
    fake_pipeline.audio_out.put(
        EndOfResponse(turn_id="turn-1", turn_revision=0, generation=0)
    )
    events = service.drain_pipeline_events()

    assert [event["type"] for event in events] == [
        "response.output_audio_transcript.delta",
        "response.output_audio_transcript.done",
        "response.done",
    ]
    assert events[1]["response_id"] == created["response"]["id"]


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


def test_two_backlogged_audio_turns_keep_distinct_response_correlation():
    fake_pipeline = _fake_output_pipeline()
    service = RealtimeService(pipeline=fake_pipeline, send_event=lambda _event: None, session_id="s1")
    service.state.session_config = {"output_modalities": ["audio", "text"]}
    for turn_id in ("turn-1", "turn-2"):
        fake_pipeline.transcript_events.put(
            Transcription(turn_id=turn_id, turn_revision=0, generation=0, text=turn_id)
        )
        fake_pipeline.audio_out.put(
            SpokenText(turn_id=turn_id, turn_revision=0, generation=0, text=turn_id)
        )
        fake_pipeline.audio_out.put(
            EndOfResponse(turn_id=turn_id, turn_revision=0, generation=0)
        )

    first_events = service.drain_pipeline_events()
    first_id = next(e["response"]["id"] for e in first_events if e["type"] == "response.created")
    assert {e.get("response_id") for e in first_events if "response_id" in e} == {first_id}
    assert next(e for e in first_events if e["type"] == "response.done")["response"]["id"] == first_id
    assert service.drain_pipeline_events() == []
    assert fake_pipeline.transcript_events.qsize() == 1
    assert fake_pipeline.audio_out.qsize() == 2
    service.mark_response_done_sent(first_id)

    second_events = service.drain_pipeline_events()
    second_id = next(e["response"]["id"] for e in second_events if e["type"] == "response.created")
    assert second_id != first_id
    assert {e.get("response_id") for e in second_events if "response_id" in e} == {second_id}
    assert next(e for e in second_events if e["type"] == "response.done")["response"]["id"] == second_id


def test_response_modalities_override_the_session_text_setting():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(
            json.dumps({"type": "session.update", "session": {"modalities": ["audio", "text"]}})
        )
        service.handle_client_message(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "audio only"}],
                    },
                }
            )
        )
        service.handle_client_message(
            json.dumps({"type": "response.create", "response": {"modalities": ["audio"]}})
        )

        events = _drain_until_done(service)
        assert sent[-1]["type"] == "response.created"
        assert not any(
            event["type"].startswith("response.output_audio_transcript") for event in events
        )
        assert any(event["type"] == "response.output_audio.delta" for event in events)
        assert any(event["type"] == "response.done" for event in events)
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_text_only_modality_does_not_claim_a_spoken_audio_transcript():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(
            json.dumps({"type": "session.update", "session": {"modalities": ["text"]}})
        )
        service.handle_client_message(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "text only"}],
                    },
                }
            )
        )
        service.handle_client_message(json.dumps({"type": "response.create"}))

        events = _drain_until_done(service)
        assert sent[-1]["type"] == "response.created"
        assert not any(
            event["type"].startswith("response.output_audio_transcript") for event in events
        )
        assert any(event["type"] == "response.done" for event in events)
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_function_call_output_is_submitted_to_llm_stage():
    calls = []
    llm = SimpleNamespace(
        submit_tool_result=lambda call_id, output, **kwargs: (
            calls.append((call_id, output, kwargs)) or True
        )
    )
    fake_pipeline = SimpleNamespace(audio_in=queue.Queue(), llm=llm)
    service = RealtimeService(pipeline=fake_pipeline, send_event=lambda _e: None, session_id="s1")

    service.handle_client_message(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": {"text": "Sunny."},
                    "will_continue": True,
                },
            }
        )
    )

    assert calls == [
        ("call_1", '{"text":"Sunny."}', {"will_continue": True, "suppress_response": False})
    ]


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


def test_response_create_with_no_pending_input_is_an_error():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(json.dumps({"type": "response.create"}))
        assert sent[-1]["type"] == "error"
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_audio_buffer_append_commit_drives_the_pipeline_end_to_end():
    pipeline, service, sent = _make_service()
    try:
        speech = base64.b64encode(b"\x01\x02\x03\x04" * 40).decode("ascii")  # non-zero -> "speech"
        service.handle_client_message(
            json.dumps({"type": "input_audio_buffer.append", "audio": speech})
        )
        service.handle_client_message(json.dumps({"type": "input_audio_buffer.commit"}))

        events = []
        import time

        deadline = time.time() + 3.0
        while time.time() < deadline:
            events += service.drain_pipeline_events()
            if any(e["type"] == "response.done" for e in events):
                break
            time.sleep(0.05)

        assert any(e["type"] == "response.output_audio.delta" for e in events)
        assert any(e["type"] == "response.done" for e in events)
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


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


def test_audio_turn_emits_the_full_input_side_lifecycle_in_order():
    """A full audio-driven turn emits, in order: speech_started ->
    speech_stopped -> committed -> conversation.item.created(user) ->
    input_audio_transcription.completed -> response.created(id=X) ->
    response.output_audio.delta(response_id=X)+ -> response.done(id=X) --
    with NO explicit client ``response.create`` (the audio path auto-triggers
    its own response -- see ``service.py``'s ``drain_pipeline_events``
    docstring)."""
    pipeline, service, sent = _make_service()
    try:
        speech = base64.b64encode(b"\x01\x02\x03\x04" * 40).decode("ascii")
        service.handle_client_message(
            json.dumps({"type": "input_audio_buffer.append", "audio": speech})
        )
        service.handle_client_message(json.dumps({"type": "input_audio_buffer.commit"}))

        events = _drain_until_done(service)
        types_in_order = [e["type"] for e in events]

        def first_index(t):
            assert t in types_in_order, "expected %r somewhere in %r" % (t, types_in_order)
            return types_in_order.index(t)

        i_started = first_index("input_audio_buffer.speech_started")
        i_stopped = first_index("input_audio_buffer.speech_stopped")
        i_committed = first_index("input_audio_buffer.committed")
        i_item_created = first_index("conversation.item.created")
        i_transcript_done = first_index("conversation.item.input_audio_transcription.completed")
        i_resp_created = first_index("response.created")
        i_first_delta = first_index("response.output_audio.delta")
        i_resp_done = first_index("response.done")

        assert (
            i_started
            < i_stopped
            < i_committed
            < i_item_created
            < i_transcript_done
            < i_resp_created
            < i_first_delta
            < i_resp_done
        ), types_in_order

        response_id = next(e for e in events if e["type"] == "response.created")["response"]["id"]
        assert response_id
        deltas = [e for e in events if e["type"] == "response.output_audio.delta"]
        assert deltas and all(e["response_id"] == response_id for e in deltas)
        done = next(e for e in events if e["type"] == "response.done")
        assert done["response"]["id"] == response_id

        # This turn was audio-driven -- the client never sent response.create,
        # so `sent` (direct send_event calls, not drained pipeline events)
        # must contain no response.created of its own.
        assert not any(e.get("type") == "response.created" for e in sent)
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_response_ids_are_unique_across_two_sequential_text_turns():
    pipeline, service, sent = _make_service()
    try:

        def one_turn(text):
            item = {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
            service.handle_client_message(
                json.dumps({"type": "conversation.item.create", "item": item})
            )
            service.handle_client_message(json.dumps({"type": "response.create"}))
            created = sent[-1]
            assert created["type"] == "response.created"
            events = _drain_until_done(service)
            return created["response"]["id"], events

        resp_id_1, events_1 = one_turn("first turn")
        resp_id_2, events_2 = one_turn("second turn")

        assert resp_id_1 and resp_id_2
        assert resp_id_1 != resp_id_2
        # Every event in each turn's own drain carries (or, for events with no
        # response_id field, doesn't contradict) that turn's own response id.
        for events, resp_id in ((events_1, resp_id_1), (events_2, resp_id_2)):
            deltas = [e for e in events if e["type"] == "response.output_audio.delta"]
            assert deltas
            assert all(e["response_id"] == resp_id for e in deltas)
            done = next(e for e in events if e["type"] == "response.done")
            assert done["response"]["id"] == resp_id
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_commit_with_empty_buffer_is_an_error():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(json.dumps({"type": "input_audio_buffer.commit"}))
        assert sent[-1]["type"] == "error"
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_clear_discards_buffered_audio_before_it_reaches_the_pipeline():
    pipeline, service, sent = _make_service()
    try:
        audio = base64.b64encode(b"\x01\x02\x03\x04").decode("ascii")
        service.handle_client_message(
            json.dumps({"type": "input_audio_buffer.append", "audio": audio})
        )
        assert service._audio_buffer  # buffered locally, not yet pushed
        service.handle_client_message(json.dumps({"type": "input_audio_buffer.clear"}))
        assert service._audio_buffer == bytearray()
        assert pipeline.audio_in.empty()
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_event_ids_are_unique_across_direct_and_dispatched_events():
    """Regression test: service.py used to mint event_ids from its OWN
    module-global counter for the events it builds directly
    (session.updated/conversation.item.created/response.created), separate
    from events.py's own module-global counter used for dispatch-built
    events (response.output_audio_transcript.delta/response.output_audio.delta/
    response.done/error) -- both started at 1, so one connection's event log
    could contain colliding event_ids. RealtimeService must now mint every
    event_id for one connection from ONE shared per-connection source."""
    pipeline, service, sent = _make_service()
    try:
        item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello there"}],
        }
        service.handle_client_message(
            json.dumps({"type": "conversation.item.create", "item": item})
        )
        service.handle_client_message(json.dumps({"type": "response.create"}))

        events = []
        import time

        deadline = time.time() + 3.0
        while time.time() < deadline:
            events += service.drain_pipeline_events()
            if any(e["type"] == "response.done" for e in events):
                break
            time.sleep(0.05)

        assert events, "expected at least one dispatched event to compare against"
        all_ids = [e["event_id"] for e in sent] + [e["event_id"] for e in events]
        assert len(all_ids) == len(set(all_ids)), (
            "duplicate event_id across direct+dispatched events: %r" % all_ids
        )
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


# --------------------------------------------------------------------------- #
# F5 -- audio_in backpressure: drop-oldest once the cap is reached, never
# grows without bound, and the enqueue itself never blocks.
# --------------------------------------------------------------------------- #
def test_audio_commit_bounds_audio_in_via_drop_oldest():
    """A fake pipeline with NO consumer draining `audio_in`: committing far
    more frames than `max_audio_in_queue` in one shot must leave the queue
    bounded at the cap (drop-oldest), not grow unboundedly, and the commit
    call itself must return (never block)."""
    fake_pipeline = SimpleNamespace(audio_in=queue.Queue())
    cap = 5
    service = RealtimeService(
        pipeline=fake_pipeline,
        send_event=lambda e: None,
        session_id="s1",
        frame_bytes=4,
        flush_silence_frames=0,
        max_audio_in_queue=cap,
    )
    # 40 bytes / 4 bytes-per-frame == 10 frames -- double the cap.
    audio = base64.b64encode(b"\x01\x02\x03\x04" * 10).decode("ascii")
    service.handle_client_message(json.dumps({"type": "input_audio_buffer.append", "audio": audio}))
    service.handle_client_message(json.dumps({"type": "input_audio_buffer.commit"}))

    assert fake_pipeline.audio_in.qsize() == cap


def test_audio_commit_drop_oldest_keeps_the_newest_frames():
    """Drop-OLDEST specifically: after overflow, the frames actually left in
    the queue must be the tail of the committed audio, not the head."""
    fake_pipeline = SimpleNamespace(audio_in=queue.Queue())
    cap = 2
    service = RealtimeService(
        pipeline=fake_pipeline,
        send_event=lambda e: None,
        session_id="s1",
        frame_bytes=1,
        flush_silence_frames=0,
        max_audio_in_queue=cap,
    )
    audio = base64.b64encode(bytes([1, 2, 3, 4, 5])).decode("ascii")
    service.handle_client_message(json.dumps({"type": "input_audio_buffer.append", "audio": audio}))
    service.handle_client_message(json.dumps({"type": "input_audio_buffer.commit"}))

    remaining = [fake_pipeline.audio_in.get_nowait() for _ in range(fake_pipeline.audio_in.qsize())]
    assert remaining == [bytes([4]), bytes([5])]


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


def test_drain_pipeline_events_emits_standard_and_compat_function_call_events():
    fake_pipeline = SimpleNamespace(
        vad_events=queue.Queue(),
        transcript_events=queue.Queue(),
        audio_out=queue.Queue(),
        cancel_scope=CancelScope(),
    )
    fake_pipeline.audio_out.put(
        LLMToolCall(
            turn_id="turn-1",
            turn_revision=0,
            generation=0,
            item_id="call_1",
            call_id="call_1",
            name="openclaw_agent_consult",
            arguments='{"question":"weather"}',
        )
    )
    service = RealtimeService(pipeline=fake_pipeline, send_event=lambda _e: None, session_id="s1")

    events = service.drain_pipeline_events()

    assert events == [
        {
            "type": "response.output_item.added",
            "event_id": "evt_1",
            "response_id": "",
            "output_index": 0,
            "item": {
                "id": "call_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "openclaw_agent_consult",
                "arguments": '{"question":"weather"}',
                "status": "completed",
            },
        },
        {
            "type": "response.function_call_arguments.done",
            "event_id": "evt_2",
            "response_id": "",
            "item_id": "call_1",
            "output_index": 0,
            "call_id": "call_1",
            "name": "openclaw_agent_consult",
            "arguments": '{"question":"weather"}',
        },
        {
            "type": "response.output_item.done",
            "event_id": "evt_3",
            "response_id": "",
            "output_index": 0,
            "item": {
                "id": "call_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "openclaw_agent_consult",
                "arguments": '{"question":"weather"}',
                "status": "completed",
            },
        },
        {
            "type": "conversation.item.done",
            "event_id": "evt_4",
            "item": {
                "id": "call_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "openclaw_agent_consult",
                "arguments": '{"question":"weather"}',
                "status": "completed",
            },
        },
    ]
