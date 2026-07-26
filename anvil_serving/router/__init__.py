"""Anvil Serving's direct local-capability gateway."""
from __future__ import annotations

from .backends import EchoBackend, RelayBackend, StaticBackend, split_into_deltas
from .discovery import models_payload
from .front_door import make_server
from .internal import Backend, InternalRequest, Message, NoAvailableTierError
from .serve import RoutingBackend, build_backend_for_tier, build_backends, build_server
from .serve import serve as serve_config

__all__ = [
    "make_server",
    "Backend",
    "InternalRequest",
    "Message",
    "NoAvailableTierError",
    "EchoBackend",
    "StaticBackend",
    "RelayBackend",
    "split_into_deltas",
    "models_payload",
    "serve_config",
    "build_server",
    "build_backends",
    "build_backend_for_tier",
    "RoutingBackend",
]
