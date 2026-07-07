"""OpenAI Realtime event tables: client-event parse table, internal-pipeline-
event -> server-event dispatch table (anvil task T012).

Two symmetric tables, matching the shape ``docs/findings/2026-07-04-hf-speech-
to-speech-review.md`` calls out as the flagship reusable piece of the
reference design's ``api/openai_realtime/`` server:

* :data:`CLIENT_EVENT_PARSERS` -- ``type`` string -> a function turning the
  raw client JSON object into a typed :class:`ClientEvent` dataclass.
  :func:`parse_client_event` is the single entry point.
* :data:`DISPATCH` -- internal pipeline message class (from
  :mod:`anvil_serving.voice.messages`) -> a function turning one such message
  into zero or more typed :class:`ServerEvent` dataclasses.
  :func:`dispatch_internal_event` is the single entry point.

Plain ``dataclasses`` only -- no pydantic, no ``openai`` SDK types. Every
event dataclass carries its own ``type`` string as a class attribute so
:func:`server_event_to_dict` can serialize it to the wire shape
(``{"type": ..., **fields}``) uniformly.

Covers the SDK-verified subset named in the unit brief: ``session.*``,
``conversation.*``, ``response.*``, ``input_audio_buffer.*``. Anything
outside that subset (item deletion/truncation, granular content-part
streaming, the full Realtime error taxonomy) is explicitly NOT modeled here
-- the reference review flagged these as reference-design gaps too; this is a
deliberately partial protocol surface, not an oversight.
"""
from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

from ..messages import AudioOut, EndOfResponse, LLMChunk, LLMToolCall, Transcription
from ..stages.vad import SpeechEvent

# --------------------------------------------------------------------------- #
# Client events (caller -> server)
# --------------------------------------------------------------------------- #


class EventParseError(ValueError):
    """Raised when a client event's ``type`` is unknown, or its payload is
    malformed for its declared type."""


@dataclass(frozen=True)
class ClientEvent:
    """Common base: every client event carries the ``type`` it was parsed
    from (mirrors the raw wire field so a handler doesn't need to re-derive
    it from the dataclass's own class)."""

    type: str


@dataclass(frozen=True)
class SessionUpdate(ClientEvent):
    """``session.update`` -- merge ``session`` into the connection's config."""

    session: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InputAudioBufferAppend(ClientEvent):
    """``input_audio_buffer.append`` -- ``audio`` is base64-encoded PCM."""

    audio: str = ""


@dataclass(frozen=True)
class InputAudioBufferCommit(ClientEvent):
    """``input_audio_buffer.commit`` -- flush the buffered audio as one turn."""


@dataclass(frozen=True)
class InputAudioBufferClear(ClientEvent):
    """``input_audio_buffer.clear`` -- discard buffered-but-uncommitted audio."""


@dataclass(frozen=True)
class ConversationItemCreate(ClientEvent):
    """``conversation.item.create`` -- append a conversation item (e.g. a
    text-only user turn) ahead of a ``response.create``."""

    item: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseCreate(ClientEvent):
    """``response.create`` -- trigger generation for the pending turn."""

    response: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseCancel(ClientEvent):
    """``response.cancel`` -- client-initiated barge-in / interruption."""


#: ``type`` string -> ``(dataclass, extra-fields-from-raw)`` builder.
def _build(cls: Type[ClientEvent]) -> Callable[[Dict[str, Any]], ClientEvent]:
    field_names = [f for f in cls.__dataclass_fields__ if f != "type"]

    def _parser(raw: Dict[str, Any]) -> ClientEvent:
        kwargs = {}
        for name in field_names:
            if name in raw:
                kwargs[name] = raw[name]
        try:
            return cls(type=raw["type"], **kwargs)
        except TypeError as exc:
            raise EventParseError("malformed %s event: %s" % (raw.get("type"), exc)) from exc

    return _parser


