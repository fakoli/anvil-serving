"""Bounded, identity-pinned observations and plans for user launchd services."""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..operator_output import redact
from .contracts import ServiceError


_READ_TIMEOUT_SECONDS = 5.0
_MAX_SUPERVISOR_OUTPUT = 64 * 1024
_MAX_PLIST_BYTES = 1024 * 1024
_MAX_LOG_LINES = 1_000
_MAX_LOG_LINE_BYTES = 4 * 1024
_MAX_DISCOVERED = 256
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PATH_LINE_RE = re.compile(r"^\s*path\s+=\s+(.+?)\s*$", re.MULTILINE)
_STATE_LINE_RE = re.compile(r"^\s*state\s+=\s+(.+?)\s*$", re.MULTILINE)
_PID_LINE_RE = re.compile(r"^\s*pid\s+=\s+(\d+)\s*$", re.MULTILINE)
_EXIT_LINE_RE = re.compile(r"^\s*last exit code\s+=\s+(-?\d+)\s*$", re.MULTILINE)
_DISABLED_LINE_RE = re.compile(
    r'"([^"\\]{1,128})"\s*=>\s*(true|false|enabled|disabled)', re.IGNORECASE
)
_SECRET_ENV_NAME_RE = re.compile(r"credential|password|secret|token|key", re.IGNORECASE)


Runner = Callable[..., subprocess.CompletedProcess[str]]


