"""Structured MCP tool errors and result envelopes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


class ToolError(Exception):
    """User-facing tool failure rendered into the structured tool envelope."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def ok(data: dict) -> dict:
    """Return a successful tool envelope."""

    return {"ok": True, "data": data}


def fail(
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    *,
    redact_text: Callable[[str], str],
    redact_details: Callable[[Any], Any],
) -> dict:
    """Return a redacted failed tool envelope."""

    return {
        "ok": False,
        "error": {
            "code": code,
            "message": redact_text(message),
            "details": redact_details(dict(details or {})),
        },
    }