CLIENT_EVENT_PARSERS: Dict[str, Callable[[Dict[str, Any]], ClientEvent]] = {
    "session.update": _build(SessionUpdate),
    "input_audio_buffer.append": _build(InputAudioBufferAppend),
    "input_audio_buffer.commit": _build(InputAudioBufferCommit),
    "input_audio_buffer.clear": _build(InputAudioBufferClear),
    "conversation.item.create": _build(ConversationItemCreate),
    "response.create": _build(ResponseCreate),
    "response.cancel": _build(ResponseCancel),
}


def parse_client_event(raw: Dict[str, Any]) -> ClientEvent:
    """Parse one raw (already JSON-decoded) client event object.

    Raises :class:`EventParseError` if ``raw`` has no (or an unrecognized)
    ``type``, or if its payload doesn't match the event's expected shape.
    """
    if not isinstance(raw, dict):
        raise EventParseError("client event must be a JSON object")
    event_type = raw.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise EventParseError("client event missing a string 'type' field")
    parser = CLIENT_EVENT_PARSERS.get(event_type)
    if parser is None:
        raise EventParseError("unsupported client event type: %r" % event_type)
    return parser(raw)


# --------------------------------------------------------------------------- #
# Server events (server -> caller)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ServerEvent:
    """Common base: every server event carries its own ``type`` and an
    ``event_id`` (a per-connection monotonic counter minted at construction
    time by the small helpers below, mirroring the Realtime wire's
    ``event_id`` field)."""

    type: str
    event_id: str


#: Module-level fallback id source -- used only when a caller doesn't supply
#: its own (see the ``id_source`` parameter on :func:`make_error_event` /
#: :func:`dispatch_internal_event` below). A real connection should NOT rely
#: on this default: :class:`~anvil_serving.voice.realtime.service.RealtimeService`
#: owns and injects its OWN per-connection counter so every server event for
#: ONE connection -- both the ones it builds directly (``session.updated``,
#: etc.) and the ones built here via dispatch -- draws unique ids from the
#: SAME source, matching this class's own "per-connection monotonic counter"
#: claim below. Two callers sharing this module-level fallback (e.g. two
#: unrelated ``RealtimeService`` instances that both forgot to inject their
#: own id_source) WOULD collide with each other across connections, though
#: never within a single connection that consistently uses one source.
_event_id_counter = itertools.count(1)


def _next_event_id() -> str:
    return "evt_%d" % next(_event_id_counter)


IdSource = Callable[[], str]


@dataclass(frozen=True)
class SessionCreated(ServerEvent):
    session: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionUpdated(ServerEvent):
    session: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationItemCreated(ServerEvent):
    item: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationItemDone(ServerEvent):
    item: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseOutputItemAdded(ServerEvent):
    response_id: str = ""
    output_index: int = 0
    item: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseFunctionCallArgumentsDone(ServerEvent):
    response_id: str = ""
    item_id: str = ""
    output_index: int = 0
    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(frozen=True)
class ResponseOutputItemDone(ServerEvent):
    response_id: str = ""
    output_index: int = 0
    item: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseCreated(ServerEvent):
    """``response.created`` -- ``response["id"]`` is the real, unique
    per-response id (PUNCH-LIST #3) threaded through every later
    ``response.output_audio.delta``/``response.output_audio_transcript.delta``/
    ``response.done`` event for THIS response (see ``response_id`` on those
    below, and ``service.py``'s ``_begin_response``, the one place that mints
    it)."""

    response: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseAudioTranscriptDelta(ServerEvent):
    """Text delta for the assistant's spoken reply (from an
    :class:`~anvil_serving.voice.messages.LLMChunk`).

    Wire type renamed ``response.audio_transcript.delta`` ->
    ``response.output_audio_transcript.delta`` (PUNCH-LIST #3) to match the
    current OpenAI Realtime wire protocol's ``output_audio`` naming (the
    pre-GA ``response.audio.*`` names this unit originally shipped with).
    ``response_id`` correlates this delta back to the ``response.created``
    that started it -- see :class:`ResponseCreated`.
    """

    delta: str = ""
    turn_id: str = ""
    response_id: str = ""


