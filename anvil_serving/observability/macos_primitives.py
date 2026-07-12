"""Reusable, unprivileged macOS host sampling primitives."""

from __future__ import annotations

import platform
import re
import subprocess
from collections.abc import Callable
from typing import Any


Runner = Callable[..., Any]
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def run_command(
    argv: list[str], *, runner: Runner = subprocess.run, timeout: float = 5.0
) -> str:
    """Run a bounded read-only platform command and return stdout."""

    completed = runner(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or "").strip() or f"{argv[0]} failed"
        lowered = message.lower()
        if "permission denied" in lowered or "operation not permitted" in lowered:
            raise PermissionError(message)
        raise OSError(message)
    output = (completed.stdout or "").strip()
    if len(output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise ValueError(f"{argv[0]} output exceeds 4 MiB")
    return output


def macos_identity_snapshot(*, runner: Runner = subprocess.run) -> dict[str, str | None]:
    """Return generic macOS identity fields without requiring elevation."""

    return {
        "platform_node": platform.node() or None,
        "hostname": _optional(["hostname"], runner),
        "computer_name": _optional(["scutil", "--get", "ComputerName"], runner),
        "local_host_name": _optional(["scutil", "--get", "LocalHostName"], runner),
        "hardware_model": _optional(["sysctl", "-n", "hw.model"], runner),
        "os_version": _optional(["sw_vers", "-productVersion"], runner),
    }


def macos_memory_snapshot(*, runner: Runner = subprocess.run) -> dict[str, int | float | str | None]:
    """Return physical/unified-memory and swap values in bytes."""

    total_text = run_command(["sysctl", "-n", "hw.memsize"], runner=runner)
    try:
        total = int(total_text)
    except ValueError as exc:
        raise ValueError("sysctl hw.memsize was not an integer") from exc
    if total <= 0:
        raise ValueError("sysctl hw.memsize was not positive")

    memory = parse_vm_stat(run_command(["vm_stat"], runner=runner), total_bytes=total)
    pressure_text = _optional(["memory_pressure", "-Q"], runner)
    if pressure_text:
        match = re.search(
            r"System-wide memory free percentage:\s*([0-9]+(?:\.[0-9]+)?)%",
            pressure_text,
            re.IGNORECASE,
        )
        if match:
            memory["memory_pressure_percent"] = 100.0 - float(match.group(1))

    swap_text = _optional(["sysctl", "vm.swapusage"], runner)
    memory.update(parse_swapusage(swap_text) if swap_text else _empty_swap())
    memory["source"] = "sysctl/vm_stat/memory_pressure"
    return memory


def parse_vm_stat(text: str, *, total_bytes: int) -> dict[str, int | float]:
    """Parse macOS ``vm_stat`` output without assuming a fixed page size."""

    page_size_match = re.search(r"page size of\s+(\d+)\s+bytes", text)
    page_size = int(page_size_match.group(1)) if page_size_match else 4096
    pages: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"Pages ([^:]+):\s+([0-9]+)\.", line.strip())
        if match:
            pages[match.group(1).strip().lower()] = int(match.group(2))
    if not pages:
        raise ValueError("vm_stat contained no page counters")
    available_pages = sum(
        pages.get(name, 0) for name in ("free", "inactive", "speculative")
    )
    available = available_pages * page_size
    if available > total_bytes:
        raise ValueError("vm_stat available memory exceeds physical memory")
    used = total_bytes - available
    return {
        "memory_total_bytes": total_bytes,
        "memory_available_bytes": available,
        "memory_used_bytes": used,
        "memory_pressure_percent": used / total_bytes * 100.0,
    }


def parse_swapusage(text: str) -> dict[str, int | None]:
    """Parse ``sysctl vm.swapusage`` values such as ``1024.00M``."""

    values: dict[str, int] = {}
    for key in ("total", "used", "free"):
        match = re.search(
            rf"\b{key}\s*=\s*([0-9]+(?:\.[0-9]+)?)([KMGTP])",
            text,
            re.IGNORECASE,
        )
        if match:
            factor = 1024 ** ("KMGTP".index(match.group(2).upper()) + 1)
            values[key] = int(float(match.group(1)) * factor)
    total = values.get("total")
    used = values.get("used")
    free = values.get("free")
    if total is not None and (
        (used is not None and used > total) or (free is not None and free > total)
    ):
        raise ValueError("vm.swapusage values are inconsistent")
    return {
        "swap_total_bytes": total,
        "swap_used_bytes": used,
        "swap_available_bytes": free,
    }


def _optional(argv: list[str], runner: Runner) -> str | None:
    try:
        value = run_command(argv, runner=runner)
    except (FileNotFoundError, OSError, subprocess.SubprocessError, ValueError):
        return None
    return value or None


def _empty_swap() -> dict[str, None]:
    return {
        "swap_total_bytes": None,
        "swap_used_bytes": None,
        "swap_available_bytes": None,
    }
