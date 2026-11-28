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


def _get_header_values(headers, name: str) -> list[str]:
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all(name)
        return [v for v in values if isinstance(v, str)] if values else []
    value = headers.get(name)
    return [value] if isinstance(value, str) else []


def is_websocket_upgrade(headers) -> bool:
    """True if ``headers`` (an ``email.message.Message`` or a plain dict) is a
    valid RFC 6455 upgrade request: ``Upgrade: websocket``, ``Connection``
    containing ``upgrade``, a present ``Sec-WebSocket-Key``, and
    ``Sec-WebSocket-Version: 13``.
    """
    upgrade_values = _get_header_values(headers, "Upgrade")
    upgrade_tokens = [
        part.strip().lower()
        for value in upgrade_values
        for part in value.split(",")
        if part.strip()
    ]
    if not upgrade_tokens or any(token != "websocket" for token in upgrade_tokens):
        return False
    connection_values = _get_header_values(headers, "Connection")
    if not any(_header_contains_token(value, "upgrade") for value in connection_values):
        return False
    keys = _get_header_values(headers, "Sec-WebSocket-Key")
    if len(keys) != 1 or not keys[0].strip():
        return False
    versions = _get_header_values(headers, "Sec-WebSocket-Version")
    if len(versions) != 1 or versions[0].strip() != "13":
        return False
    return True
