"""Backends package: the inference seam implementations.

This was a single ``backends.py`` module (the local M0 backends). T006 splits it
into a package so a real network-facing :class:`~anvil_serving.router.backends.cloud.CloudBackend`
can live alongside the in-process ones — WITHOUT changing the public import
surface. Everything that previously did::

    from anvil_serving.router.backends import EchoBackend, StaticBackend

still resolves: the local backends are re-exported here from :mod:`.local`.

* :mod:`.local`  — ``StaticBackend`` / ``EchoBackend`` (no network, no GPU).
* :mod:`.cloud`  — ``CloudBackend`` (outbound Anthropic / OpenAI-compatible call,
  auth resolved from the per-tier ``auth_env`` env var; stdlib-only HTTP).
"""

from __future__ import annotations

from .cloud import CloudBackend, MissingCredentialError
from .local import EchoBackend, StaticBackend, split_into_deltas

__all__ = [
    "EchoBackend",
    "StaticBackend",
    "split_into_deltas",
    "CloudBackend",
    "MissingCredentialError",
]
