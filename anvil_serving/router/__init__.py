"""anvil-serving router package.

M0 ships the protocol-standard front door (T001): one HTTP server speaking both
the Anthropic Messages and OpenAI Chat Completions dialects, streaming back in
each caller's native SSE framing, passing through to one injectable backend.
Intent routing, multiple tiers, and verify/fallback are later milestones.
"""

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
from .front_door import make_server, serve
from .intent import PRESETS, Preset
from .internal import Backend, InternalRequest, Message
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
    "serve",
    "Backend",
    "InternalRequest",
    "Message",
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
]