@dataclass(frozen=True)
class ResponseAudioDelta(ServerEvent):
    """Base64-encoded synthesized PCM (from an
    :class:`~anvil_serving.voice.messages.AudioOut`).

    Wire type renamed ``response.audio.delta`` -> ``response.output_audio.delta``
    (PUNCH-LIST #3) -- see :class:`ResponseAudioTranscriptDelta`'s docstring
    for why. ``response_id`` correlates this delta back to its
    ``response.created`` -- see :class:`ResponseCreated`.
    """

    delta: str = ""
    turn_id: str = ""
    response_id: str = ""


@dataclass(frozen=True)
class ResponseDone(ServerEvent):
    """Terminal event for one turn's response (from an
    :class:`~anvil_serving.voice.messages.EndOfResponse`, OR minted directly
    by ``service.py``'s ``_on_response_cancel`` for a barge-in).
    ``response["id"]`` is the SAME id this response's own ``response.created``
    carried -- see :class:`ResponseCreated`."""

    response: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InputAudioBufferSpeechStarted(ServerEvent):
    """``input_audio_buffer.speech_started`` (PUNCH-LIST #3) -- surfaced from
    the VAD stage's :class:`~anvil_serving.voice.stages.vad.SpeechEvent`
    ``kind="started"`` (see :func:`_dispatch_speech_event`)."""

    item_id: str = ""
    audio_start_ms: int = 0


@dataclass(frozen=True)
class InputAudioBufferSpeechStopped(ServerEvent):
    """``input_audio_buffer.speech_stopped`` (PUNCH-LIST #3) -- surfaced from
    :class:`~anvil_serving.voice.stages.vad.SpeechEvent` ``kind="stopped"``
    (see :func:`_dispatch_speech_event`)."""

    item_id: str = ""
    audio_end_ms: int = 0


@dataclass(frozen=True)
class InputAudioBufferCommitted(ServerEvent):
    """``input_audio_buffer.committed`` (PUNCH-LIST #3) -- this pipeline
    always routes audio through VAD-based (server) turn detection (see
    ``service.py``'s module docstring, honesty note 3), so a completed turn
    is always effectively auto-committed the instant VAD detects end-of-
    speech; emitted paired with :class:`InputAudioBufferSpeechStopped` (same
    ``item_id``, same underlying :class:`~anvil_serving.voice.stages.vad.SpeechEvent`)."""

    item_id: str = ""


@dataclass(frozen=True)
class ConversationItemInputAudioTranscriptionCompleted(ServerEvent):
    """``conversation.item.input_audio_transcription.completed`` (PUNCH-LIST
    #3) -- the final STT transcript for a user turn's audio item. Only a
    ``.completed`` (not the wire protocol's ``.delta``) is modeled: this
    pipeline's dispatch only sees a :class:`~anvil_serving.voice.messages.Transcription`
    once (partial transcripts are dropped -- see :func:`_dispatch_transcription`),
    same deliberately-partial-surface spirit as this module's docstring."""

    item_id: str = ""
    transcript: str = ""


@dataclass(frozen=True)
class ErrorEvent(ServerEvent):
    error: Dict[str, Any] = field(default_factory=dict)


def make_error_event(etype: str, message: str, *, id_source: IdSource = _next_event_id) -> ErrorEvent:
    """Build an ``error`` server event (the minimal error taxonomy this unit
    supports: ``{"type": etype, "message": message}``, not the full Realtime
    error object shape).

    ``id_source`` defaults to this module's own fallback counter; a caller
    that owns a per-connection id source (see :class:`RealtimeService`)
    should pass it here so this event's id comes from the SAME sequence as
    every other event on that connection.
    """
    return ErrorEvent(
        type="error", event_id=id_source(), error={"type": etype, "message": message}
    )


