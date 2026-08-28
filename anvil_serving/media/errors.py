"""Protocol-neutral failures for bounded media operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class MediaError(Exception):
    """A stable media-domain error that is safe to project across protocols."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            result["details"] = dict(self.details)
        return result


__all__ = ["MediaError"]
