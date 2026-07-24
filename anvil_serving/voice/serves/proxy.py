"""Docker-managed Realtime voice-proxy serve lifecycle (managed-container half).

Manages bring-up/tear-down + readiness of the Realtime voice proxy when it is
run as a Docker-MANAGED CONTAINER (reusing the ``anvil-serving`` image running
``anvil-serving voice proxy run``) instead of as a same-host native process
(:mod:`anvil_serving.voice.realtime_service`). The container itself is declared
in a `serves.toml`/`serves.voice.toml` manifest entry (NOT in this file, NOT
run here) -- see :mod:`anvil_serving.voice.serves._common` for the shared
delegation-to-``anvil_serving.serves`` lifecycle plumbing.

This mirrors the STT/TTS managed-serve pattern
(:mod:`anvil_serving.voice.serves.stt` / :mod:`~anvil_serving.voice.serves.tts`)
exactly: ``anvil-serving voice proxy up``/``down`` (with a managed
``[voice.proxy]`` lifecycle) construct a :class:`ProxyServe` from the voice
manifest's ``[voice.proxy]`` table and call :meth:`ProxyServe.bring_up`/
:meth:`ProxyServe.tear_down` -- no raw ``docker run`` in the operator path.

The one difference from STT/TTS: the realtime proxy exposes its health at
``/usage`` (see :mod:`anvil_serving.voice.realtime.app`'s ``extra_routes``),
not the OpenAI ``/v1/models`` an STT/TTS serve answers, so the readiness probe
defaults to ``{base_url}/usage``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from ._common import (
    DEFAULT_READY_TIMEOUT,
    ServeLifecycle,
    ServeNotConfigured,
    ServeReadiness,
)

__all__ = ["ProxyServeConfig", "ProxyServe", "ServeNotConfigured", "ServeReadiness"]

#: The `serves.toml` `[[serve]]` entry name expected to front the proxy serve.
DEFAULT_SERVE_NAME = "realtime-proxy"


@dataclass(frozen=True)
class ProxyServeConfig:
    """Where the managed realtime-proxy container lives (from the voice
    manifest's ``[voice.proxy]`` table) -- NOT how it's launched (that's the
    serves manifest; see ``_common.ServeLifecycle``)."""

    base_url: str
    model: str
    serve_name: str = DEFAULT_SERVE_NAME
    manifest_path: Optional[str] = None
    ready_url: Optional[str] = None


class ProxyServe:
    """Bring-up/tear-down + readiness for the Docker-managed realtime proxy
    container exposing the Realtime WebSocket endpoint plus a ``/usage`` health
    route.

    NEVER runs `docker` itself -- delegates to
    :class:`anvil_serving.voice.serves._common.ServeLifecycle`, which in turn
    delegates to :mod:`anvil_serving.serves` (the declarative serves-manifest
    lifecycle already used by `anvil-serving serves up/down`).
    """

    def __init__(
        self,
        config: ProxyServeConfig,
        *,
        _run: Optional[Callable[..., Any]] = None,
        _open: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.config = config
        # The proxy's health route is `/usage`, not the OpenAI `/v1/models`
        # ServeLifecycle probes by default -- pass an explicit ready_url so the
        # readiness half checks the right endpoint.
        ready_url = config.ready_url or (config.base_url.rstrip("/") + "/usage")
        self._lifecycle = ServeLifecycle(
            config.serve_name, manifest_path=config.manifest_path,
            ready_url=ready_url, _run=_run, _open=_open,
        )

    def bring_up(self, *, dry_run: bool = False, recreate: bool = False) -> int:
        """Start the proxy serve's container. Raises :class:`ServeNotConfigured`
        if no matching `serves.toml` entry exists yet."""
        return self._lifecycle.bring_up(dry_run=dry_run, recreate=recreate)

    def tear_down(self, *, dry_run: bool = False) -> int:
        """Stop the proxy serve's container."""
        return self._lifecycle.tear_down(dry_run=dry_run)

    def wait_ready(self, *, timeout: float = DEFAULT_READY_TIMEOUT) -> ServeReadiness:
        """Poll docker state plus the proxy's ``/usage`` readiness probe."""
        return self._lifecycle.wait_ready(self.config.base_url, timeout=timeout)

    @property
    def realtime_url(self) -> str:
        """The realtime endpoint URL clients connect to on the proxy."""
        return self.config.base_url.rstrip("/") + "/realtime"
