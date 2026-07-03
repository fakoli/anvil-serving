"""Base types for external benchmark source adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParseResult:
    rows: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    parser_name: str = ""
    parser_version: str = ""


class SourceAdapter:
    name = "base"
    parser_name = "base"
    parser_version = "0"

    def parse(
        self,
        raw_bytes: bytes,
        *,
        content_type: str | None = None,
        source_url: str | None = None,
        original_name: str | None = None,
    ) -> ParseResult:
        raise NotImplementedError

