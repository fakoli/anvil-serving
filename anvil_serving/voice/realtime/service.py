"""Protocol <-> pipeline translator for one Realtime connection (anvil task T012/T013).

:class:`RealtimeService` is the pure translation layer between
:mod:`events` (typed client/server events) and one
:class:`~anvil_serving.voice.pipeline.VoicePipeline` instance. It holds no
socket/transport state itself (``ws.py`` owns the wire; ``pool.py`` owns
which pipeline instance a session gets) -- just per-connection session state
plus the two directions of translation:

* **client event -> pipeline input.** Audio (``input_audio_buffer.*``) is
  buffered locally and, on ``commit``, pushed onto ``pipeline.audio_in`` as
  raw PCM frames -- the SAME entry point ``VoicePipeline``'s own VAD stage
  already consumes (see ``tests/voice/test_pipeline_spine.py``), so audio-
  driven turns get real VAD-based turn-taking for free. Text-only turns
  (``conversation.item.create`` + ``response.create``) skip VAD/STT entirely
  and are bridged STRAIGHT into a :class:`~anvil_serving.voice.messages.GenerateRequest`
  pushed onto ``pipeline.llm.in_queue`` -- this is the "bridge a completed
  transcript into a GenerateRequest" the unit brief calls out, applied to a
  client-supplied (already-final) transcript instead of an STT one.
* **pipeline output -> server event.** :meth:`drain_pipeline_events` pulls
  whatever is currently on ``pipeline.audio_out`` and maps each item through
  :func:`events.dispatch_internal_event`.

HONESTY NOTE / known simplifications (flagged, not hidden):

1. ``input_audio_buffer.commit`` cannot force VAD's silence-based end-of-turn
   deterministically without knowing its ``silence_frames`` threshold, so
   this service pushes :attr:`flush_silence_frames` full-silence frames after
   the buffered audio to trigger it. A real acoustic VAD model may behave
   differently; this is only proven against the deterministic
   :class:`~anvil_serving.voice.stages.vad.FakeVADModel` in tests.
2. ``input_audio_buffer.speech_started``/``speech_stopped`` are DEFINED in
   ``events.py`` (part of the SDK-verified subset) but this service does not
   yet emit them: :class:`~anvil_serving.voice.stages.vad.SpeechEvent` is
   fanned out on the VAD stage's own internal out-queue
   (``vad_to_stt`` in ``pipeline.py``), which ``VoicePipeline`` does not
   expose publicly. Wiring this up is a follow-up (would need
   ``VoicePipeline`` to expose that queue, or a VAD-stage constructor hook to
   also fan out to a "sideband" queue this service reads).
3. Manual (client-driven) turn detection (``session.turn_detection = null``
   in the real Realtime API) is not distinguished from the default
   server-VAD mode -- audio always goes through the pipeline's VAD stage.
"""
from __future__ import annotations

import base64
import itertools
import queue
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..messages import GenerateRequest
from .events import (
    ClientEvent,
    ConversationItemCreate,
    EventParseError,
    InputAudioBufferAppend,
    InputAudioBufferClear,
    InputAudioBufferCommit,
    ResponseCancel,
    ResponseCreate,
    SessionUpdate,
    dispatch_internal_event,
    make_error_event,
    parse_client_event,
    server_event_to_dict,
)

#: Bytes per audio frame handed to the pipeline's VAD stage on commit. 16kHz
#: mono 16-bit PCM @ 20ms = 640 bytes; matches ``VADConfig``'s default
#: ``frame_ms=20`` (see ``stages/vad.py``) so a real detector sees frame
#: durations it was tuned for.
DEFAULT_FRAME_BYTES = 640

#: Trailing full-silence frames pushed after committed audio to force VAD's
#: silence-threshold end-of-turn deterministically (see class docstring,
#: honesty note 1). ``VADConfig``'s default ``silence_frames`` is
#: ``round(200/20) == 10``; 12 gives headroom without depending on the exact
#: configured threshold.
DEFAULT_FLUSH_SILENCE_FRAMES = 12


