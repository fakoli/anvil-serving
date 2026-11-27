"""RFC 6455 handshake helpers shared by the voice proxy and the router.

Stdlib only and import-free of the rest of anvil_serving, so both
``anvil_serving.voice.realtime.ws`` and ``anvil_serving.router.realtime`` can
use it without an import cycle.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

#: RFC 6455 s1.3 magic GUID appended to the client's Sec-WebSocket-Key before
#: SHA-1 + base64 to compute Sec-WebSocket-Accept.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def compute_accept_key(client_key: str) -> str:
    """RFC 6455 s1.3: base64(SHA1(client_key + magic GUID))."""
    digest = hashlib.sha1((client_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _header_contains_token(value: Optional[str], token: str) -> bool:
    """True if comma-separated ``value`` contains ``token`` (case-insensitive).

    Both ``Connection`` and ``Upgrade`` are allowed to carry a comma-separated
    list per RFC 7230 s6.1/s6.7 (e.g. ``Connection: keep-alive, Upgrade``).
    """
    if not value:
        return False
    return token.lower() in [part.strip().lower() for part in value.split(",")]


def is_websocket_upgrade(headers) -> bool:
    """True if ``headers`` (an ``email.message.Message`` or a plain dict) is a
    valid RFC 6455 upgrade request: ``Upgrade: websocket``, ``Connection``
    containing ``upgrade``, a present ``Sec-WebSocket-Key``, and
    ``Sec-WebSocket-Version: 13``.
    """
    get = headers.get
    if not _header_contains_token(get("Upgrade"), "websocket"):
        return False
    if not _header_contains_token(get("Connection"), "upgrade"):
        return False
    if not (get("Sec-WebSocket-Key") or "").strip():
        return False
    if (get("Sec-WebSocket-Version") or "").strip() != "13":
        return False
    return True
