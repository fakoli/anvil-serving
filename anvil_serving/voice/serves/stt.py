"""Out-of-process STT serve lifecycle (anvil task T006, serve half).

Manages bring-up/tear-down + readiness of the STT serve that exposes an
OpenAI-compatible ``/v1/audio/transcriptions`` endpoint (e.g. parakeet.cpp or
a vLLM Whisper/Qwen3-ASR deployment -- see
``docs/findings/2026-07-04-hf-speech-to-speech-review.md``). The engine
binary/container itself is declared in a `serves.toml` manifest entry (NOT in
this file, NOT run here) -- see :mod:`anvil_serving.voice.serves._common` for
the shared delegation-to-``anvil_serving.serves`` lifecycle plumbing.

``anvil-serving voice up``/``down`` construct an :class:`STTServe` from the
voice manifest's ``[voice.stt]`` table (``base_url``/``model``) and call
:meth:`STTServe.bring_up`/:meth:`STTServe.tear_down` -- no raw ``docker run``
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

__all__ = ["STTServeConfig", "STTServe", "ServeNotConfigured", "ServeReadiness"]

#: The `serves.toml` `[[serve]]` entry name expected to front the STT serve.
DEFAULT_SERVE_NAME = "stt"


@dataclass(frozen=True)
class STTServeConfig:
    """Where the STT serve lives (from the voice manifest's ``[voice.stt]``
    table) -- NOT how it's launched (that's the serves manifest; see
    ``_common.ServeLifecycle``)."""

    base_url: str
    model: str
    serve_name: str = DEFAULT_SERVE_NAME
    manifest_path: Optional[str] = None


class STTServe:
    """Bring-up/tear-down + readiness for the out-of-process STT serve
    exposing OpenAI ``/v1/audio/transcriptions``.

    NEVER runs `docker` itself -- delegates to
    :class:`anvil_serving.voice.serves._common.ServeLifecycle`, which in turn
    delegates to :mod:`anvil_serving.serves` (the declarative serves-manifest
    lifecycle already used by `anvil-serving serves up/down`).
    """

    def __init__(
        self,
        config: STTServeConfig,
        *,
        _run: Optional[Callable[..., Any]] = None,
        _open: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.config = config
        self._lifecycle = ServeLifecycle(
            config.serve_name, manifest_path=config.manifest_path, _run=_run, _open=_open,
        )

    def bring_up(self, *, dry_run: bool = False, recreate: bool = False) -> int:
        """Start the STT serve's container. Raises :class:`ServeNotConfigured`
        if no matching `serves.toml` entry exists yet."""
        return self._lifecycle.bring_up(dry_run=dry_run, recreate=recreate)

    def tear_down(self) -> int:
        """Stop the STT serve's container (frees the GPU)."""
        return self._lifecycle.tear_down()

    def wait_ready(self, *, timeout: float = DEFAULT_READY_TIMEOUT) -> ServeReadiness:
        """Poll docker state + an OpenAI-compatible readiness probe."""
        return self._lifecycle.wait_ready(self.config.base_url, timeout=timeout)

    @property
    def transcriptions_url(self) -> str:
        """The full ``/v1/audio/transcriptions`` URL the STT stage POSTs to."""
        return self.config.base_url.rstrip("/") + "/audio/transcriptions"
