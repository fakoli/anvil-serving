"""Closed, owner-declared Docker diagnostics for the Workbench.

The browser selects a configured diagnostic ID.  It never supplies a container,
shell, environment, user, working directory, or executable argument.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from ..operator_output import redact


_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")
_CONTAINER = re.compile(r"[a-f0-9]{64}\Z")
_MAX_TIMEOUT = 30
_MAX_OUTPUT = 64 * 1024
_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"})


class ContainerExecError(ValueError):
    """A public, non-diagnostic contract error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _id(value: object, field: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ContainerExecError("invalid_binding", f"{field} must be a declared identifier")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class ContainerExec:
    """Execute only a pinned diagnostic argv in a pinned managed container."""

    def __init__(self, bindings: Sequence[Mapping[str, Any]], *, popen: Callable[..., Any] = subprocess.Popen):
        if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)) or len(bindings) > 128:
            raise ValueError("container exec bindings must be a bounded list")
        parsed = [self._binding(item) for item in bindings]
        self._bindings = {item["id"]: item for item in parsed}
        if len(self._bindings) != len(bindings):
            raise ValueError("container exec binding IDs must be unique")
        self._popen = popen

    @staticmethod
    def _binding(raw: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError("container exec binding must be an object")
        allowed = {"id", "host_id", "execution_runtime", "service", "container_id", "commands", "timeout_seconds", "max_output_bytes"}
        if set(raw) - allowed or not {"id", "host_id", "execution_runtime", "service", "container_id", "commands"} <= set(raw):
            raise ValueError("container exec binding fields are invalid")
        container = raw["container_id"]
        if type(container) is not str or not _CONTAINER.fullmatch(container):
            raise ValueError("container exec requires an immutable container ID")
        commands = raw["commands"]
        if not isinstance(commands, Mapping) or not commands or len(commands) > 32:
            raise ValueError("container exec requires a bounded command catalog")
        clean = {}
        for name, argv in commands.items():
            _id(name, "command")
            if not isinstance(argv, list) or not 1 <= len(argv) <= 16 or any(type(part) is not str or not part or len(part) > 256 or "\x00" in part for part in argv):
                raise ValueError("container exec command argv is invalid")
            if argv[0].rsplit("/", 1)[-1].lower() in _SHELLS or any(part.startswith("-") for part in argv):
                raise ValueError("container exec command cannot use a shell or flags")
            clean[name] = list(argv)
        timeout = raw.get("timeout_seconds", 10)
        maximum = raw.get("max_output_bytes", _MAX_OUTPUT)
        if type(timeout) is not int or not 1 <= timeout <= _MAX_TIMEOUT or type(maximum) is not int or not 1 <= maximum <= _MAX_OUTPUT:
            raise ValueError("container exec limits are invalid")
        return {"id": _id(raw["id"], "binding"), "host_id": _id(raw["host_id"], "host"),
                "execution_runtime": _id(raw["execution_runtime"], "runtime"), "service": _id(raw["service"], "service"),
                "container_id": container, "commands": clean, "timeout_seconds": timeout, "max_output_bytes": maximum}

    def read_services(self, resource_id: str | None = None) -> list[dict[str, Any]]:
        rows = self._bindings.values() if resource_id is None else [self._get(resource_id)]
        return [{"id": row["id"], "host_id": row["host_id"], "execution_runtime": row["execution_runtime"], "service": row["service"], "container_id": row["container_id"],
                 "exec": {"status": "available", "commands": sorted(row["commands"]), "timeout_seconds": row["timeout_seconds"], "max_output_bytes": row["max_output_bytes"]}}
                for row in rows]

    def preview(self, resource_id: str, command_id: str) -> dict[str, Any]:
        row, command = self._get(resource_id), _id(command_id, "command")
        if command not in row["commands"]:
            raise ContainerExecError("unsupported_command", "Select a declared container diagnostic.")
        identity = {"resource_id": row["id"], "host_id": row["host_id"], "execution_runtime": row["execution_runtime"], "service": row["service"], "container_id": row["container_id"],
                    "command_id": command, "argv": row["commands"][command], "timeout_seconds": row["timeout_seconds"], "max_output_bytes": row["max_output_bytes"]}
        return {**identity, "policy_digest": hashlib.sha256(_canonical(row)).hexdigest(),
                "candidate_digest": hashlib.sha256(_canonical(identity)).hexdigest(),
                "effect": "Runs the declared diagnostic in the reviewed managed container.", "execution": "container"}

    def execute(self, preview: Mapping[str, Any], *, host_id: object, execution_runtime: object) -> dict[str, Any]:
        if not isinstance(preview, Mapping):
            raise ContainerExecError("invalid_preview", "Review a declared container diagnostic first.")
        current = self.preview(preview.get("resource_id"), preview.get("command_id"))
        if host_id != current["host_id"] or execution_runtime != current["execution_runtime"]:
            raise ContainerExecError("owner_mismatch", "Container diagnostics run only on their declared owner runtime.")
        if any(preview.get(key) != current[key] for key in ("host_id", "execution_runtime", "service", "container_id", "argv", "timeout_seconds", "max_output_bytes", "policy_digest", "candidate_digest")):
            raise ContainerExecError("stale_preview", "Container identity or diagnostic policy changed; review again.")
        argv = ["docker", "exec", "--", current["container_id"], *current["argv"]]
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                process = self._popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                code = process.wait(timeout=current["timeout_seconds"])
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1)
                except (subprocess.TimeoutExpired, TypeError):
                    pass
                return {"status": "timeout", "exit_code": None, "output": "", "truncated": False}
            except OSError:
                return {"status": "unavailable", "exit_code": None, "output": "", "truncated": False}
            stdout.seek(0); stderr.seek(0)
            limit = current["max_output_bytes"]
            data = stdout.read(limit + 1) + stderr.read(limit + 1)
            truncated = len(data) > limit
            return {"status": "completed" if code == 0 else "failed", "exit_code": code,
                    "output": redact(data[:limit].decode("utf-8", "replace")), "truncated": truncated}

    def _get(self, resource_id: object) -> dict[str, Any]:
        try:
            return self._bindings[_id(resource_id, "resource")]
        except KeyError:
            raise ContainerExecError("unknown_resource", "Select a declared managed container.") from None


def load_config(path: str | Path) -> ContainerExec:
    """Load only the private, owner-managed diagnostic catalog."""
    try:
        with Path(path).open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ContainerExecError("config_unavailable", "Container diagnostics are not configured on this owner.") from exc
    if not isinstance(raw, dict) or set(raw) != {"bindings"}:
        raise ContainerExecError("invalid_config", "Container diagnostic configuration is invalid.")
    return ContainerExec(raw["bindings"])
