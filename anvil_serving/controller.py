"""Compatibility facade for the authenticated HTTP controller.

The implementation lives in :mod:`anvil_serving.control_plane.controller`.
This module retains the established import and ``python -m`` surfaces used by
the CLI, tests, and external callers. Only names with real callers through
this facade (the CLI dispatch table, ``python -m anvil_serving.controller``,
and ``tests/test_controller.py``) are re-exported here; everything else lives
only in the internal ``control_plane.controller`` package.
"""

from __future__ import annotations

# Re-exported compatibility names are intentionally imported into this module.
# ruff: noqa: F401

from . import mcp as mcp
from .control_plane.controller.cli import (
    DEFAULT_STATUS_MAX_RESPONSE_BYTES,
    _build_parser,
    main,
    status,
)
from .control_plane.controller.errors import ControllerError
from .control_plane.controller.http import (
    FileAuditLogger,
    make_handler,
)
from .control_plane.controller.security import (
    BindAssessment,
    BindSafetyError,
    _json_dumps,
    _sanitize_persisted_value,
    resolve_auth_token,
    validate_bind_safety,
)
from .control_plane.controller.server import (
    make_server,
    serve,
)
from .control_plane.controller.store import (
    OperationStore,
    _operation_fingerprint,
)


__all__ = [
    "BindAssessment",
    "BindSafetyError",
    "ControllerError",
    "DEFAULT_STATUS_MAX_RESPONSE_BYTES",
    "FileAuditLogger",
    "OperationStore",
    "main",
    "make_handler",
    "make_server",
    "resolve_auth_token",
    "serve",
    "status",
    "validate_bind_safety",
]


if __name__ == "__main__":
    raise SystemExit(main())
