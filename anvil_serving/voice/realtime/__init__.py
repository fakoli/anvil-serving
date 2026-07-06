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

HONESTY NOTE: CI covers the dependency-light pieces (frame encode/decode,
handshake bytes, event parse/dispatch tables, pool isolation). Live hardware
and official OpenAI SDK compatibility are proven by capture harnesses, not by
CI; see ``docs/findings/2026-07-voice-realtime-proof.md`` and
``docs/VOICE-REALTIME.md`` for the current replacement contract and known
subset.
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
