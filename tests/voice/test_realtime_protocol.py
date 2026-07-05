"""Client-event parse table + internal-event -> server-event dispatch table
(``anvil_serving.voice.realtime.events``). Pure dataclass/dict logic --
no sockets, no threads.
"""
from __future__ import annotations

import pytest

from anvil_serving.voice.messages import AudioOut, EndOfResponse, LLMChunk, Transcription
from anvil_serving.voice.stages.vad import SpeechEvent
from anvil_serving.voice.realtime.events import (
    ConversationItemCreate,
    ConversationItemInputAudioTranscriptionCompleted,
    EventParseError,
    InputAudioBufferAppend,
    InputAudioBufferClear,
    InputAudioBufferCommit,
    InputAudioBufferCommitted,
    InputAudioBufferSpeechStarted,
    InputAudioBufferSpeechStopped,
    ResponseCancel,
    ResponseCreate,
    ResponseAudioDelta,
    ResponseAudioTranscriptDelta,
    ResponseDone,
    SessionUpdate,
    ConversationItemCreated,
    dispatch_internal_event,
    make_error_event,
    parse_client_event,
    server_event_to_dict,
)


# --------------------------------------------------------------------------- #
# parse_client_event: type -> typed dataclass
# --------------------------------------------------------------------------- #


def test_parse_session_update():
    event = parse_client_event({"type": "session.update", "session": {"voice": "alloy"}})
    assert isinstance(event, SessionUpdate)
    assert event.session == {"voice": "alloy"}


def test_parse_input_audio_buffer_append():
    event = parse_client_event({"type": "input_audio_buffer.append", "audio": "AAA="})
    assert isinstance(event, InputAudioBufferAppend)
    assert event.audio == "AAA="


def test_parse_input_audio_buffer_commit_and_clear():
    commit = parse_client_event({"type": "input_audio_buffer.commit"})
    clear = parse_client_event({"type": "input_audio_buffer.clear"})
    assert isinstance(commit, InputAudioBufferCommit)
    assert isinstance(clear, InputAudioBufferClear)


def test_parse_conversation_item_create():
    item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    event = parse_client_event({"type": "conversation.item.create", "item": item})
    assert isinstance(event, ConversationItemCreate)
    assert event.item == item


def test_parse_response_create_and_cancel():
    create = parse_client_event({"type": "response.create", "response": {"modalities": ["audio"]}})
    cancel = parse_client_event({"type": "response.cancel"})
    assert isinstance(create, ResponseCreate)
    assert create.response == {"modalities": ["audio"]}
    assert isinstance(cancel, ResponseCancel)


def test_parse_client_event_rejects_unknown_type():
    with pytest.raises(EventParseError):
        parse_client_event({"type": "not.a.real.event"})


def test_parse_client_event_rejects_missing_type():
    with pytest.raises(EventParseError):
        parse_client_event({"session": {}})


def test_parse_client_event_rejects_non_dict():
    with pytest.raises(EventParseError):
        parse_client_event(["not", "a", "dict"])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# dispatch_internal_event: pipeline message -> server event(s)
# --------------------------------------------------------------------------- #


def test_dispatch_transcription_to_conversation_item_created():
    msg = Transcription(turn_id="t1", turn_revision=0, generation=1, text="hello", is_final=True)
    events = dispatch_internal_event(msg)
    assert len(events) == 2
    assert isinstance(events[0], ConversationItemCreated)
    assert events[0].item["content"][0]["text"] == "hello"
    assert events[0].item["id"] == "t1"
    # PUNCH-LIST #3: also the transcription-completed event, same item id.
    assert isinstance(events[1], ConversationItemInputAudioTranscriptionCompleted)
    assert events[1].item_id == "t1"
    assert events[1].transcript == "hello"


def test_dispatch_non_final_transcription_is_dropped():
    """Only a FINAL transcript surfaces on the wire -- see
    ``_dispatch_transcription``'s docstring."""
    msg = Transcription(turn_id="t1", turn_revision=0, generation=1, text="par", is_final=False)
    assert dispatch_internal_event(msg) == []


def test_dispatch_speech_started_carries_item_id_and_audio_start_ms():
    msg = SpeechEvent(kind="started", turn_id="turn-1", turn_revision=0, generation=1, audio_ms=40)
    (event,) = dispatch_internal_event(msg)
    assert isinstance(event, InputAudioBufferSpeechStarted)
    assert event.item_id == "turn-1"
    assert event.audio_start_ms == 40


