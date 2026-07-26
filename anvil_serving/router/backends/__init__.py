"""Backends package: the inference seam implementations.

The package contains deterministic in-process backends and the local network
relay used by the direct capability gateway. Everything that previously did::

    from anvil_serving.router.backends import EchoBackend, StaticBackend

still resolves: the local backends are re-exported here from :mod:`.local`.

* :mod:`.local`  — ``StaticBackend`` / ``EchoBackend`` (no network, no GPU).
* :mod:`.relay` — ``RelayBackend`` for local OpenAI/Anthropic-compatible serves.
"""

from __future__ import annotations

from .local import EchoBackend, StaticBackend, split_into_deltas
from .relay import RelayBackend

__all__ = [
    "EchoBackend",
    "StaticBackend",
    "split_into_deltas",
    "RelayBackend",
]