class Adapter:
    """A deliberately narrow launchd adapter; callers execute returned plans."""

    def __init__(
        self,
        *,
        run: Runner = subprocess.run,
        discovery_root: Path | None = None,
    ):
        self._run = run
        self._discovery_root = (
            Path.home() / "Library" / "LaunchAgents"
            if discovery_root is None
            else Path(discovery_root)
        )

    def describe(self, binding: dict[str, Any]) -> dict[str, Any]:
        """Return adoption-safe metadata after validating the pinned plist."""
        details = self._binding_details(binding)
        return {
            "manager": "launchd",
            "identity": details["identity"],
            "label": details["label"],
            "definition_sha256": details["definition_sha256"],
            "engine_hint": _engine_hint(details["plist"]),
            "ports": _declared_ports(details["plist"]),
        }

    def inspect(self, binding: dict[str, Any]) -> dict[str, Any]:
        """Read exact launchd registration state without exposing raw output."""
        details = self._binding_details(binding)
        try:
            completed = self._read(["launchctl", "print", details["identity"]])
        except ServiceError as exc:
            state = "unreachable" if exc.code in {"supervisor_timeout", "supervisor_unavailable"} else "unknown"
            return _unavailable_observation(state)
        if completed.returncode != 0:
            if _is_absent(completed):
                return {
                    "manager": "launchd",
                    "identity": details["identity"],
                    "registered": False,
                    "running": False,
                    "enabled": self._enabled(details),
                    "pid": None,
                    "state": "unloaded",
                }
            return _unavailable_observation(_failed_state(completed))

        output = _bounded_text(completed.stdout)
        registered_path = _matched_value(_PATH_LINE_RE, output)
        if registered_path != str(details["definition"]):
            raise ServiceError("identity_mismatch", "launchd registration does not match the declared definition")
        state, running = _state_from_print(output)
        return {
            "manager": "launchd",
            "identity": details["identity"],
            "registered": True,
            "running": running,
            "enabled": self._enabled(details),
            "pid": _pid_from_print(output),
            "state": state,
        }

    def plan(self, binding: dict[str, Any], action: str, observed: dict[str, Any]) -> list[list[str]]:
        """Build non-mutating launchctl commands from a validated observation."""
        details = self._binding_details(binding)
        if action not in {"up", "down", "restart", "enable", "disable"}:
            raise ServiceError("unsupported_action", "service action is not supported by launchd")
        if not isinstance(observed, dict):
            raise ServiceError("invalid_observation", "service observation must be a mapping")

        identity = details["identity"]
        registered_value = observed.get("registered")
        if not isinstance(registered_value, bool):
            raise ServiceError("state_unknown", "launchd registration state is unavailable")
        registered = registered_value
        running = observed.get("running") is True
        enabled = observed.get("enabled")
        if registered and observed.get("identity") != identity:
            raise ServiceError("identity_mismatch", "launchd action requires the exact registered identity")

        if action in {"up", "restart"} and enabled is False:
            raise ServiceError("startup_disabled", "service startup policy is disabled")
        if action == "up":
            if running:
                return []
            if registered:
                return [["launchctl", "kickstart", identity]]
            commands = [
                ["launchctl", "bootstrap", details["domain"], str(details["definition"])]
            ]
            if details["plist"].get("RunAtLoad") is not True:
                commands.append(["launchctl", "kickstart", identity])
            return commands
        if action == "down":
            if not registered:
                return []
            return [["launchctl", "bootout", identity]]
        if action == "restart":
            if registered:
                command = ["launchctl", "kickstart"]
                if running:
                    command.append("-k")
                command.append(identity)
                return [command]
            return [
                ["launchctl", "bootstrap", details["domain"], str(details["definition"])],
                ["launchctl", "kickstart", identity],
            ]
        if action == "enable":
            return [] if enabled is True else [["launchctl", "enable", identity]]
        return [] if enabled is False else [["launchctl", "disable", identity]]

    def logs(self, binding: dict[str, Any], tail: int) -> list[str]:
        """Read a bounded tail from only secure plist-declared log files."""
        details = self._binding_details(binding)
        if isinstance(tail, bool) or not isinstance(tail, int) or not 1 <= tail <= _MAX_LOG_LINES:
            raise ServiceError("invalid_tail", "log tail must be between 1 and 1000")
        lines: list[str] = []
        for key in ("StandardOutPath", "StandardErrorPath"):
            candidate = details["plist"].get(key)
            if not isinstance(candidate, str) or not candidate:
                continue
            path = Path(candidate)
            if not path.is_absolute():
                raise ServiceError("unsafe_log", "declared log path must be absolute")
            if not path.exists():
                continue
            _validate_regular_owned_file(path, details["owner_uid"], error_code="unsafe_log")
            lines.extend(_tail_lines(path, tail))
        secret_values = _environment_secrets()
        return [_sanitize_log_line(line, secret_values) for line in lines[-tail:]]

    def discover(self) -> list[dict[str, Any]]:
        """Merge bounded user-domain registrations with safe LaunchAgent definitions."""
        completed = self._read(["launchctl", "list"])
        if completed.returncode != 0:
            self._raise_supervisor_error(completed)
        domain = _domain_for_uid(os.getuid())
        registered: dict[str, dict[str, Any]] = {}
        for line in _bounded_text(completed.stdout).splitlines():
            fields = line.split("\t")
            if len(fields) != 3:
                continue
            pid_raw, _status, label = (field.strip() for field in fields)
            if (pid_raw, _status, label) == ("PID", "Status", "Label"):
                continue
            if not _safe_label(label):
                continue
            pid = int(pid_raw) if pid_raw.isdecimal() and int(pid_raw) > 0 else None
            running = True if pid is not None else False if pid_raw == "-" else None
            registered.setdefault(
                label,
                {
                    "manager": "launchd",
                    "identity": f"{domain}/{label}",
                    "label": label,
                    "pid": pid,
                    "state": "running" if running is True else "waiting" if running is False else "unknown",
                    "registered": True,
                    "running": running,
                    "engine_hint": "unknown",
                    "ports": [],
                    "definition_sha256": None,
                    "eligible_for_adoption": False,
                },
            )
            if len(registered) >= _MAX_DISCOVERED:
                break
        definitions = _discover_definitions(self._discovery_root, os.getuid())
        for label, (metadata, binding) in definitions.items():
            try:
                observed = self.inspect(binding)
            except ServiceError:
                observed = _unavailable_observation("unknown")
            known = isinstance(observed.get("registered"), bool)
            registered[label] = {
                "manager": "launchd",
                "identity": f"{domain}/{label}",
                "label": label,
                "pid": observed.get("pid") if isinstance(observed.get("pid"), int) else None,
                "state": observed.get("state", "unknown"),
                "registered": observed.get("registered") if known else None,
                "running": observed.get("running") if known else None,
                **metadata,
                "eligible_for_adoption": known,
            }
        records = list(registered.values())
        records.sort(
            key=lambda record: (
                record["label"] not in definitions,
                not record["eligible_for_adoption"],
                record["label"].casefold(),
                record["label"],
            )
        )
        return records[:_MAX_DISCOVERED]

    def _binding_details(self, binding: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(binding, dict) or binding.get("manager") != "launchd":
            raise ServiceError("invalid_binding", "binding must declare launchd as its manager")
        label = binding.get("label")
        if not isinstance(label, str) or not _safe_label(label):
            raise ServiceError("invalid_binding", "launchd binding label is invalid")
        owner_uid = binding.get("owner_uid")
        if isinstance(owner_uid, bool) or not isinstance(owner_uid, int) or owner_uid != os.getuid():
            raise ServiceError("owner_mismatch", "launchd binding owner does not match the current user")
        raw_definition = binding.get("definition")
        if not isinstance(raw_definition, str):
            raise ServiceError("invalid_binding", "launchd binding definition is required")
        definition = Path(raw_definition)
        if not definition.is_absolute():
            raise ServiceError("unsafe_definition", "launchd definition must be an absolute path")
        _validate_regular_owned_file(definition, owner_uid, error_code="unsafe_definition")
        size = definition.stat().st_size
        if size > _MAX_PLIST_BYTES:
            raise ServiceError("unsafe_definition", "launchd definition exceeds the allowed size")
        try:
            raw = definition.read_bytes()
        except OSError as exc:
            raise ServiceError("invalid_definition", "launchd definition is not readable") from exc
        digest = binding.get("definition_sha256")
        actual_digest = hashlib.sha256(raw).hexdigest()
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ServiceError("invalid_binding", "launchd definition hash is invalid")
        if digest != actual_digest:
            raise ServiceError("definition_changed", "launchd definition hash has changed")
        try:
            plist = plistlib.loads(raw)
        except (ValueError, plistlib.InvalidFileException) as exc:
            raise ServiceError("invalid_definition", "launchd definition is not a readable plist") from exc
        if not isinstance(plist, dict) or plist.get("Label") != label:
            raise ServiceError("identity_mismatch", "launchd definition label does not match the binding")
        domain = _domain_for_uid(owner_uid)
        return {
            "label": label,
            "owner_uid": owner_uid,
            "definition": definition,
            "definition_sha256": actual_digest,
            "identity": f"{domain}/{label}",
            "domain": domain,
            "plist": plist,
        }

    def _enabled(self, details: dict[str, Any]) -> bool | None:
        completed = self._read(["launchctl", "print-disabled", details["domain"]])
        if completed.returncode != 0:
            return None
        for label, disabled in _DISABLED_LINE_RE.findall(_bounded_text(completed.stdout)):
            if label == details["label"]:
                return disabled.lower() in {"false", "enabled"}
        return True

    def _read(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self._run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=_READ_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ServiceError("supervisor_timeout", "launchd did not respond before the timeout") from exc
        except OSError as exc:
            raise ServiceError("supervisor_unavailable", "launchctl is unavailable") from exc

    @staticmethod
    def _raise_supervisor_error(completed: subprocess.CompletedProcess[str]) -> None:
        detail = "\n".join((_bounded_text(completed.stdout), _bounded_text(completed.stderr))).lower()
        if "not permitted" in detail or "permission denied" in detail:
            raise ServiceError("supervisor_inaccessible", "launchd access was denied")
        raise ServiceError("supervisor_unknown", "launchd did not return a usable service observation")


def _discover_definitions(
    root: Path, owner_uid: int
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    paths: list[Path] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if len(paths) >= _MAX_DISCOVERED:
                    break
                try:
                    if entry.name.endswith(".plist") and entry.is_file(follow_symlinks=False):
                        paths.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return {}

    definitions: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    ambiguous: set[str] = set()
    for path in sorted(paths, key=lambda candidate: (candidate.name.casefold(), candidate.name)):
        candidate = _definition_metadata(path, owner_uid)
        if candidate is None:
            continue
        label, metadata = candidate
        if label in definitions:
            definitions.pop(label, None)
            ambiguous.add(label)
        elif label not in ambiguous:
            definitions[label] = metadata
    return definitions


def _definition_metadata(
    path: Path, owner_uid: int
) -> tuple[str, tuple[dict[str, Any], dict[str, Any]]] | None:
    try:
        _validate_regular_owned_file(path, owner_uid, error_code="unsafe_definition")
        if path.stat().st_size > _MAX_PLIST_BYTES:
            return None
        with path.open("rb") as handle:
            raw = handle.read(_MAX_PLIST_BYTES + 1)
        if len(raw) > _MAX_PLIST_BYTES:
            return None
        plist = plistlib.loads(raw)
    except (OSError, ServiceError, ValueError, plistlib.InvalidFileException):
        return None
    label = plist.get("Label") if isinstance(plist, dict) else None
    if (
        not isinstance(label, str)
        or not _safe_label(label)
        or path.name != f"{label}.plist"
    ):
        return None
    digest = hashlib.sha256(raw).hexdigest()
    return label, (
        {
            "engine_hint": _engine_hint(plist),
            "ports": _declared_ports(plist),
            "definition_sha256": digest,
        },
        {
            "manager": "launchd",
            "label": label,
            "owner_uid": owner_uid,
            "definition": str(path),
            "definition_sha256": digest,
        },
    )


def _validate_regular_owned_file(path: Path, owner_uid: int, *, error_code: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ServiceError(error_code, "declared file is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ServiceError(error_code, "declared file must be a regular non-symlink file")
    if info.st_uid != owner_uid or info.st_mode & 0o022:
        raise ServiceError(error_code, "declared file has unsafe ownership or permissions")


def _domain_for_uid(uid: int) -> str:
    return "system" if uid == 0 else f"gui/{uid}"


def _safe_label(value: str) -> bool:
    return bool(_LABEL_RE.fullmatch(value))


def _bounded_text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str):
        return ""
    return value[:_MAX_SUPERVISOR_OUTPUT]


def _is_absent(completed: subprocess.CompletedProcess[str]) -> bool:
    detail = "\n".join((_bounded_text(completed.stdout), _bounded_text(completed.stderr))).lower()
    return completed.returncode == 113 or "could not find service" in detail or "no such process" in detail


def _failed_state(completed: subprocess.CompletedProcess[str]) -> str:
    detail = "\n".join((_bounded_text(completed.stdout), _bounded_text(completed.stderr))).lower()
    if "not permitted" in detail or "permission denied" in detail:
        return "inaccessible"
    return "unknown"


def _unavailable_observation(state: str) -> dict[str, Any]:
    return {
        "manager": "launchd",
        "identity": None,
        "registered": None,
        "running": None,
        "enabled": None,
        "pid": None,
        "state": state,
    }


def _matched_value(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1).strip().strip('"')


def _state_from_print(output: str) -> tuple[str, bool | None]:
    raw_state = _matched_value(_STATE_LINE_RE, output)
    if raw_state == "running":
        return "running", True
    if raw_state in {"waiting", "exited", "stopped", "not running", "spawn scheduled"}:
        exit_code = _matched_value(_EXIT_LINE_RE, output)
        if exit_code not in {None, "0"}:
            return "failed", False
        return "waiting", False
    return "unknown", None


def _pid_from_print(output: str) -> int | None:
    value = _matched_value(_PID_LINE_RE, output)
    if value is None:
        return None
    pid = int(value)
    return pid if pid > 0 else None


def _engine_hint(plist: dict[str, Any]) -> str:
    arguments = plist.get("ProgramArguments")
    if not isinstance(arguments, list) or not all(isinstance(value, str) for value in arguments):
        return "unknown"
    normalized = " ".join(arguments).lower()
    if "mlx_lm" in normalized:
        return "mlx_lm"
    if "mlx_vlm" in normalized:
        return "mlx_vlm"
    if "parakeet" in normalized:
        return "parakeet"
    if "uvicorn" in normalized and "kokoro" in normalized:
        return "kokoro"
    if "anvil-serving" in normalized and "controller" in normalized:
        return "anvil_controller"
    if "anvil-serving" in normalized and "events" in normalized:
        return "anvil_events"
    return "unknown"


def _declared_ports(plist: dict[str, Any]) -> list[int]:
    arguments = plist.get("ProgramArguments")
    if not isinstance(arguments, list) or not all(isinstance(value, str) for value in arguments):
        return []
    ports: list[int] = []
    for index, argument in enumerate(arguments):
        value: str | None = None
        if argument.startswith("--port="):
            value = argument.removeprefix("--port=")
        elif argument == "--port" and index + 1 < len(arguments):
            value = arguments[index + 1]
        if value is not None and value.isdecimal():
            port = int(value)
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)
    return ports


def _tail_lines(path: Path, tail: int) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        chunks: list[bytes] = []
        newlines = 0
        remaining = tail * (_MAX_LOG_LINE_BYTES + 1)
        while position > 0 and newlines <= tail and remaining > 0:
            size = min(4096, position, remaining)
            position -= size
            remaining -= size
            handle.seek(position)
            chunk = handle.read(size)
            chunks.append(chunk)
            newlines += chunk.count(b"\n")
        raw_lines = b"".join(reversed(chunks)).splitlines()[-tail:]
    return [_decode_bounded_line(line) for line in raw_lines]


def _decode_bounded_line(line: bytes) -> str:
    text = line[-_MAX_LOG_LINE_BYTES:].decode("utf-8", "replace")
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_LOG_LINE_BYTES:
        text = encoded[-_MAX_LOG_LINE_BYTES:].decode("utf-8", "ignore")
    return text


def _environment_secrets() -> tuple[str, ...]:
    return tuple(
        value
        for name, value in os.environ.items()
        if value and _SECRET_ENV_NAME_RE.search(name)
    )


def _sanitize_log_line(line: str, secrets: tuple[str, ...]) -> str:
    return str(redact(line, secrets=secrets))