def test_dispatch_speech_stopped_yields_stopped_then_committed():
    msg = SpeechEvent(kind="stopped", turn_id="turn-1", turn_revision=0, generation=1, audio_ms=240)
    events = dispatch_internal_event(msg)
    assert len(events) == 2
    assert isinstance(events[0], InputAudioBufferSpeechStopped)
    assert events[0].item_id == "turn-1"
    assert events[0].audio_end_ms == 240
    assert isinstance(events[1], InputAudioBufferCommitted)
    assert events[1].item_id == "turn-1"


def test_dispatch_llm_chunk_to_audio_transcript_delta():
    msg = LLMChunk(turn_id="t1", turn_revision=0, generation=1, text="Hi there.")
    events = dispatch_internal_event(msg, response_id="resp_1")
    assert len(events) == 1
    assert isinstance(events[0], ResponseAudioTranscriptDelta)
    assert events[0].delta == "Hi there."
    assert events[0].turn_id == "t1"
    assert events[0].response_id == "resp_1"


def test_dispatch_audio_out_to_response_audio_delta_base64():
    msg = AudioOut(turn_id="t1", turn_revision=0, generation=1, pcm=b"\x01\x02\x03")
    events = dispatch_internal_event(msg, response_id="resp_1")
    assert len(events) == 1
    assert isinstance(events[0], ResponseAudioDelta)
    assert events[0].response_id == "resp_1"
    import base64

    assert base64.b64decode(events[0].delta) == b"\x01\x02\x03"


def test_dispatch_end_of_response_to_response_done():
    msg = EndOfResponse(turn_id="t1", turn_revision=0, generation=1)
    events = dispatch_internal_event(msg, response_id="resp_1")
    assert len(events) == 1
    assert isinstance(events[0], ResponseDone)
    assert events[0].response["turn_id"] == "t1"
    assert events[0].response["status"] == "completed"
    assert events[0].response["id"] == "resp_1"


def test_dispatch_unmapped_message_type_returns_empty_list():
    assert dispatch_internal_event(object()) == []
    assert dispatch_internal_event("not a pipeline message") == []


# --------------------------------------------------------------------------- #
# server_event_to_dict + make_error_event
# --------------------------------------------------------------------------- #


def test_server_event_to_dict_includes_type_and_event_id():
    msg = EndOfResponse(turn_id="t1", turn_revision=0, generation=1)
    (event,) = dispatch_internal_event(msg)
    wire = server_event_to_dict(event)
    assert wire["type"] == "response.done"
    assert isinstance(wire["event_id"], str) and wire["event_id"]


def test_make_error_event_shape():
    event = make_error_event("invalid_request", "bad thing happened")
    wire = server_event_to_dict(event)
    assert wire["type"] == "error"
    assert wire["error"] == {"type": "invalid_request", "message": "bad thing happened"}


def test_dispatch_internal_event_uses_injected_id_source():
    """Regression test: dispatch_internal_event/make_error_event must draw
    from a caller-supplied ``id_source`` when given one, instead of always
    minting from this module's own fallback counter -- this is what lets
    ``RealtimeService`` share ONE per-connection id sequence across both the
    events it builds directly and the ones dispatched here (see
    ``service.py``'s ``_evt_id``)."""
    ids = iter(["evt_custom_1", "evt_custom_2"])

    def fake_id_source():
        return next(ids)

    msg = EndOfResponse(turn_id="t1", turn_revision=0, generation=1)
    (event,) = dispatch_internal_event(msg, id_source=fake_id_source)
    assert event.event_id == "evt_custom_1"

    error = make_error_event("invalid_request", "boom", id_source=fake_id_source)
    assert error.event_id == "evt_custom_2"


def test_dispatch_internal_event_defaults_to_module_counter_when_no_id_source_given():
    msg = EndOfResponse(turn_id="t1", turn_revision=0, generation=1)
    (event,) = dispatch_internal_event(msg)
    assert event.event_id.startswith("evt_")


def test_every_client_event_type_is_a_dataclass_with_a_type_field():
    for type_str, parser in [
        ("session.update", {"type": "session.update"}),
        ("input_audio_buffer.append", {"type": "input_audio_buffer.append"}),
        ("input_audio_buffer.commit", {"type": "input_audio_buffer.commit"}),
        ("input_audio_buffer.clear", {"type": "input_audio_buffer.clear"}),
        ("conversation.item.create", {"type": "conversation.item.create"}),
        ("response.create", {"type": "response.create"}),
        ("response.cancel", {"type": "response.cancel"}),
    ]:
        event = parse_client_event(parser)
        assert event.type == type_str
