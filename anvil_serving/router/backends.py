"""Default in-process backends for the M0 front door.

* :class:`StaticBackend` — yields a fixed list of text deltas regardless of the
  request. Deterministic; the tests inject this to assert exact streamed output.
* :class:`EchoBackend` — the default for the runnable server: echoes the last
  user message back as a SHORT multi-token completion (several deltas), so the
  streaming path is genuinely exercised end-to-end without a real model.

Both implement the :class:`~anvil_serving.router.internal.Backend` Protocol.
No network, no GPU, no third-party deps.
"""

from __future__ import annotations

from typing import Iterator, List, Sequence

from .internal import InternalRequest


def split_into_deltas(text: str) -> List[str]:
    """Split ``text`` into several word-sized deltas such that ``"".join`` is
    lossless (``"".join(split_into_deltas(t)) == t``).

    Leading spaces are attached to the following word so each delta carries its
    own separator — this produces multiple chunks for multi-word replies, which
    is what exercises multi-chunk streaming.
    """
    if not text:
        return []
    words = text.split(" ")
    deltas: List[str] = []
    for i, w in enumerate(words):
        deltas.append(w if i == 0 else " " + w)
    return [d for d in deltas if d]


class StaticBackend:
    """Yield a fixed, caller-supplied sequence of text deltas. Deterministic."""

    def __init__(self, tokens: Sequence[str]):
        self._tokens: List[str] = list(tokens)

    def generate(self, request: InternalRequest) -> Iterator[str]:
        for t in self._tokens:
            yield t


class EchoBackend:
    """Echo the last user message back, split into several text deltas.

    Deterministic given the request. Falls back to a fixed greeting when there
    is no user text, so the reply is always multi-delta.
    """

    def __init__(self, prefix: str = "You said: "):
        self._prefix = prefix

    def generate(self, request: InternalRequest) -> Iterator[str]:
        user = request.last_user_text.strip()
        reply = self._prefix + (user or "(no user message)")
        for delta in split_into_deltas(reply):
            yield delta
