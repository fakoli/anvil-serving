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
  whatever is currently on ``pipeline.vad_events``/``pipeline.transcript_events``/
  ``pipeline.audio_out`` and maps each item through
  :func:`events.dispatch_internal_event`.

HONESTY NOTE / known simplifications (flagged, not hidden):

1. ``input_audio_buffer.commit`` cannot force VAD's silence-based end-of-turn
   deterministically without knowing its ``silence_frames`` threshold, so
   this service pushes :attr:`flush_silence_frames` full-silence frames after
   the buffered audio to trigger it. A real acoustic VAD model may behave
   differently; this is only proven against the deterministic
   :class:`~anvil_serving.voice.stages.vad.FakeVADModel` in tests.
2. **RESOLVED (PUNCH-LIST #3).** ``input_audio_buffer.speech_started``/
   ``speech_stopped``/``committed``, the user-turn ``conversation.item.created``/
   ``conversation.item.input_audio_transcription.completed``, and a real
   per-response ``response.id`` are now emitted -- see
   :meth:`drain_pipeline_events` (which now also drains
   ``pipeline.vad_events``/``pipeline.transcript_events``, the sideband queues
   ``pipeline.py`` fans VAD's ``SpeechEvent`` and STT's ``Transcription`` out
   to) and :meth:`_begin_response` (mints the response id, used by BOTH the
   client-driven text path and the audio path's auto-response-on-completed-
   transcription).
3. Manual (client-driven) turn detection (``session.turn_detection = null``
   in the real Realtime API) is not distinguished from the default
   server-VAD mode -- audio always goes through the pipeline's VAD stage, so
   every completed turn is treated as an auto-committed, auto-responded
   server-VAD turn (see :meth:`drain_pipeline_events`'s ``Transcription``
   handling).
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..messages import GenerateRequest, Transcription
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

#: F5 fix -- backpressure cap on ``pipeline.audio_in``. A ``queue.Queue()``
#: (``VoicePipeline``'s default) has no ``maxsize``, so a client committing
#: audio faster than the VAD stage consumes it grows the queue without limit.
#: Rather than give the queue itself a blocking ``maxsize`` (a blocking
#: ``put()`` on a full queue would wedge THIS connection's own thread --
#: exactly the "block the WS thread forever" failure mode the fix must
#: avoid), :meth:`RealtimeService._enqueue_audio_frame` enforces this cap
#: itself with a non-blocking, deterministic DROP-OLDEST policy: once the
#: queue is at capacity, the oldest buffered frame is discarded to make room
#: for the new one. Dropping the oldest (not the newest) keeps the tail of a
#: committed utterance -- the audio VAD/STT need to actually reach a final
#: transcript -- rather than truncating it. Default is 500 frames (
#: ``DEFAULT_FRAME_BYTES`` * 500 == 10s of buffered 20ms audio at 16kHz mono
#: 16-bit), comfortably above real-time commit bursts.
DEFAULT_MAX_AUDIO_IN_QUEUE = int(os.environ.get("ANVIL_VOICE_MAX_AUDIO_IN_QUEUE", "500"))
MINI_OWNER = "mini"
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class RealtimeProxyState:
    """Typed state for the Mini-owned Realtime listener."""

    owner: str
    running: bool
    host: str
    port: int
    started_at: Optional[float]
    stopping: bool = False
    close_error: Optional[BaseException] = None


@dataclass(frozen=True)
class RealtimeProxyLogs:
    """Typed empty output snapshot for the in-process proxy lifecycle."""

    lines: tuple[str, ...] = ()


class RealtimeProxyStopTimeoutError(TimeoutError):
    """Raised when a Realtime proxy runner does not stop in time."""


@dataclass
class _ShutdownRequest:
    """One shutdown attempt shared by concurrent stop callers."""

    done: threading.Event = field(default_factory=threading.Event)
    error: Optional[BaseException] = None


class RealtimeProxyService:
    """Bounded lifecycle owner for an already-constructed Realtime server.

    The caller supplies the server factory so this module remains a protocol
    lifecycle seam.  It does not construct STT/TTS serves, import their
    lifecycle modules, or invoke their handlers.
    """

    def __init__(
        self,
        server_factory: Callable[[], Any],
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        owner: str = MINI_OWNER,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if owner != MINI_OWNER:
            raise ValueError("realtime proxy owner must be %r" % MINI_OWNER)
        if host != "127.0.0.1":
            raise ValueError("Mini realtime proxy must bind 127.0.0.1")
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            raise ValueError("port must be an integer from 1 through 65535")
        self._server_factory = server_factory
        self._host = host
        self._port = port
        self._owner = owner
        self._clock = clock
        self._lock = threading.Lock()
        self._server: Any = None
        self._thread: Optional[threading.Thread] = None
        self._runner: Optional[threading.Thread] = None
        self._started_at: Optional[float] = None
        self._running = False
        self._stopping = False
        self._close_error: Optional[BaseException] = None
        self._shutdown_runner: Optional[threading.Thread] = None
        self._shutdown_request: Optional[_ShutdownRequest] = None

    def _state_locked(self) -> RealtimeProxyState:
        return RealtimeProxyState(
            self._owner,
            self._running,
            self._host,
            self._port,
            self._started_at,
            self._stopping,
            self._close_error,
        )

    def _serve_registered(self, server: Any) -> RealtimeProxyState:
        runner = threading.current_thread()
        try:
            server.serve_forever()
        finally:
            close_error = None
            close = getattr(server, "server_close", None)
            if callable(close):
                try:
                    close()
                except BaseException as exc:
                    close_error = exc
            with self._lock:
                if self._runner is runner:
                    self._server = None
                    self._thread = None
                    self._runner = None
                    self._started_at = None
                    self._running = False
                    self._stopping = False
                    self._close_error = close_error
                    self._shutdown_runner = None
                    self._shutdown_request = None
        return self.status()

    def run(self) -> RealtimeProxyState:
        """Serve in the calling thread until the server is shut down."""
        with self._lock:
            if self._running:
                raise RuntimeError("realtime proxy is already running")
            server = self._server_factory()
            self._server = server
            self._runner = threading.current_thread()
            self._started_at = self._clock()
            self._running = True
            self._stopping = False
            self._close_error = None
        return self._serve_registered(server)

    def start(self) -> RealtimeProxyState:
        with self._lock:
            if self._running:
                return self._state_locked()
            server = self._server_factory()
            thread = threading.Thread(
                target=self._serve_registered,
                args=(server,),
                name="anvil-voice-realtime",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            self._runner = thread
            self._started_at = self._clock()
            self._running = True
            self._stopping = False
            self._close_error = None
            thread.start()
            return self._state_locked()

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> RealtimeProxyState:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + timeout
        with self._lock:
            server, runner = self._server, self._runner
            self._stopping = self._running
            request = self._shutdown_request if self._shutdown_runner is runner else None
            shutdown = getattr(server, "shutdown", None)
            if request is None and callable(shutdown):
                request = _ShutdownRequest()

                def request_shutdown() -> None:
                    try:
                        shutdown()
                    except BaseException as exc:
                        request.error = exc
                    finally:
                        request.done.set()

                self._shutdown_runner = runner
                self._shutdown_request = request
                threading.Thread(
                    target=request_shutdown,
                    name="anvil-voice-realtime-shutdown",
                    daemon=True,
                ).start()
        if request is not None:
            request.done.wait(timeout=max(0.0, deadline - time.monotonic()))
            if not request.done.is_set():
                raise RealtimeProxyStopTimeoutError(
                    "realtime proxy did not stop within %.3f seconds" % timeout
                )
            if request.error is not None:
                raise request.error
        if runner is not None and runner is not threading.current_thread():
            runner.join(timeout=max(0.0, deadline - time.monotonic()))
            if runner.is_alive():
                raise RealtimeProxyStopTimeoutError(
                    "realtime proxy did not stop within %.3f seconds" % timeout
                )
        return self.status()

    def restart(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> RealtimeProxyState:
        self.stop(timeout=timeout)
        return self.start()

    def status(self) -> RealtimeProxyState:
        with self._lock:
            return self._state_locked()

    def logs(self) -> RealtimeProxyLogs:
        """Lifecycle has no subprocess output; expose a typed empty snapshot."""
        return RealtimeProxyLogs()


@dataclass
class SessionState:
    """Per-connection state :class:`RealtimeService` mutates as events arrive."""

    session_id: str
    session_config: Dict[str, Any] = field(default_factory=dict)
    pending_text: List[str] = field(default_factory=list)
    turn_counter: "itertools.count" = field(default_factory=lambda: itertools.count(1))
    #: ``turn_id`` of the response currently in flight (set by
    #: ``response.create`` -- or, for an audio-driven turn, by
    #: :meth:`RealtimeService._begin_response` once the completed
    #: transcription auto-triggers a response, see ``drain_pipeline_events`` --
    #: cleared once a terminal ``response.done`` has been sent for it, either
    #: normal completion via ``drain_pipeline_events`` or a ``response.cancel``
    #: interruption). ``None`` when no response is in progress, which also
    #: guards ``_on_response_cancel`` against emitting a spurious terminal
    #: event with no matching ``response.created``.
    current_turn_id: Optional[str] = None
    #: The REAL, unique ``response.id`` (PUNCH-LIST #3) for the response
    #: ``current_turn_id`` identifies -- minted once by
    #: :meth:`RealtimeService._begin_response` at ``response.created`` and
    #: threaded through every later delta/done event for the SAME response
    #: (see ``drain_pipeline_events``). Cleared in lockstep with
    #: ``current_turn_id``.
    current_response_id: Optional[str] = None


class RealtimeService:
    """Translates Realtime client events against one ``VoicePipeline``.

    ``pipeline`` must expose ``audio_in`` (a ``queue.Queue`` of raw PCM
    frames), ``audio_out`` (a ``queue.Queue`` of
    :class:`~anvil_serving.voice.messages.AudioOut`/``EndOfResponse``),
    ``vad_events``/``transcript_events`` (the PUNCH-LIST #3 sideband queues
    :meth:`drain_pipeline_events` also drains -- see ``pipeline.py``'s class
    docstring; only needed if a caller actually calls that method),
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
        max_audio_in_queue: int = DEFAULT_MAX_AUDIO_IN_QUEUE,
    ) -> None:
        self.pipeline = pipeline
        self.send_event = send_event
        self.state = SessionState(session_id=session_id)
        self.frame_bytes = frame_bytes
        self.flush_silence_frames = flush_silence_frames
        #: F5 fix: bounds ``pipeline.audio_in`` via drop-oldest (see
        #: DEFAULT_MAX_AUDIO_IN_QUEUE / _enqueue_audio_frame), not a blocking
        #: queue maxsize -- a fast committer must never be able to block this
        #: connection's own thread.
        self.max_audio_in_queue = max_audio_in_queue
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
        #: Mints ``response.id`` values (PUNCH-LIST #3) -- a SEPARATE
        #: sequence from ``_evt_counter``/``state.turn_counter`` (distinct
        #: id-space, so a response id is never confused with an
        #: ``evt_N``/``turn-N``/``item_N`` id even though they're all small
        #: monotonic integers under the hood). Per-connection, like every
        #: other id source this class owns.
        self._response_id_counter = itertools.count(1)

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
        self.send_event(
            server_event_to_dict(make_error_event(etype, message, id_source=self._evt_id))
        )

    def _begin_response(self, turn_id: str) -> Dict[str, Any]:
        """Mint a fresh, unique ``response.id`` and build the ``response.created``
        wire event for it (PUNCH-LIST #3).

        The ONE place a response id is minted, used by BOTH triggers this
        service has for starting a response:

        * :meth:`_on_response_create` -- the client-driven text path
          (explicit ``response.create``).
        * :meth:`drain_pipeline_events` -- the audio path, where a completed
          :class:`~anvil_serving.voice.messages.Transcription` auto-triggers a
          response (the pipeline already pushed the ``GenerateRequest`` before
          this service ever sees the transcript -- see that method's own
          docstring) with no explicit client ``response.create`` at all,
          mirroring the real Realtime API's server-VAD auto-response default.

        Sets ``state.current_turn_id``/``current_response_id`` so later
        events for this response (deltas, the terminal ``response.done``,
        and a ``response.cancel`` barge-in) all thread the SAME id -- see
        :meth:`_on_response_cancel` and :meth:`drain_pipeline_events`.
        """
        response_id = "resp_%d" % next(self._response_id_counter)
        self.state.current_turn_id = turn_id
        self.state.current_response_id = response_id
        return {
            "type": "response.created",
            "event_id": self._evt_id(),
            "response": {"id": response_id, "turn_id": turn_id, "status": "in_progress"},
        }

    # -- individual client-event handlers --------------------------------------
    def _on_session_update(self, event: SessionUpdate) -> None:
        self.state.session_config.update(event.session)
        configure = getattr(getattr(self.pipeline, "llm", None), "configure_realtime_session", None)
        if callable(configure):
            configure(self.state.session_config)
        self.send_event(
            {
                "type": "session.updated",
                "event_id": self._evt_id(),
                "session": dict(self.state.session_config),
            }
        )

    def _on_audio_append(self, event: InputAudioBufferAppend) -> None:
        try:
            self._audio_buffer += base64.b64decode(event.audio, validate=True)
        except Exception as exc:  # noqa: BLE001 - any malformed base64 is a client error, not a crash
            self._send_error(
                "invalid_request", "input_audio_buffer.append: bad base64 audio: %s" % exc
            )

    def _on_audio_clear(self, event: InputAudioBufferClear) -> None:
        # Buffered-but-uncommitted audio never reached the pipeline (see class
        # docstring), so clearing it here is exact -- no partial state to undo
        # downstream.
        self._audio_buffer = bytearray()

    def _enqueue_audio_frame(self, frame: bytes) -> None:
        """Push one PCM frame onto ``pipeline.audio_in``, enforcing
        ``max_audio_in_queue`` with a non-blocking DROP-OLDEST policy (F5
        fix -- see ``DEFAULT_MAX_AUDIO_IN_QUEUE``'s module-level note).

        Never blocks: uses ``get_nowait``/``put_nowait`` only, so a client
        committing audio far faster than the VAD stage consumes it can never
        wedge this connection's own thread waiting on a full queue.
        """
        q = self.pipeline.audio_in
        while q.qsize() >= self.max_audio_in_queue:
            try:
                q.get_nowait()
            except queue.Empty:
                break
        q.put_nowait(frame)

    def _on_audio_commit(self, event: InputAudioBufferCommit) -> None:
        if not self._audio_buffer:
            self._send_error("invalid_request", "input_audio_buffer.commit: buffer is empty")
            return
        pcm = bytes(self._audio_buffer)
        self._audio_buffer = bytearray()
        for offset in range(0, len(pcm), self.frame_bytes):
            self._enqueue_audio_frame(pcm[offset : offset + self.frame_bytes])
        silence_frame = b"\x00" * self.frame_bytes
        for _ in range(self.flush_silence_frames):
            self._enqueue_audio_frame(silence_frame)

    def _on_item_create(self, event: ConversationItemCreate) -> None:
        item = event.item or {}
        if item.get("type") == "function_call_output":
            submitted = self._submit_tool_output(item)
            if not submitted:
                self._send_error(
                    "invalid_request", "conversation.item.create: malformed function_call_output"
                )
                return
        else:
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

    def _submit_tool_output(self, item: Dict[str, Any]) -> bool:
        call_id = item.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return False
        output = item.get("output")
        if isinstance(output, str):
            text = output
        else:
            text = json.dumps(output if output is not None else {}, separators=(",", ":"))
        submit = getattr(getattr(self.pipeline, "llm", None), "submit_tool_result", None)
        if not callable(submit):
            return False
        return bool(
            submit(
                call_id,
                text,
                will_continue=item.get("will_continue") is True,
                suppress_response=item.get("suppress_response") is True,
            )
        )

    def _on_response_create(self, event: ResponseCreate) -> None:
        if not self.state.pending_text:
            self._send_error("invalid_request", "response.create: no pending input to respond to")
            return
        configure = getattr(
            getattr(self.pipeline, "llm", None), "configure_realtime_response", None
        )
        if callable(configure):
            configure(
                self.state.session_config,
                event.response if isinstance(event.response, dict) else {},
            )
        text = "\n".join(self.state.pending_text)
        self.state.pending_text = []
        turn_id = "rt-turn-%d" % next(self.state.turn_counter)
        generation = self.pipeline.cancel_scope.begin_new_generation()
        request = GenerateRequest(
            turn_id=turn_id, turn_revision=0, generation=generation, text=text
        )
        # Bridges the client-supplied (already-final) transcript straight
        # into the LLM stage's own input queue -- skipping VAD/STT entirely,
        # exactly mirroring what ``pipeline.py``'s TranscriptionToGenerate
        # bridge does for an audio-driven turn.
        self.pipeline.llm.in_queue.put(request)
        self.send_event(self._begin_response(turn_id))

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
        response_id = self.state.current_response_id
        self.state.current_turn_id = None
        self.state.current_response_id = None
        self.send_event(
            {
                "type": "response.done",
                "event_id": self._evt_id(),
                "response": {"id": response_id or "", "turn_id": turn_id, "status": "cancelled"},
            }
        )

    def mark_response_done_sent(self, response_id: Optional[str] = None) -> None:
        """Clear in-flight response bookkeeping once its terminal event is sent.

        ``drain_pipeline_events`` builds outbound events but does not own the
        transport. Clearing ``current_turn_id`` while a completed
        ``response.done`` is only queued (not yet written to the socket) lets a
        concurrent ``response.cancel`` vanish: the client saw
        ``response.created``, but cancellation finds no active response and the
        queued completed terminal may then be pruned. The transport owner calls
        this after it actually writes a terminal ``response.done``.
        """
        if (
            response_id
            and self.state.current_response_id
            and response_id != self.state.current_response_id
        ):
            return
        self.state.current_turn_id = None
        self.state.current_response_id = None

    # -- outbound: pipeline output -> server events ----------------------------
    def drain_pipeline_events(self, *, max_items: Optional[int] = None) -> List[Dict[str, Any]]:
        """Drain whatever is CURRENTLY buffered on ``pipeline.vad_events``,
        ``pipeline.transcript_events``, and ``pipeline.audio_out`` (in that
        order -- see below), mapping each item through
        :func:`events.dispatch_internal_event`.

        Non-blocking: returns as soon as every queue reports empty (does not
        wait for more items to arrive), so it is safe to call in a tight poll
        loop from a connection's background thread. Returns the wire dicts in
        arrival order; does NOT call ``send_event`` itself -- callers decide
        whether to loop-and-send (as ``pool.py``'s session-drive loop does) or
        just collect (as tests do).

        PUNCH-LIST #3 -- three sideband/bookkeeping queues, drained in THIS
        order, on purpose: ``vad_events`` (VAD's own ``SpeechEvent`` --
        started/stopped/committed) always causally PRECEDES anything the STT
        stage could have produced from the ``VADAudio`` segment VAD just
        flushed, which in turn always PRECEDES anything the LLM/TTS stages
        downstream of a completed transcript could have produced. Draining in
        this fixed order (fully draining each queue before moving to the
        next, rather than interleaving) means that whatever a SINGLE call
        drains is already in the right relative order across queues, even
        though each queue is filled by its own independent stage thread --
        because within one call, an event that reached a LATER queue can only
        have been produced by something already visible in an EARLIER queue.
        ``pipeline.py``'s class docstring covers why these are safe,
        non-destructive fan-out duplicates of the primary in-pipeline queues.

        For a ``Transcription`` (audio path), the pipeline itself already
        auto-bridged it into a ``GenerateRequest`` for the LLM stage (see
        ``pipeline.py``'s ``TranscriptionToGenerate``) -- there was never a
        client ``response.create`` for this turn. So right after emitting
        that transcript's own wire events (``conversation.item.created`` +
        ``...input_audio_transcription.completed``), this method ALSO mints
        the ``response.created`` for the response the pipeline is already
        generating (see :meth:`_begin_response`) -- giving the audio path the
        same real, unique ``response.id`` guarantee the text path gets from
        an explicit ``response.create``.

        B1 fix -- staleness guard: a ``response.cancel`` bumps
        ``pipeline.cancel_scope``'s generation, but items produced by the
        now-superseded turn (e.g. an ``AudioOut`` synthesized before the
        cancel landed) may still be sitting in a queue when this drains it.
        Every per-generation-tagged item (``AudioOut``, ``LLMChunk``,
        ``EndOfResponse``, ``Transcription``, ``SpeechEvent`` -- anything
        carrying ``.generation``) is dropped here if it predates the CURRENT
        generation, so no stale audio/text from a superseded reply ever
        reaches the client. (A stale ``EndOfResponse`` is intentionally
        dropped too -- ``_on_response_cancel`` emits its own terminal
        ``response.done`` for the cancelled turn instead, see B2.)
        """
        out: List[Dict[str, Any]] = []
        count = 0
        for q in (
            self.pipeline.vad_events,
            self.pipeline.transcript_events,
            self.pipeline.audio_out,
        ):
            while max_items is None or count < max_items:
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    break
                count += 1
                gen = getattr(item, "generation", None)
                if gen is not None and self.pipeline.cancel_scope.is_stale(gen):
                    continue
                for server_event in dispatch_internal_event(
                    item, id_source=self._evt_id, response_id=self.state.current_response_id
                ):
                    out.append(server_event_to_dict(server_event))
                if isinstance(item, Transcription) and item.is_final:
                    out.append(self._begin_response(item.turn_id))
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
