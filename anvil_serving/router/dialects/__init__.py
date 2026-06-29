"""Wire dialects spoken by the front door.

A :class:`Dialect` is the data-plane seam (M0) that adapts one external wire
protocol to the router's internal representation in both directions:

* ``parse_request(body)``  — wire JSON  -> :class:`~anvil_serving.router.internal.InternalRequest`
* ``stream(request, deltas)`` — internal text deltas -> native SSE ``bytes``
* ``render(request, text)`` — internal full text -> a non-streamed response dict

Two dialects ship in M0: :class:`~anvil_serving.router.dialects.openai.OpenAIDialect`
(``data:`` / ``[DONE]`` framing) and
:class:`~anvil_serving.router.dialects.anthropic.AnthropicDialect` (named-event
framing). A later task (T011) formalizes the seam registry; the Protocol below
is intentionally minimal but real.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Iterator, Mapping

from ..internal import InternalRequest

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


@runtime_checkable
class Dialect(Protocol):
    """Adapter between one wire protocol and the internal representation."""

    name: str

    def parse_request(self, body: Mapping[str, Any]) -> InternalRequest:
        ...

    def stream(self, request: InternalRequest, deltas: Iterable[str]) -> Iterator[bytes]:
        ...

    def render(self, request: InternalRequest, text: str) -> Dict[str, Any]:
        ...


__all__ = ["Dialect"]
