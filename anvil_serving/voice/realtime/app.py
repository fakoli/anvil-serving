"""Package-owned Realtime server assembly for ``anvil-serving voice proxy run``.

This module wires the pure transport/protocol pieces together:

* :class:`~anvil_serving.voice.realtime.pool.SessionPool`
* :class:`~anvil_serving.voice.realtime.service.RealtimeService`
* :func:`~anvil_serving.voice.realtime.ws.make_ws_server`
* :func:`~anvil_serving.voice.pipeline.real_pipeline_factory_from_manifest`

It deliberately stays stdlib-only. The optional official OpenAI SDK client
used by ``scripts/voice/realtime_sdk_client_demo.py`` belongs in that script,
not in this package module.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any, Dict, Mapping, Optional, Tuple

from ..pipeline import real_pipeline_factory_from_manifest
from .pool import SessionPool
from .service import RealtimeService
from .ws import make_ws_server

DEFAULT_POOL_SIZE = 4
SENDER_POLL_INTERVAL_S = 0.05
RESPONSE_OUTPUT_YIELD_S = 0.01
_RESPONSE_OUTPUT_EVENT_TYPES = {
    "response.output_audio.delta",
    "response.output_audio_transcript.delta",
    "response.output_audio_transcript.done",
    "error",
    "response.done",
}


def _event_response_id(event: Mapping[str, Any]) -> Optional[str]:
    response = event.get("response")
    if isinstance(response, Mapping):
        rid = response.get("id")
        return rid if isinstance(rid, str) else None
    rid = event.get("response_id")
    return rid if isinstance(rid, str) else None


def _is_cancelled_done(event: Mapping[str, Any]) -> bool:
    response = event.get("response")
    return (
        event.get("type") == "response.done"
        and isinstance(response, Mapping)
        and response.get("status") == "cancelled"
    )


def _drop_when_cancelled(event: Mapping[str, Any], response_id: str) -> bool:
    if _event_response_id(event) != response_id:
        return False
    if _is_cancelled_done(event):
        return False
    return event.get("type") in _RESPONSE_OUTPUT_EVENT_TYPES


class _OutboundEvents:
    """One ordered outbound queue for direct service events + pipeline drains."""

    def __init__(self) -> None:
        self._events: "deque[Dict[str, Any]]" = deque()
        self._cv = threading.Condition()
        self._closed = False

    def enqueue(self, event: Mapping[str, Any]) -> None:
        item = dict(event)
        response_id = _event_response_id(item)
        with self._cv:
            if response_id and _is_cancelled_done(item):
                self._events = deque(
                    queued for queued in self._events if not _drop_when_cancelled(queued, response_id)
                )
            self._events.append(item)
            self._cv.notify()

    def pop(self, *, timeout: float) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + timeout
        with self._cv:
            while not self._closed and not self._events:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cv.wait(remaining)
            if self._events:
                return self._events.popleft()
            return None

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()


def build_realtime_server_from_manifest(
    data: Mapping[str, Any],
    voice: Optional[Mapping[str, Any]] = None,
    *,
    pool_size: Optional[int] = None,
    session_id_prefix: str = "voice-run",
    sender_thread_name_prefix: str = "voice-run-sender",
) -> Tuple[Any, SessionPool]:
    """Build a configured Realtime WebSocket server and backing session pool.

    ``data`` must be a loaded+validated voice manifest. ``voice`` may be a
    caller-adjusted copy of ``data["voice"]`` (for example, a proof harness
    can override ``realtime_port`` to bind an ephemeral port while preserving
    all STT/LLM/TTS configuration).

    Returns ``(server, pool)``. The server socket is bound, but the caller
    still owns ``serve_forever`` startup and shutdown.
    """
    voice_table: Dict[str, Any] = dict(voice or data.get("voice", {}))
    size = int(pool_size if pool_size is not None else voice_table.get("pool_size", DEFAULT_POOL_SIZE))
    pipeline_factory = real_pipeline_factory_from_manifest(data)
    pool = SessionPool(size=size, pipeline_factory=pipeline_factory)
    session_counter = [0]

    def on_connect(conn, path) -> None:
        session_counter[0] += 1
        session_id = "%s-%d" % (session_id_prefix, session_counter[0])
        try:
            unit = pool.claim(session_id)
        except Exception:  # noqa: BLE001 - pool exhaustion is a clean Realtime rejection
            conn.send_json(
                {
                    "type": "error",
                    "event_id": "evt_reject",
                    "error": {
                        "type": "session_limit_reached",
                        "message": "no free session slot",
                    },
                }
            )
            conn.close(code=1008, reason="session_limit_reached")
            return

        outbound = _OutboundEvents()
        service_lock = threading.Lock()
        service = RealtimeService(pipeline=unit.pipeline, send_event=outbound.enqueue, session_id=session_id)
        unit.service = service
        outbound.enqueue(
            {
                "type": "session.created",
                "event_id": "evt_session",
                "session": {"id": session_id},
            }
        )

        stop_sender = threading.Event()

        def sender_loop() -> None:
            while not stop_sender.is_set():
                with service_lock:
                    for event in service.drain_pipeline_events():
                        outbound.enqueue(event)
                event = outbound.pop(timeout=SENDER_POLL_INTERVAL_S)
                if event is None:
                    continue
                if event.get("type") == "response.done":
                    with service_lock:
                        try:
                            conn.send_text(json.dumps(event))
                        except OSError:
                            return
                        service.mark_response_done_sent(_event_response_id(event))
                else:
                    try:
                        conn.send_text(json.dumps(event))
                    except OSError:
                        return
                if event.get("type") == "response.output_audio.delta":
                    time.sleep(RESPONSE_OUTPUT_YIELD_S)

        sender_thread = threading.Thread(
            target=sender_loop,
            daemon=True,
            name="%s-%s" % (sender_thread_name_prefix, session_id),
        )
        sender_thread.start()
        try:
            while True:
                text = conn.recv_text()
                if text is None:
                    break
                with service_lock:
                    service.handle_client_message(text)
        finally:
            stop_sender.set()
            outbound.close()
            sender_thread.join(timeout=2.0)
            pool.release(unit)

    server = make_ws_server(
        voice_table.get("realtime_host", "127.0.0.1"),
        int(voice_table.get("realtime_port", 8765)),
        on_connect,
        extra_routes={"/pool": pool.pool_status, "/usage": pool.usage_stats},
        token_env=voice_table.get("realtime_token_env"),
    )
    return server, pool
