"""Local-tier relay backend (#46: decoupled from ``serve.py``).

:class:`RelayBackend` relays an
:class:`~anvil_serving.router.internal.InternalRequest` to a LOCAL tier's
OpenAI/Anthropic-compatible endpoint by reusing
:class:`~anvil_serving.router.backends.cloud.CloudBackend`'s tested dialect
machinery (``_endpoint`` / ``_build_body`` / ``_extract_text`` / ``generate``).
It changes only the credential policy — auth is OPTIONAL for a local vLLM/SGLang
server — so it belongs in the backends package alongside the cloud backend it
specialises, keeping ``serve.py`` about routing rather than backend internals.
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional

from ..config import DIALECT_ANTHROPIC, Tier
from .cloud import _ANTHROPIC_VERSION, CloudBackend, StreamTransport, Transport


class RelayBackend(CloudBackend):
    """Relay an :class:`~anvil_serving.router.internal.InternalRequest` to a
    LOCAL tier's OpenAI/Anthropic-compatible endpoint.

    Reuses :class:`~anvil_serving.router.backends.cloud.CloudBackend`'s tested
    dialect machinery (``_endpoint`` / ``_build_body`` / ``_extract_text`` /
    ``generate``) by subclassing it, and changes only the credential policy:

    * It serves a ``privacy == "local"`` tier (CloudBackend refuses non-cloud
      tiers by design — it authenticates against a remote provider).
    * **Auth is optional.** A local vLLM/SGLang server usually needs none, so a
      missing ``auth_env`` is NOT fatal here (unlike CloudBackend). If the env
      var IS set we forward it (``Authorization: Bearer`` / ``x-api-key``); if
      not, the relay is unauthenticated.

    Construction delegates to ``CloudBackend.__init__`` with the private
    ``_require_key=False`` opt-out, so RelayBackend INHERITS the base's attribute
    set (``_tier`` / ``_key`` / ``_timeout`` / ``_transport``) and the env/transport
    resolution rather than hand-copying them — a future attribute added to
    ``CloudBackend.__init__`` carries over automatically. The only override is
    :meth:`_headers` (auth-optional).

    The cloud call is non-streaming upstream; the reply is split into deltas so
    the front door's streaming path stays genuinely multi-chunk (inherited).
    """

    def __init__(
        self,
        tier: Tier,
        *,
        env: Optional[Mapping[str, str]] = None,
        transport: Optional[Transport] = None,
        timeout: float = 120.0,
        stream_transport: Optional[StreamTransport] = None,
    ):
        # Relay mode: no credential requirement and no cloud-only privacy gate
        # (local tier). super() resolves the optional key from ``auth_env`` (may
        # be empty -> no auth header, see _headers) and the default transport
        # (including the streaming one — a local vLLM/SGLang serve streams SSE).
        super().__init__(
            tier, env=env, transport=transport, timeout=timeout,
            stream_transport=stream_transport, _require_key=False
        )

    def _headers(self) -> Dict[str, str]:
        """Outbound headers; the auth header is included ONLY if a key resolved."""
        headers = {"Content-Type": "application/json"}
        if self._tier.dialect == DIALECT_ANTHROPIC:
            headers["anthropic-version"] = _ANTHROPIC_VERSION
            if self._key:
                headers["x-api-key"] = self._key
        else:  # openai-compatible
            if self._key:
                headers["Authorization"] = f"Bearer {self._key}"
        return headers
