"""Wire dialects spoken by the front door.

A :class:`Dialect` is the data-plane seam (M0) that adapts one external wire
protocol to the router's internal representation in both directions:

* ``parse_request(body)``  — wire JSON  -> :class:`~anvil_serving.router.internal.InternalRequest`
* ``stream(request, deltas)`` — internal text deltas -> native SSE ``bytes``
* ``render(request, text)`` — internal full text -> a non-streamed response dict
* ``render_error(status, etype, message)`` — an error in the dialect's native
  envelope (OpenAI ``{"error":{...}}``; Anthropic ``{"type":"error","error":{...}}``)

Two dialects ship in M0: :class:`~anvil_serving.router.dialects.openai.OpenAIDialect`
(``data:`` / ``[DONE]`` framing) and
:class:`~anvil_serving.router.dialects.anthropic.AnthropicDialect` (named-event
framing). A later task (T011) formalizes the seam registry; the Protocol below
is intentionally minimal but real.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional

from ..internal import InternalRequest

from typing import Protocol, runtime_checkable


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:24]


@runtime_checkable
class Dialect(Protocol):
    """Adapter between one wire protocol and the internal representation."""

    name: str

    def parse_request(self, body: Mapping[str, Any]) -> InternalRequest:
        ...

    def stream(
        self,
        request: InternalRequest,
        deltas: Iterable[str],
        *,
        get_structured: Optional[Callable[[], Any]] = None,
        response_model: Optional[str] = None,
    ) -> Iterator[bytes]:
        ...

    def render(
        self,
        request: InternalRequest,
        text: str,
        *,
        structured: Any = None,
        response_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        ...

    def render_error(self, status: int, etype: str, message: str) -> Dict[str, Any]:
        ...


__all__ = ["Dialect"]
