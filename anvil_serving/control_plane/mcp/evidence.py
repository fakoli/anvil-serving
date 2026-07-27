"""MCP adapters for public benchmark artifact validation."""

from __future__ import annotations

from ...benchmarking.artifacts import (
    BenchmarkArtifactError,
    read_benchmark_artifact as _read_benchmark_artifact,
    resolve_benchmark_artifact_path as _resolve_benchmark_artifact_path,
)
from .errors import ToolError


def _translate_artifact_error(exc: BenchmarkArtifactError) -> ToolError:
    return ToolError(exc.code, exc.message, exc.details)


def read_benchmark_artifact(path: str) -> dict:
    try:
        return _read_benchmark_artifact(path)
    except BenchmarkArtifactError as exc:
        raise _translate_artifact_error(exc) from exc


def resolve_benchmark_artifact_path(path: str) -> tuple[str, list[str]]:
    try:
        return _resolve_benchmark_artifact_path(path)
    except BenchmarkArtifactError as exc:
        raise _translate_artifact_error(exc) from exc
