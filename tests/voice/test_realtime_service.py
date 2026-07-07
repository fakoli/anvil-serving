"""RealtimeService: client events -> pipeline side effects -> server events
(``anvil_serving.voice.realtime.service``). Dependency-light: a fake LLM
``stream_fn``, no real HTTP/socket/audio hardware.
"""
from __future__ import annotations

import base64
import json
import queue
from types import SimpleNamespace

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import AudioOut, LLMToolCall
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


def test_session_update_configures_llm_instructions_and_tools():
    pipeline, service, sent = _make_service()
    try:
        service.handle_client_message(json.dumps({
            "type": "session.update",
            "session": {
                "model": "fast-local",
                "instructions": "Use OpenClaw session context.",
                "tools": [
                    {
                        "type": "function",
                        "name": "openclaw_agent_consult",
                        "description": "Ask OpenClaw.",
                        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
                    }
                ],
                "tool_choice": "auto",
            },
        }))
        assert sent[-1]["type"] == "session.updated"
        assert pipeline.llm.config.model == "fast-local"
        assert pipeline.llm.config.system_prompt == "Use OpenClaw session context."
        assert pipeline.llm.config.tools == [
            {
                "type": "function",
                "function": {
                    "name": "openclaw_agent_consult",
                    "description": "Ask OpenClaw.",
                    "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
                },
            }
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
        item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello there"}]}
        service.handle_client_message(json.dumps({"type": "conversation.item.create", "item": item}))
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

        # NOTE: pipeline.audio_out only ever carries AudioOut/EndOfResponse --
        # VoicePipeline's LLMChunkToTTSInput bridge (pipeline.py) consumes
        # every LLMChunk and turns it into TTSInput before anything reaches
        # audio_out, so a "response.output_audio_transcript.delta" (LLMChunk's
        # dispatch mapping in events.py) never actually appears on this
        # queue in today's wiring -- assert on what really arrives instead.
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


def test_function_call_output_is_submitted_to_llm_stage():
    calls = []
    llm = SimpleNamespace(
        submit_tool_result=lambda call_id, output, **kwargs: calls.append((call_id, output, kwargs))
    )
    fake_pipeline = SimpleNamespace(audio_in=queue.Queue(), llm=llm)
    service = RealtimeService(pipeline=fake_pipeline, send_event=lambda _e: None, session_id="s1")

    service.handle_client_message(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": {"text": "Sunny."},
            "will_continue": True,
        },
    }))

    assert calls == [
        ("call_1", '{"text":"Sunny."}', {"will_continue": True, "suppress_response": False})
    ]


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

        assert any(e["type"] == "response.output_audio.delta" for e in events)
        assert any(e["type"] == "response.done" for e in events)
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
        service.handle_client_message(json.dumps({"type": "input_audio_buffer.append", "audio": speech}))
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
            item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
            service.handle_client_message(json.dumps({"type": "conversation.item.create", "item": item}))
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
    events (response.output_audio_transcript.delta/response.output_audio.delta/
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
        pipeline=fake_pipeline, send_event=lambda e: None, session_id="s1",
        frame_bytes=4, flush_silence_frames=0, max_audio_in_queue=cap,
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
        pipeline=fake_pipeline, send_event=lambda e: None, session_id="s1",
        frame_bytes=1, flush_silence_frames=0, max_audio_in_queue=cap,
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
        item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}
        service.handle_client_message(json.dumps({"type": "conversation.item.create", "item": item}))
        service.handle_client_message(json.dumps({"type": "response.create"}))
        turn_id = service.state.current_turn_id
        assert turn_id is not None
        response_id = service.state.current_response_id
        assert response_id
        generation = pipeline.cancel_scope.current()

        # An AudioOut synthesized under the pre-cancel generation, already
        # queued before the barge-in lands.
        pipeline.audio_out.put(
            AudioOut(turn_id=turn_id, turn_revision=0, generation=generation, pcm=b"pre-cancel-tail")
        )

        service.handle_client_message(json.dumps({"type": "response.cancel"}))
        assert pipeline.cancel_scope.current() == generation + 1
        assert service.state.current_turn_id is None  # cleared once terminated
        assert service.state.current_response_id is None

        # A second, doubly-stale item landing on the queue AFTER the cancel
        # (e.g. a slow TTS stage still finishing the superseded turn).
        pipeline.audio_out.put(
            AudioOut(turn_id=turn_id, turn_revision=0, generation=generation, pcm=b"post-cancel-tail")
        )

        drained = service.drain_pipeline_events()
        audio_deltas = [e for e in drained if e["type"] == "response.output_audio.delta"]
        assert audio_deltas == [], "stale-generation audio must be dropped, got: %r" % (audio_deltas,)

        all_events = sent + drained
        done_events = [e for e in all_events if e["type"] == "response.done"]
        assert len(done_events) == 1, "expected exactly one terminal response.done, got: %r" % (done_events,)
        assert done_events[0]["response"]["status"] == "cancelled"
        assert done_events[0]["response"]["turn_id"] == turn_id
        assert done_events[0]["response"]["id"] == response_id
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_drain_pipeline_events_emits_function_call_item_done():
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
            "type": "conversation.item.done",
            "event_id": "evt_1",
            "item": {
                "id": "call_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "openclaw_agent_consult",
                "arguments": '{"question":"weather"}',
            },
        }
    ]
