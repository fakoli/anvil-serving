"""Controller errors shared by the internal implementation modules."""

from __future__ import annotations

from typing import Any, Optional


class ControllerError(Exception):
    """Structured controller failure rendered as JSON."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
