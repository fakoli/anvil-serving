"""Shared deterministic router test doubles."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from anvil_serving.router.internal import InternalRequest


class StaticBackend:
    """Yield a fixed caller-supplied sequence of text deltas."""

    def __init__(self, tokens: Sequence[str]):
        self._tokens = list(tokens)

    def generate(self, request: InternalRequest) -> Iterator[str]:
        yield from self._tokens