def server_event_to_dict(event: ServerEvent) -> Dict[str, Any]:
    """Serialize a :class:`ServerEvent` to its wire dict (``asdict`` already
    includes ``type``/``event_id`` since they're dataclass fields)."""
    return asdict(event)


# --------------------------------------------------------------------------- #
# internal pipeline message -> server event dispatch table
# --------------------------------------------------------------------------- #

#: One internal message may fan out to zero or more server events (kept a
#: list for future messages that map to more than one wire event). Takes the
#: caller's ``id_source`` so every event it mints draws from that same
#: per-connection sequence rather than this module's own fallback counter,
#: plus the caller's current ``response_id`` (PUNCH-LIST #3 -- ``None`` for
#: message types that don't belong to an assistant response, e.g.
#: :class:`~anvil_serving.voice.stages.vad.SpeechEvent`/:class:`Transcription`,
#: which simply ignore it).
_DispatchFn = Callable[[Any, IdSource, Optional[str]], List[ServerEvent]]


def _dispatch_transcription(msg: Transcription, id_source: IdSource, response_id: Optional[str]) -> List[ServerEvent]:
    # Only a FINAL transcript surfaces on the wire (mirrors this module's
    # already-deliberately-partial-surface stance elsewhere): a real STT
    # stage may stream non-final partials through the same sideband queue
    # (see `stages/stt.py::STTStage.process`), but this unit models only
    # `conversation.item.input_audio_transcription.completed`, not the wire
    # protocol's `.delta` variant (PUNCH-LIST #3, item 3).
    if not msg.is_final:
        return []
    # A completed STT transcript surfaces as BOTH a conversation item (the
    # user's turn, mirroring how the reference server represents a finalized
    # user turn) AND the transcription-completed event carrying the actual
    # text -- real protocol order: item created (transcript pending) THEN
    # transcription completed. This unit only ever sees the transcript once
    # it's already final, so it emits both together, in that order.
    return [
        ConversationItemCreated(
            type="conversation.item.created",
            event_id=id_source(),
            item={
                "id": msg.turn_id,
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": msg.text}],
            },
        ),
        ConversationItemInputAudioTranscriptionCompleted(
            type="conversation.item.input_audio_transcription.completed",
            event_id=id_source(),
            item_id=msg.turn_id,
            transcript=msg.text,
        ),
    ]


def _dispatch_speech_event(msg: SpeechEvent, id_source: IdSource, response_id: Optional[str]) -> List[ServerEvent]:
    if msg.kind == "started":
        return [
            InputAudioBufferSpeechStarted(
                type="input_audio_buffer.speech_started",
                event_id=id_source(),
                item_id=msg.turn_id,
                audio_start_ms=msg.audio_ms,
            )
        ]
    # "stopped" -- this pipeline always drives audio through VAD-based (server)
    # turn detection (see `service.py`'s module docstring, honesty note 3), so
    # end-of-speech IS the commit point: pair `speech_stopped` with
    # `input_audio_buffer.committed` for the same item, matching the real
    # protocol's server-VAD auto-commit behavior.
    return [
        InputAudioBufferSpeechStopped(
            type="input_audio_buffer.speech_stopped",
            event_id=id_source(),
            item_id=msg.turn_id,
            audio_end_ms=msg.audio_ms,
        ),
        InputAudioBufferCommitted(
            type="input_audio_buffer.committed",
            event_id=id_source(),
            item_id=msg.turn_id,
        ),
    ]


def _dispatch_llm_chunk(msg: LLMChunk, id_source: IdSource, response_id: Optional[str]) -> List[ServerEvent]:
    return [
        ResponseAudioTranscriptDelta(
            type="response.output_audio_transcript.delta",
            event_id=id_source(),
            delta=msg.text,
            turn_id=msg.turn_id,
            response_id=response_id or "",
        )
    ]


