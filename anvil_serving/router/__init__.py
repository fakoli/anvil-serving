"""anvil-serving router — quality-gated local-model router with automatic fallback and per-(model, work-class) quality profiling."""

from __future__ import annotations

from .backends import (
    CloudBackend,
    EchoBackend,
    MissingCredentialError,
    StaticBackend,
    split_into_deltas,
)
from .commit_window import (
    FallbackEvent,
    build_response_view,
    stream_with_commit_window,
)
from .discovery import models_payload
# NOTE: import ONLY make_server here. The T001 front-door launcher is
# ``front_door.serve``; importing it as ``serve`` would be SHADOWED below by the
# ``serve`` SUBMODULE (``from .serve import ...`` rebinds ``router.serve`` to the
# module), silently breaking the export. Reach the T001 launcher via
# ``anvil_serving.router.front_door.serve`` or ``python -m anvil_serving.router``.
from .front_door import make_server
from .intent import PRESETS, Preset
from .internal import Backend, InternalRequest, Message, NoAvailableTierError
from .secrets import redact_key, redact_prompt, sanitize
from .serve import (
    RelayBackend,
    RoutingBackend,
    build_backend_for_tier,
    build_backends,
    build_server,
)
from .serve import serve as serve_config
from .verify import (
    CodeParses,
    DiffWellFormed,
    FormatWellFormed,
    NonEmptyContent,
    NotTruncated,
    RefusalMarker,
    ResponseView,
    ToolCallJSONValid,
    Verifier,
    VerifyResult,
    aggregate,
    all_passed,
    default_verifiers,
    run_verifiers,
)

__all__ = [
    "make_server",
    "Backend",
    "InternalRequest",
    "Message",
    "NoAvailableTierError",
    "EchoBackend",
    "StaticBackend",
    "split_into_deltas",
    # T006 — cloud-tier credentialed backend + secrets hygiene
    "CloudBackend",
    "MissingCredentialError",
    "redact_key",
    "redact_prompt",
    "sanitize",
    # T007 — cheap inline structural verifiers
    "ResponseView",
    "VerifyResult",
    "Verifier",
    "NonEmptyContent",
    "NotTruncated",
    "ToolCallJSONValid",
    "CodeParses",
    "DiffWellFormed",
    "FormatWellFormed",
    "RefusalMarker",
    "default_verifiers",
    "run_verifiers",
    "all_passed",
    "aggregate",
    # T008 — streaming commit-window (buffer -> verify -> commit-or-fallback)
    "stream_with_commit_window",
    "FallbackEvent",
    "build_response_view",
    # T004 — /v1/models preset discovery
    "PRESETS",
    "Preset",
    "models_payload",
    # T012 — `anvil-serving serve`: config -> per-tier backends -> front door
    "serve_config",
    "build_server",
    "build_backends",
    "build_backend_for_tier",
    "RoutingBackend",
    "RelayBackend",
    # `serve` (the submodule) is intentionally NOT re-exported as a name here; it
    # is the T012 CLI module, reached as ``anvil_serving.router.serve``. Likewise
    # the T005 ``profile_store`` and T015 ``profile_bootstrap`` modules are reached
    # directly (``anvil_serving.router.profile_bootstrap``) — the latter is also a
    # ``python -m`` entry point, so re-exporting it here would double-load it.
]
