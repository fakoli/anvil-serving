"""OpenAI Realtime-compatible WebSocket server, stdlib-only (anvil task T011/T012/T013).

Four modules, one direction of data flow: a caller (any Realtime-speaking SDK
or a hand-rolled WS client) connects over ``ws.py``'s pure-stdlib WebSocket
transport; ``events.py`` parses its client events and builds the server-event
subset back; ``service.py`` translates those events against one
:class:`~anvil_serving.voice.pipeline.VoicePipeline` instance; ``pool.py``
hands each connection an isolated pipeline out of a bounded pool, with
drain-before-release so one session's stale in-flight audio can never leak
into the next session reusing that slot.

Import-light like the rest of ``anvil_serving.voice``: stdlib only
(``http.server``, ``socket``, ``hashlib``, ``base64``, ``struct``, ``json``,
``threading``, ``queue``, ``dataclasses``). No ``websockets`` library, no
FastAPI, no ``openai`` SDK.

HONESTY NOTE: this unit is proven by dependency-light unit tests only (frame
encode/decode, handshake bytes, event parse/dispatch tables, pool isolation)
-- never against a live audio device, a real STT/TTS serve, or the official
OpenAI Realtime SDK as a client. See each module's docstring for the specific
simplifications made relative to the reference design in
``docs/findings/2026-07-04-hf-speech-to-speech-review.md``.
"""
from __future__ import annotations

from .events import (
    ClientEvent,
    EventParseError,
    ServerEvent,
    dispatch_internal_event,
    parse_client_event,
    server_event_to_dict,
)
from .pool import PoolUnit, SessionPool, SessionPoolExhausted
from .service import RealtimeService, SessionState
from .ws import WebSocketConnection, build_frame, client_handshake, make_ws_server, parse_frame

__all__ = [
    "ClientEvent",
    "EventParseError",
    "ServerEvent",
    "dispatch_internal_event",
    "parse_client_event",
    "server_event_to_dict",
    "PoolUnit",
    "SessionPool",
    "SessionPoolExhausted",
    "RealtimeService",
    "SessionState",
    "WebSocketConnection",
    "build_frame",
    "client_handshake",
    "make_ws_server",
    "parse_frame",
]