def _dispatch_audio_out(msg: AudioOut, id_source: IdSource, response_id: Optional[str]) -> List[ServerEvent]:
    import base64

    return [
        ResponseAudioDelta(
            type="response.output_audio.delta",
            event_id=id_source(),
            delta=base64.b64encode(msg.pcm).decode("ascii"),
            turn_id=msg.turn_id,
            response_id=response_id or "",
        )
    ]


def _dispatch_llm_tool_call(msg: LLMToolCall, id_source: IdSource, response_id: Optional[str]) -> List[ServerEvent]:
    item = {
        "id": msg.item_id,
        "type": "function_call",
        "call_id": msg.call_id,
        "name": msg.name,
        "arguments": msg.arguments,
        "status": "completed",
    }
    response = response_id or ""
    return [
        ResponseOutputItemAdded(
            type="response.output_item.added",
            event_id=id_source(),
            response_id=response,
            output_index=msg.output_index,
            item=dict(item),
        ),
        ResponseFunctionCallArgumentsDone(
            type="response.function_call_arguments.done",
            event_id=id_source(),
            response_id=response,
            item_id=msg.item_id,
            output_index=msg.output_index,
            call_id=msg.call_id,
            name=msg.name,
            arguments=msg.arguments,
        ),
        ResponseOutputItemDone(
            type="response.output_item.done",
            event_id=id_source(),
            response_id=response,
            output_index=msg.output_index,
            item=dict(item),
        ),
        ConversationItemDone(
            type="conversation.item.done",
            event_id=id_source(),
            item=dict(item),
        )
    ]


def _dispatch_end_of_response(msg: EndOfResponse, id_source: IdSource, response_id: Optional[str]) -> List[ServerEvent]:
    return [
        ResponseDone(
            type="response.done",
            event_id=id_source(),
            response={"id": response_id or "", "turn_id": msg.turn_id, "status": "completed"},
        )
    ]


DISPATCH: Dict[type, _DispatchFn] = {
    Transcription: _dispatch_transcription,
    SpeechEvent: _dispatch_speech_event,
    LLMChunk: _dispatch_llm_chunk,
    LLMToolCall: _dispatch_llm_tool_call,
    AudioOut: _dispatch_audio_out,
    EndOfResponse: _dispatch_end_of_response,
}


def dispatch_internal_event(
    msg: Any, *, id_source: IdSource = _next_event_id, response_id: Optional[str] = None
) -> List[ServerEvent]:
    """Map one internal pipeline message to zero or more server events.

    Returns an empty list for a message type with no server-event mapping
    (e.g. a bare :class:`~anvil_serving.voice.messages.VADAudio` segment,
    which the ``vad_events`` sideband also carries alongside its paired
    ``SpeechEvent`` -- see ``pipeline.py``'s fan-out note) rather than
    raising, so an unmapped message never crashes the drain loop.

    ``id_source`` defaults to this module's own fallback counter; a caller
    that owns a per-connection id source (see :class:`RealtimeService`)
    should pass it here so every event minted for one connection -- whether
    built here or directly by the caller -- comes from the SAME sequence
    (see the module-level fallback counter's own docstring for why this
    matters: two independently-defaulting callers would otherwise collide).

    ``response_id`` (PUNCH-LIST #3) is the CURRENT response's id -- threaded
    into every ``response.output_audio.delta``/``response.output_audio_transcript.delta``/
    ``response.done`` event this call produces (see :class:`ResponseCreated`'s
    docstring); message types unrelated to an assistant response (``SpeechEvent``,
    ``Transcription``) simply ignore it. ``None`` (the default) renders as an
    empty string on the wire rather than a Python ``None`` leaking into JSON.
    """
    fn = DISPATCH.get(type(msg))
    return fn(msg, id_source, response_id) if fn is not None else []
