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
from typing import Any, Callable, Dict, List, Type

from ..messages import AudioOut, EndOfResponse, LLMChunk, Transcription

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
class ResponseCreated(ServerEvent):
    response: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseAudioTranscriptDelta(ServerEvent):
    """Text delta for the assistant's spoken reply (from an
    :class:`~anvil_serving.voice.messages.LLMChunk`)."""

    delta: str = ""
    turn_id: str = ""


@dataclass(frozen=True)
class ResponseAudioDelta(ServerEvent):
    """Base64-encoded synthesized PCM (from an
    :class:`~anvil_serving.voice.messages.AudioOut`)."""

    delta: str = ""
    turn_id: str = ""


@dataclass(frozen=True)
class ResponseDone(ServerEvent):
    """Terminal event for one turn's response (from an
    :class:`~anvil_serving.voice.messages.EndOfResponse`)."""

    response: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InputAudioBufferSpeechStarted(ServerEvent):
    item_id: str = ""
    audio_start_ms: int = 0


@dataclass(frozen=True)
class InputAudioBufferSpeechStopped(ServerEvent):
    item_id: str = ""
    audio_end_ms: int = 0


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
#: per-connection sequence rather than this module's own fallback counter.
_DispatchFn = Callable[[Any, IdSource], List[ServerEvent]]


def _dispatch_transcription(msg: Transcription, id_source: IdSource) -> List[ServerEvent]:
    # A completed STT transcript surfaces as a conversation item, mirroring
    # how the reference server represents a finalized user turn.
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
        )
    ]


def _dispatch_llm_chunk(msg: LLMChunk, id_source: IdSource) -> List[ServerEvent]:
    return [
        ResponseAudioTranscriptDelta(
            type="response.audio_transcript.delta",
            event_id=id_source(),
            delta=msg.text,
            turn_id=msg.turn_id,
        )
    ]


def _dispatch_audio_out(msg: AudioOut, id_source: IdSource) -> List[ServerEvent]:
    import base64

    return [
        ResponseAudioDelta(
            type="response.audio.delta",
            event_id=id_source(),
            delta=base64.b64encode(msg.pcm).decode("ascii"),
            turn_id=msg.turn_id,
        )
    ]


def _dispatch_end_of_response(msg: EndOfResponse, id_source: IdSource) -> List[ServerEvent]:
    return [
        ResponseDone(
            type="response.done",
            event_id=id_source(),
            response={"turn_id": msg.turn_id, "status": "completed"},
        )
    ]


DISPATCH: Dict[type, _DispatchFn] = {
    Transcription: _dispatch_transcription,
    LLMChunk: _dispatch_llm_chunk,
    AudioOut: _dispatch_audio_out,
    EndOfResponse: _dispatch_end_of_response,
}


def dispatch_internal_event(msg: Any, *, id_source: IdSource = _next_event_id) -> List[ServerEvent]:
    """Map one internal pipeline message to zero or more server events.

    Returns an empty list for a message type with no server-event mapping
    (e.g. :class:`~anvil_serving.voice.stages.vad.SpeechEvent` today has no
    entry -- see ``service.py``'s module docstring for why) rather than
    raising, so an unmapped message never crashes the drain loop.

    ``id_source`` defaults to this module's own fallback counter; a caller
    that owns a per-connection id source (see :class:`RealtimeService`)
    should pass it here so every event minted for one connection -- whether
    built here or directly by the caller -- comes from the SAME sequence
    (see the module-level fallback counter's own docstring for why this
    matters: two independently-defaulting callers would otherwise collide).
    """
    fn = DISPATCH.get(type(msg))
    return fn(msg, id_source) if fn is not None else []
