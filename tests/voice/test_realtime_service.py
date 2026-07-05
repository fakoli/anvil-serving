"""RealtimeService: client events -> pipeline side effects -> server events
(``anvil_serving.voice.realtime.service``). Dependency-light: a fake LLM
``stream_fn``, no real HTTP/socket/audio hardware.
"""
from __future__ import annotations

import base64
import json

from anvil_serving.voice.messages import AudioOut
from anvil_serving.voice.pipeline import VoicePipeline
from anvil_serving.voice.realtime.service import RealtimeService
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


def test_session_update_merges_config_and_echoes():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(json.dumps({"type": "session.update", "session": {"voice": "alloy"}}))
        assert service.state.session_config == {"voice": "alloy"}
        assert sent[-1]["type"] == "session.updated"
        assert sent[-1]["session"] == {"voice": "alloy"}
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
        item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello there"}]}
        service.handle_client_message(json.dumps({"type": "conversation.item.create", "item": item}))
        assert sent[-1]["type"] == "conversation.item.created"
        assert service.state.pending_text == ["hello there"]

        service.handle_client_message(json.dumps({"type": "response.create"}))
        assert sent[-1]["type"] == "response.created"
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

        # NOTE: pipeline.audio_out only ever carries AudioOut/EndOfResponse --
        # VoicePipeline's LLMChunkToTTSInput bridge (pipeline.py) consumes
        # every LLMChunk and turns it into TTSInput before anything reaches
        # audio_out, so a "response.audio_transcript.delta" (LLMChunk's
        # dispatch mapping in events.py) never actually appears on this
        # queue in today's wiring -- assert on what really arrives instead.
        audio_deltas = [e for e in events if e["type"] == "response.audio.delta"]
        assert audio_deltas, f"expected at least one audio delta, got: {events}"
        assert any(e["type"] == "response.done" for e in events)
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


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
        service.handle_client_message(json.dumps({"type": "input_audio_buffer.append", "audio": speech}))
        service.handle_client_message(json.dumps({"type": "input_audio_buffer.commit"}))

        events = []
        import time

        deadline = time.time() + 3.0
        while time.time() < deadline:
            events += service.drain_pipeline_events()
            if any(e["type"] == "response.done" for e in events):
                break
            time.sleep(0.05)

        assert any(e["type"] == "response.audio.delta" for e in events)
        assert any(e["type"] == "response.done" for e in events)
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
        service.handle_client_message(json.dumps({"type": "input_audio_buffer.append", "audio": audio}))
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
    events (response.audio_transcript.delta/response.audio.delta/
    response.done/error) -- both started at 1, so one connection's event log
    could contain colliding event_ids. RealtimeService must now mint every
    event_id for one connection from ONE shared per-connection source."""
    pipeline, service, sent = _make_service()
    try:
        item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello there"}]}
        service.handle_client_message(json.dumps({"type": "conversation.item.create", "item": item}))
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
        assert len(all_ids) == len(set(all_ids)), "duplicate event_id across direct+dispatched events: %r" % all_ids
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


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
    drain -- neither stale item may surface as ``response.audio.delta``, and
    exactly one terminal ``response.done`` (status ``cancelled``) must have
    been sent for the interrupted turn.
    """
    pipeline, service, sent = _make_service()
    try:
        item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}
        service.handle_client_message(json.dumps({"type": "conversation.item.create", "item": item}))
        service.handle_client_message(json.dumps({"type": "response.create"}))
        turn_id = service.state.current_turn_id
        assert turn_id is not None
        generation = pipeline.cancel_scope.current()

        # An AudioOut synthesized under the pre-cancel generation, already
        # queued before the barge-in lands.
        pipeline.audio_out.put(
            AudioOut(turn_id=turn_id, turn_revision=0, generation=generation, pcm=b"pre-cancel-tail")
        )

        service.handle_client_message(json.dumps({"type": "response.cancel"}))
        assert pipeline.cancel_scope.current() == generation + 1
        assert service.state.current_turn_id is None  # cleared once terminated

        # A second, doubly-stale item landing on the queue AFTER the cancel
        # (e.g. a slow TTS stage still finishing the superseded turn).
        pipeline.audio_out.put(
            AudioOut(turn_id=turn_id, turn_revision=0, generation=generation, pcm=b"post-cancel-tail")
        )

        drained = service.drain_pipeline_events()
        audio_deltas = [e for e in drained if e["type"] == "response.audio.delta"]
        assert audio_deltas == [], "stale-generation audio must be dropped, got: %r" % (audio_deltas,)

        all_events = sent + drained
        done_events = [e for e in all_events if e["type"] == "response.done"]
        assert len(done_events) == 1, "expected exactly one terminal response.done, got: %r" % (done_events,)
        assert done_events[0]["response"]["status"] == "cancelled"
        assert done_events[0]["response"]["turn_id"] == turn_id
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)
