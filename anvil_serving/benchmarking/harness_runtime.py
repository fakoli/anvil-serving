"""Bounded command and filesystem helpers shared by harness preparation layers."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any, Callable, Sequence

from .artifacts import real_path
from .jobs import BenchmarkJobError


MAX_HARNESS_OUTPUT_BYTES = 64 * 1024

DEFAULT_HARNESS_COMMAND_TIMEOUT = 1800

CommandRunner = Callable[[Sequence[str], str | None, float], Any]

def resolve_container_binary() -> str | None:
    """Resolve Docker for detached workers whose service PATH may be minimal."""
    discovered = shutil.which("docker")
    if discovered:
        return discovered
    if sys.platform == "darwin":
        for candidate in (
            "/usr/local/bin/docker",
            "/opt/homebrew/bin/docker",
            "/Applications/Docker.app/Contents/Resources/bin/docker",
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None

def _default_runner(argv: Sequence[str], cwd: str | None, timeout: float):
    return subprocess.run(
        list(argv),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )

def _bounded_text(value: Any, *, keep_tail: bool = False) -> tuple[str, bool]:
    raw = value if isinstance(value, bytes) else str(value or "").encode("utf-8")
    truncated = len(raw) > MAX_HARNESS_OUTPUT_BYTES
    if truncated and keep_tail:
        marker = b"\n...[middle output omitted]...\n"
        head = (MAX_HARNESS_OUTPUT_BYTES - len(marker)) // 2
        tail = MAX_HARNESS_OUTPUT_BYTES - len(marker) - head
        raw = raw[:head] + marker + raw[-tail:]
    return raw[:MAX_HARNESS_OUTPUT_BYTES].decode("utf-8", "replace"), truncated

def _run(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: float = DEFAULT_HARNESS_COMMAND_TIMEOUT,
) -> dict[str, Any]:
    try:
        result = runner(tuple(argv), cwd, timeout)
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _bounded_text(exc.stdout)
        stderr, stderr_truncated = _bounded_text(exc.stderr, keep_tail=True)
        raise BenchmarkJobError(
            "harness_timeout",
            "managed harness command timed out",
            {
                "command": list(argv),
                "stdout": stdout,
                "stderr": stderr,
                "output_truncated": stdout_truncated or stderr_truncated,
            },
        ) from exc
    stdout, stdout_truncated = _bounded_text(getattr(result, "stdout", b""))
    stderr, stderr_truncated = _bounded_text(getattr(result, "stderr", b""), keep_tail=True)
    return {
        "command": list(argv),
        "returncode": int(getattr(result, "returncode", 1)),
        "stdout": stdout,
        "stderr": stderr,
        "output_truncated": stdout_truncated or stderr_truncated,
    }

def _directory_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total

def _normalized_freeze(value: str, *, cache_root: str) -> list[str]:
    normalized_root = real_path(cache_root).replace("\\", "/").rstrip("/")
    packages = []
    for raw in value.splitlines():
        line = raw.strip().replace("\\", "/")
        if not line:
            continue
        packages.append(line.replace(normalized_root, "$CACHE_ROOT"))
    return sorted(packages, key=str.casefold)
