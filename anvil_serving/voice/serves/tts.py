"""Out-of-process TTS serve lifecycle (anvil task T008, serve half).

Manages bring-up/tear-down + readiness of the TTS serve that exposes an
OpenAI-compatible ``/v1/audio/speech`` endpoint (e.g. Kokoro-FastAPI or an
Orpheus-3B vLLM deployment behind a thin shim -- see
``docs/findings/2026-07-04-hf-speech-to-speech-review.md``). The engine
binary/container itself is declared in a `serves.toml` manifest entry (NOT in
this file, NOT run here) -- see :mod:`anvil_serving.voice.serves._common` for
the shared delegation-to-``anvil_serving.serves`` lifecycle plumbing.

``anvil-serving voice up``/``down`` construct a :class:`TTSServe` from the
voice manifest's ``[voice.tts]`` table (``base_url``/``model``) and call
:meth:`TTSServe.bring_up`/:meth:`TTSServe.tear_down` -- no raw ``docker run``
in the operator path.
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

__all__ = ["TTSServeConfig", "TTSServe", "ServeNotConfigured", "ServeReadiness"]

#: The `serves.toml` `[[serve]]` entry name expected to front the TTS serve.
DEFAULT_SERVE_NAME = "tts"


@dataclass(frozen=True)
class TTSServeConfig:
    """Where the TTS serve lives (from the voice manifest's ``[voice.tts]``
    table) -- NOT how it's launched (that's the serves manifest; see
    ``_common.ServeLifecycle``)."""

    base_url: str
    model: str
    serve_name: str = DEFAULT_SERVE_NAME
    manifest_path: Optional[str] = None


class TTSServe:
    """Bring-up/tear-down + readiness for the out-of-process TTS serve
    exposing OpenAI ``/v1/audio/speech``.

    NEVER runs `docker` itself -- delegates to
    :class:`anvil_serving.voice.serves._common.ServeLifecycle`, which in turn
    delegates to :mod:`anvil_serving.serves` (the declarative serves-manifest
    lifecycle already used by `anvil-serving serves up/down`).
    """

    def __init__(
        self,
        config: TTSServeConfig,
        *,
        _run: Optional[Callable[..., Any]] = None,
        _open: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.config = config
        self._lifecycle = ServeLifecycle(
            config.serve_name, manifest_path=config.manifest_path, _run=_run, _open=_open,
        )

    def bring_up(self, *, dry_run: bool = False, recreate: bool = False) -> int:
        """Start the TTS serve's container. Raises :class:`ServeNotConfigured`
        if no matching `serves.toml` entry exists yet."""
        return self._lifecycle.bring_up(dry_run=dry_run, recreate=recreate)

    def tear_down(self, *, dry_run: bool = False) -> int:
        """Stop the TTS serve's container (frees the GPU)."""
        return self._lifecycle.tear_down(dry_run=dry_run)

    def wait_ready(self, *, timeout: float = DEFAULT_READY_TIMEOUT) -> ServeReadiness:
        """Poll docker state + an OpenAI-compatible readiness probe."""
        return self._lifecycle.wait_ready(self.config.base_url, timeout=timeout)

    @property
    def speech_url(self) -> str:
        """The full ``/v1/audio/speech`` URL the TTS stage POSTs to."""
        return self.config.base_url.rstrip("/") + "/audio/speech"
