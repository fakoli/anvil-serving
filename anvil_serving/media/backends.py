"""Internal normalized records for media backend adapters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BackendOutput:
    """Private retrieval coordinates that must stop at the artifact boundary."""

    node: str
    filename: str = field(repr=False)
    subfolder: str = field(default="", repr=False)
    storage_type: str = field(default="output", repr=False)


@dataclass(frozen=True)
class BackendStatus:
    prompt_id: str = field(repr=False)
    state: str = "unknown"
    progress: float | None = None
    outputs: tuple[BackendOutput, ...] = ()
    error_code: str = ""


__all__ = ["BackendOutput", "BackendStatus"]
