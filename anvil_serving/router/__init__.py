"""anvil-serving router package.

M0 ships the protocol-standard front door (T001): one HTTP server speaking both
the Anthropic Messages and OpenAI Chat Completions dialects, streaming back in
each caller's native SSE framing, passing through to one injectable backend.
Intent routing, multiple tiers, and verify/fallback are later milestones.
"""

from __future__ import annotations

from .backends import EchoBackend, StaticBackend, split_into_deltas
from .front_door import make_server, serve
from .internal import Backend, InternalRequest, Message

__all__ = [
    "make_server",
    "serve",
    "Backend",
    "InternalRequest",
    "Message",
    "EchoBackend",
    "StaticBackend",
    "split_into_deltas",
]