@dataclass
class SessionState:
    """Per-connection state :class:`RealtimeService` mutates as events arrive."""

    session_id: str
    session_config: Dict[str, Any] = field(default_factory=dict)
    pending_text: List[str] = field(default_factory=list)
    turn_counter: "itertools.count" = field(default_factory=lambda: itertools.count(1))
    #: ``turn_id`` of the response currently in flight (set by
    #: ``response.create``, cleared once a terminal ``response.done`` has
    #: been sent for it -- either normal completion via ``drain_pipeline_events``
    #: or a ``response.cancel`` interruption). ``None`` when no response is
    #: in progress, which also guards ``_on_response_cancel`` against
    #: emitting a spurious terminal event with no matching ``response.created``.
    current_turn_id: Optional[str] = None


class RealtimeService:
    """Translates Realtime client events against one ``VoicePipeline``.

    ``pipeline`` must expose ``audio_in`` (a ``queue.Queue`` of raw PCM
    frames), ``audio_out`` (a ``queue.Queue`` of
    :class:`~anvil_serving.voice.messages.AudioOut`/``EndOfResponse``),
    ``cancel_scope`` (a :class:`~anvil_serving.voice.cancel_scope.CancelScope`),
    and ``llm.in_queue`` (the queue :class:`~anvil_serving.voice.stages.llm.LLMStage`
    reads :class:`~anvil_serving.voice.messages.GenerateRequest` from) --
    exactly the shape of :class:`~anvil_serving.voice.pipeline.VoicePipeline`.
    ``send_event`` is called with each outgoing wire dict (a caller wires
    this to ``conn.send_json`` from ``ws.py``); kept as a plain callable so
    this class never imports the transport layer, and so tests can pass a
    list-appending fake.
    """

    def __init__(
        self,
        *,
        pipeline: Any,
        send_event: Callable[[Dict[str, Any]], None],
        session_id: str,
        frame_bytes: int = DEFAULT_FRAME_BYTES,
        flush_silence_frames: int = DEFAULT_FLUSH_SILENCE_FRAMES,
    ) -> None:
        self.pipeline = pipeline
        self.send_event = send_event
        self.state = SessionState(session_id=session_id)
        self.frame_bytes = frame_bytes
        self.flush_silence_frames = flush_silence_frames
        self._audio_buffer = bytearray()
        # ONE id source, owned by THIS instance (i.e. per-connection, since
        # one RealtimeService == one connection) -- shared between the
        # events this class mints directly below (session.updated,
        # conversation.item.created, response.created) and the ones
        # events.py's dispatch_internal_event/make_error_event mint on its
        # behalf, so every event_id on one connection's wire log is unique,
        # matching ServerEvent's own "per-connection monotonic counter"
        # docstring claim (previously each side had its OWN counter starting
        # at 1, so two events from the same connection could collide).
        self._evt_counter = itertools.count(1)

        self._handlers: Dict[type, Callable[[ClientEvent], None]] = {
            SessionUpdate: self._on_session_update,
            InputAudioBufferAppend: self._on_audio_append,
            InputAudioBufferCommit: self._on_audio_commit,
            InputAudioBufferClear: self._on_audio_clear,
            ConversationItemCreate: self._on_item_create,
            ResponseCreate: self._on_response_create,
            ResponseCancel: self._on_response_cancel,
        }

    # -- inbound: raw wire text -> typed event -> pipeline side effect --------
    def handle_client_message(self, raw_text: str) -> None:
        """Parse one raw (JSON-text) client message and dispatch it.

        Never raises: a malformed/unknown event is answered with an
        ``error`` server event instead (mirrors the real Realtime API, which
        never tears down the connection over one bad client event).
        """
        import json

        try:
            raw = json.loads(raw_text)
        except (ValueError, TypeError) as exc:
            self._send_error("invalid_json", "could not parse client message as JSON: %s" % exc)
            return
        try:
            event = parse_client_event(raw)
        except EventParseError as exc:
            self._send_error("invalid_request", str(exc))
            return
        self.handle_client_event(event)

    def handle_client_event(self, event: ClientEvent) -> None:
        handler = self._handlers.get(type(event))
        if handler is None:  # pragma: no cover - every parseable type has a handler
            self._send_error("unsupported_event", "no handler for %r" % event.type)
            return
        handler(event)

    def _evt_id(self) -> str:
        """This connection's own next event id (shared with events.py's
        dispatch table -- see the ONE id source note in ``__init__``)."""
        return "evt_%d" % next(self._evt_counter)

    def _send_error(self, etype: str, message: str) -> None:
        self.send_event(server_event_to_dict(make_error_event(etype, message, id_source=self._evt_id)))

    # -- individual client-event handlers --------------------------------------
    def _on_session_update(self, event: SessionUpdate) -> None:
        self.state.session_config.update(event.session)
        self.send_event(
            {"type": "session.updated", "event_id": self._evt_id(), "session": dict(self.state.session_config)}
        )

    def _on_audio_append(self, event: InputAudioBufferAppend) -> None:
        try:
            self._audio_buffer += base64.b64decode(event.audio, validate=True)
        except Exception as exc:  # noqa: BLE001 - any malformed base64 is a client error, not a crash
            self._send_error("invalid_request", "input_audio_buffer.append: bad base64 audio: %s" % exc)

    def _on_audio_clear(self, event: InputAudioBufferClear) -> None:
        # Buffered-but-uncommitted audio never reached the pipeline (see class
        # docstring), so clearing it here is exact -- no partial state to undo
        # downstream.
        self._audio_buffer = bytearray()

    def _on_audio_commit(self, event: InputAudioBufferCommit) -> None:
        if not self._audio_buffer:
            self._send_error("invalid_request", "input_audio_buffer.commit: buffer is empty")
            return
        pcm = bytes(self._audio_buffer)
        self._audio_buffer = bytearray()
        for offset in range(0, len(pcm), self.frame_bytes):
            self.pipeline.audio_in.put(pcm[offset : offset + self.frame_bytes])
        silence_frame = b"\x00" * self.frame_bytes
        for _ in range(self.flush_silence_frames):
            self.pipeline.audio_in.put(silence_frame)

    def _on_item_create(self, event: ConversationItemCreate) -> None:
        item = event.item or {}
        text = _extract_text(item)
        if text:
            self.state.pending_text.append(text)
        item_id = item.get("id") or "item_%d" % next(self.state.turn_counter)
        self.send_event(
            {
                "type": "conversation.item.created",
                "event_id": self._evt_id(),
                "item": {**item, "id": item_id},
            }
        )

    def _on_response_create(self, event: ResponseCreate) -> None:
        if not self.state.pending_text:
            self._send_error("invalid_request", "response.create: no pending input to respond to")
            return
        text = "\n".join(self.state.pending_text)
        self.state.pending_text = []
        turn_id = "rt-turn-%d" % next(self.state.turn_counter)
        self.state.current_turn_id = turn_id
        generation = self.pipeline.cancel_scope.begin_new_generation()
        request = GenerateRequest(turn_id=turn_id, turn_revision=0, generation=generation, text=text)
        # Bridges the client-supplied (already-final) transcript straight
        # into the LLM stage's own input queue -- skipping VAD/STT entirely,
        # exactly mirroring what ``pipeline.py``'s TranscriptionToGenerate
        # bridge does for an audio-driven turn.
        self.pipeline.llm.in_queue.put(request)
        self.send_event(
            {"type": "response.created", "event_id": self._evt_id(), "response": {"turn_id": turn_id, "status": "in_progress"}}
        )

    def _on_response_cancel(self, event: ResponseCancel) -> None:
        # Barge-in: bump the shared generation counter so every stage still
        # working on the now-superseded turn recognizes it as stale (see
        # ``cancel_scope.py``) and stops emitting further output for it.
        self.pipeline.cancel_scope.cancel()
        # B2 fix: a cancelled turn must still terminate on the wire. Before
        # this fix, termination relied on the (now-superseded) turn's own
        # ``EndOfResponse`` reaching ``drain_pipeline_events`` -- but B1's
        # staleness guard means that item is now correctly DROPPED as stale,
        # so without an explicit terminal event here a client that saw
        # ``response.created`` would simply never see a matching
        # ``response.done`` for an interrupted turn. Emit it directly, using
        # this connection's own event-id source (see ``_evt_id``'s docstring
        # for why every event on one connection must share one id sequence).
        turn_id = self.state.current_turn_id
        if turn_id is None:
            # No response is in flight (already completed, or cancel with
            # nothing pending) -- nothing to terminate, and there is no
            # matching ``response.created`` to pair a ``response.done``
            # against, so stay silent (avoids a spurious extra terminal
            # event and keeps "exactly one response.done per response.created"
            # true).
            return
        self.state.current_turn_id = None
        self.send_event(
            {
                "type": "response.done",
                "event_id": self._evt_id(),
                "response": {"turn_id": turn_id, "status": "cancelled"},
            }
        )

    # -- outbound: pipeline output -> server events ----------------------------
    def drain_pipeline_events(self, *, max_items: Optional[int] = None) -> List[Dict[str, Any]]:
        """Drain whatever is CURRENTLY buffered on ``pipeline.audio_out``,
        mapping each item through :func:`events.dispatch_internal_event`.

        Non-blocking: returns as soon as the queue reports empty (does not
        wait for more items to arrive), so it is safe to call in a tight poll
        loop from a connection's background thread. Returns the wire dicts in
        arrival order; does NOT call ``send_event`` itself -- callers decide
        whether to loop-and-send (as ``pool.py``'s session-drive loop does) or
        just collect (as tests do).

        B1 fix -- staleness guard: a ``response.cancel`` bumps
        ``pipeline.cancel_scope``'s generation, but items produced by the
        now-superseded turn (e.g. an ``AudioOut`` synthesized before the
        cancel landed) may still be sitting in ``audio_out`` when this drains
        it. Every per-generation-tagged item (``AudioOut``, ``LLMChunk``,
        ``EndOfResponse``, ``Transcription`` -- anything carrying
        ``.generation``) is dropped here if it predates the CURRENT
        generation, so no stale audio/text from a superseded reply ever
        reaches the client. (A stale ``EndOfResponse`` is intentionally
        dropped too -- ``_on_response_cancel`` emits its own terminal
        ``response.done`` for the cancelled turn instead, see B2.)
        """
        out: List[Dict[str, Any]] = []
        count = 0
        while max_items is None or count < max_items:
            try:
                item = self.pipeline.audio_out.get_nowait()
            except queue.Empty:
                break
            count += 1
            gen = getattr(item, "generation", None)
            if gen is not None and self.pipeline.cancel_scope.is_stale(gen):
                continue
            for server_event in dispatch_internal_event(item, id_source=self._evt_id):
                out.append(server_event_to_dict(server_event))
                if server_event.type == "response.done":
                    # Normal completion path: mirror the bookkeeping
                    # ``_on_response_cancel`` does, so a later cancel with
                    # nothing left in flight is correctly a no-op instead of
                    # emitting a second, spurious terminal event.
                    self.state.current_turn_id = None
        return out


def _extract_text(item: Dict[str, Any]) -> Optional[str]:
    """Pull the user-visible text out of a ``conversation.item.create``
    ``item`` payload: concatenates every ``input_text``/``text`` content part.
    """
    content = item.get("content")
    if not isinstance(content, list):
        return None
    parts = []
    for part in content:
        if isinstance(part, dict) and part.get("type") in ("input_text", "text"):
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts) if parts else None
