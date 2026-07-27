"""Bounded subprocess execution and output capture for MCP tools."""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
from collections.abc import Callable

from .errors import ToolError


MAX_CAPTURE_CHARS = 1024 * 1024


def capture(fn: Callable[[], int]) -> tuple[int, str, str]:
    with (
        tempfile.TemporaryFile(
            mode="w+",
            encoding="utf-8",
            errors="replace",
        ) as out,
        tempfile.TemporaryFile(
            mode="w+",
            encoding="utf-8",
            errors="replace",
        ) as err,
    ):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            returncode = fn()
        out.seek(0)
        err.seek(0)
        return (
            returncode,
            out.read(MAX_CAPTURE_CHARS),
            err.read(MAX_CAPTURE_CHARS),
        )


def command_preview(argv: list[str]) -> dict:
    return {"would_run": True, "command": argv}


def run_argv(
    argv: list[str],
    *,
    confirm: bool,
    timeout: int | None = None,
) -> dict:
    if not confirm:
        return command_preview(argv)
    try:
        process = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ToolError("command_not_found", str(exc), {"command": argv})
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            "timeout",
            "command timed out",
            {"command": argv, "timeout": exc.timeout},
        )
    result = {
        "command": argv,
        "returncode": process.returncode,
        "stdout": process.stdout or "",
        "stderr": process.stderr or "",
    }
    if process.returncode != 0:
        raise ToolError(
            "command_failed",
            "command exited with status %s" % process.returncode,
            result,
        )
    return result


def read_spooled_text(
    handle,
    max_bytes: int,
    redactor: Callable[[str], str] | None = None,
) -> tuple[str, bool]:
    handle.seek(0)
    read_limit = max_bytes + (4096 if redactor else 1)
    raw = handle.read(read_limit + 1)
    text = raw[:read_limit].decode("utf-8", "replace")
    if redactor is not None:
        text = redactor(text)
    encoded = text.encode("utf-8")
    truncated = len(raw) > read_limit or len(encoded) > max_bytes
    return encoded[:max_bytes].decode("utf-8", "replace"), truncated


def run_argv_spooled(
    argv: list[str],
    *,
    timeout: int | None,
    max_output_bytes: int,
    redactor: Callable[[str], str] | None = None,
) -> dict:
    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        try:
            process = subprocess.run(
                argv,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise ToolError("command_not_found", str(exc), {"command": argv})
        except subprocess.TimeoutExpired as exc:
            raise ToolError(
                "timeout",
                "command timed out",
                {"command": argv, "timeout": exc.timeout},
            )

        stdout, stdout_truncated = read_spooled_text(
            stdout_file,
            max_output_bytes,
            redactor,
        )
        stderr, stderr_truncated = read_spooled_text(
            stderr_file,
            max_output_bytes,
            redactor,
        )
        result = {
            "command": argv,
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        if process.returncode != 0:
            raise ToolError(
                "command_failed",
                "command exited with status %s" % process.returncode,
                result,
            )
        return result
