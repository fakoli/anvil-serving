"""Backends package: the inference seam implementations.

The package contains the local network relay used by the direct capability
gateway.
"""

from __future__ import annotations

from .relay import RelayBackend, split_into_deltas

__all__ = [
    "split_into_deltas",
    "RelayBackend",
]
