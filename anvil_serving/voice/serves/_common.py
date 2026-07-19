"""Shared out-of-process audio-serve lifecycle plumbing for STT/TTS
(anvil tasks T006/T008).

Bring-up/tear-down for the STT/TTS serves is NOT raw `docker run` in this
package -- it delegates ALL container lifecycle to
:mod:`anvil_serving.serves` (the existing declarative serve-manifest
lifecycle behind `anvil-serving serves status/up/down`: `docker_state`,
`cmd_up`, `cmd_down`, container health). That manifest (default
`./serves.toml`, what `anvil-serving serves render`/`init` write) declares each
serve's container name, port, and `up` command -- the actual STT/TTS engine
binary/container choice is configured there, NEVER in this Python file. This
module only adds:

* a readiness probe against the serve's declared endpoint (from
  the VOICE manifest's `[voice.stt]`/`[voice.tts]` tables -- see
  `anvil_serving/voice/config.py`), so a caller can distinguish "the
  container is up" from "the model is loaded and answering requests" --
  the same gap `anvil_serving/serves.py`'s own `_health` probe fills for the
  LLM tiers.
* :class:`ServeNotConfigured`, raised when the serves manifest (or a matching
  entry for this serve's name) doesn't exist yet -- a normal, expected state
  before an operator has declared the audio serve's container, not a crash.

Stdlib-only: `urllib.request`.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ... import serves as generic_serves

#: Default readiness-poll timeout (seconds) for the OpenAI-compatible probe.
DEFAULT_READY_TIMEOUT = 10.0


class ServeNotConfigured(RuntimeError):
    """The serves manifest (or an entry named for this serve) isn't declared yet.

    Expected before an operator has wired the audio serve's container into
    `serves.toml` -- callers (e.g. the `voice audio up`/`down` CLI) should treat
    this as "nothing to manage yet", not an error.
    """


@dataclass(frozen=True)
class ServeReadiness:
    """Snapshot of one out-of-process serve's lifecycle + health state."""

    name: str
    docker_state: str
    ready: bool
    detail: str


def _probe_models_endpoint(
    base_url: str, timeout: float, _open: Callable[..., Any],
    ready_url: Optional[str] = None,
) -> bool:
    """Probe a declared readiness URL, defaulting to ``{base_url}/models``.

    OpenAI-compatible TTS servers usually expose ``/v1/models``. Some valid
    STT engines (including parakeet.cpp) expose only a root ``/health`` route,
    so a manifest may set an explicit ``ready_url`` without changing the audio
    stage's OpenAI-compatible ``base_url``. A non-2xx response or a refused
    connection just means "not ready yet" -- never raises.
    """
    url = ready_url or (base_url.rstrip("/") + "/models")
    try:
        with _open(url, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return 200 <= status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


class ServeLifecycle:
    """Delegates bring-up/tear-down of ONE named out-of-process audio serve to
    :mod:`anvil_serving.serves` -- this class never invokes `docker` itself.

    ``serve_name`` selects the entry (by `name` or `container`) in the serves
    manifest (same discovery path `anvil-serving serves` reads) that fronts
    this serve.
    """

    def __init__(
        self,
        serve_name: str,
        *,
        manifest_path: Optional[str] = None,
        ready_url: Optional[str] = None,
        _run: Optional[Callable[..., Any]] = None,
        _open: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.serve_name = serve_name
        self.manifest_path = generic_serves.resolve_manifest_path(manifest_path)
        self.ready_url = ready_url
        # None -> let anvil_serving.serves fall back to its own subprocess.run default.
        self._run = _run
        self._open = _open or urllib.request.urlopen

    def _run_kwargs(self) -> dict:
        return {"_run": self._run} if self._run is not None else {}

    def _serves(self) -> List[dict]:
        try:
            return generic_serves.load_manifest(self.manifest_path)
        except FileNotFoundError as exc:
            raise ServeNotConfigured(
                "no serves manifest at %s -- declare a [[serve]] entry named "
                "%r for its container/port/up command (see "
                "examples/fakoli-dark/serves.toml) before bringing it up"
                % (self.manifest_path, self.serve_name)
            ) from exc

    def _find_entry(self, serves: List[dict]) -> dict:
        for s in serves:
            if s["name"] == self.serve_name or s["container"] == self.serve_name:
                return s
        raise ServeNotConfigured(
            "no [[serve]] entry named %r in %s" % (self.serve_name, self.manifest_path)
        )

    def bring_up(self, *, dry_run: bool = False, recreate: bool = False) -> int:
        """Start (or restart-if-stopped/unpause-if-paused) the serve.

        Raises :class:`ServeNotConfigured` if the manifest or a matching
        entry doesn't exist yet -- see the module docstring.
        """
        serves = self._serves()
        self._find_entry(serves)  # validates the entry exists; raises if not
        return generic_serves.cmd_up(
            serves, [self.serve_name], dry_run=dry_run, recreate=recreate,
            **self._run_kwargs(),
        )

    def tear_down(self, *, dry_run: bool = False) -> int:
        """Stop the serve (frees the GPU/container); no-op if already stopped."""
        serves = self._serves()
        self._find_entry(serves)
        return generic_serves.cmd_down(
            serves, [self.serve_name], dry_run=dry_run, **self._run_kwargs()
        )

    def docker_state(self) -> str:
        """The serve's current docker state, or ``"absent"``/raises if unconfigured."""
        serves = self._serves()
        entry = self._find_entry(serves)
        return generic_serves.docker_state(entry["container"], **self._run_kwargs())

    def wait_ready(
        self, base_url: str, *, timeout: float = DEFAULT_READY_TIMEOUT,
    ) -> ServeReadiness:
        """Probe both docker state and the OpenAI-compatible endpoint.

        Never raises :class:`ServeNotConfigured` for the HEALTH half -- an
        unconfigured serves manifest still yields a ``ServeReadiness`` with
        ``docker_state="unconfigured"`` so a caller can report readiness
        without a hard crash; only ``bring_up``/``tear_down`` (which actually
        mutate state) enforce the manifest must exist.
        """
        try:
            state = self.docker_state()
        except ServeNotConfigured:
            state = "unconfigured"
        probe_url = self.ready_url or (base_url.rstrip("/") + "/models")
        ready = _probe_models_endpoint(base_url, timeout, self._open, self.ready_url)
        return ServeReadiness(
            name=self.serve_name,
            docker_state=state,
            ready=ready,
            detail="healthy" if ready else (
                "not responding at %s" % probe_url
            ),
        )
