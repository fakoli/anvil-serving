"""Read-only inventory and sanitized export of operator-owned configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import stat
import sys
import tomllib
import urllib.parse
from pathlib import Path
from typing import Any

from . import __version__
from .paths import config_home


DEFAULT_MAX_BYTES = 1024 * 1024
DEFAULT_GATEWAY_PATH = "~/.openclaw/openclaw.json"
_VERSIONABLE_NAMES = {
    ".env.example",
    "operator-topology.toml",
    "voice.toml",
    "host.toml",
    "router.toml",
    "serves.toml",
    "serve-recipes.toml",
    "edge.toml",
}
_VERSIONABLE_SUFFIXES = (".toml", ".json", ".yaml", ".yml")
_SECRET_PARTS = {"secrets", "credentials", "identity"}
_RUNTIME_SUFFIXES = (".sqlite", ".sqlite3", ".db", ".log", ".pid")
_CACHE_PARTS = {"cache", "caches", "tmp", "temp", "__pycache__"}
_DEPENDENCY_KEYS = {
    "router_config",
    "rollback_router_config",
    "topology",
    "topology_overlay",
    "manifest",
    "voice_config",
    "compose_file",
}
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|credential)(?:$|_)", re.IGNORECASE
)
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


class ConfigExportError(ValueError):
    """A candidate cannot be inventoried or exported safely."""


def default_gateway_path() -> str | None:
    """Return the standard OpenClaw gateway path only when it exists as a file."""

    candidate = Path(DEFAULT_GATEWAY_PATH).expanduser()
    return str(candidate) if candidate.is_file() else None


def _resolved_home(home: str | os.PathLike[str] | None) -> Path:
    selected = Path(home or config_home()).expanduser().absolute()
    if selected.is_symlink():
        raise ConfigExportError(
            f"operator config home must not be a symlink: {selected}"
        )
    root = selected.resolve(strict=False)
    if not root.is_dir():
        raise ConfigExportError(f"operator config home is not a directory: {root}")
    return root


def _classification(relative: Path) -> str:
    lower_parts = tuple(part.lower() for part in relative.parts)
    name = relative.name.lower()
    if ".anvil.bak." in name or name.endswith((".bak", ".backup")):
        return "backup"
    if any(part in _CACHE_PARTS for part in lower_parts) or name.endswith(
        (".lock", ".tmp", ".pyc")
    ):
        return "cache"
    if (
        name == ".env"
        or name == "openclaw.json"
        or any(part in _SECRET_PARTS for part in lower_parts)
        or any(token in name for token in ("secret", "credential", "device-auth"))
        or ("token" in name and not name.endswith(".example"))
    ):
        return "secret"
    if name.endswith(_RUNTIME_SUFFIXES) or any(
        token in name for token in ("operation-state", "controller-operations")
    ):
        return "runtime"
    if name in _VERSIONABLE_NAMES or name.endswith(_VERSIONABLE_SUFFIXES):
        return "versionable"
    return "unknown"


def _parser_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".toml":
        return "toml"
    if suffix == ".json":
        return "json"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if path.name.lower().startswith(".env"):
        return "env"
    return "binary"


def _read_bounded(path: Path, *, max_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConfigExportError(f"could not inspect candidate {path.name}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ConfigExportError(f"candidate must not be a symlink: {path.name}")
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigExportError(f"candidate must be a regular file: {path.name}")
    if metadata.st_size > max_bytes:
        raise ConfigExportError(
            f"candidate exceeds the {max_bytes}-byte size limit: {path.name}"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ConfigExportError(f"candidate is unreadable: {path.name}: {exc}") from exc


def _parse(path: Path, data: bytes, parser: str) -> Any:
    if parser == "binary":
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigExportError(f"candidate is not UTF-8 text: {path.name}") from exc
    try:
        if parser == "toml":
            return tomllib.loads(text)
        if parser == "json":
            return json.loads(text)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        raise ConfigExportError(f"candidate does not parse as {parser}: {path.name}: {exc}") from exc
    return text


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _dependency_values(value: Any, *, key: str = ""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _dependency_values(child, key=str(child_key).lower())
    elif isinstance(value, list):
        for child in value:
            yield from _dependency_values(child, key=key)
    elif isinstance(value, str) and key in _DEPENDENCY_KEYS:
        yield value
    elif isinstance(value, str) and key == "up":
        try:
            arguments = shlex.split(value)
        except ValueError as exc:
            raise ConfigExportError(f"could not parse lifecycle command: {exc}") from exc
        for index, argument in enumerate(arguments[:-1]):
            if argument in {"-f", "--file"}:
                yield arguments[index + 1]


def _dependencies(root: Path, source: Path, parsed: Any) -> list[dict[str, str]]:
    edges = []
    if not isinstance(parsed, (dict, list)):
        return edges
    for raw in _dependency_values(parsed):
        expanded = raw.replace("{dir}", str(source.parent))
        target = Path(expanded).expanduser()
        if not target.is_absolute():
            target = source.parent / target
        target = target.resolve(strict=False)
        if not _inside(root, target):
            raise ConfigExportError(
                f"dependency outside approved root: {source.name} -> {raw}"
            )
        if target.is_symlink():
            raise ConfigExportError(
                f"dependency must not be a symlink: {source.name} -> {raw}"
            )
        if not target.is_file():
            raise ConfigExportError(
                f"unresolved dependency: {source.name} -> {raw}"
            )
        edges.append(
            {
                "source": source.relative_to(root).as_posix(),
                "target": target.relative_to(root).as_posix(),
            }
        )
    return edges


def _installed_revisions() -> dict[str, str]:
    from .mcp import PROTOCOL_VERSION

    return {
        "anvil_serving": __version__,
        "packaged_mcp_bridge": __version__,
        "mcp_protocol": PROTOCOL_VERSION,
        "python": platform.python_version(),
    }


def _inventory_rows(root: Path, *, max_bytes: int) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    dependency_edges: list[dict] = []
    try:
        candidates = sorted(root.rglob("*"), key=lambda path: path.as_posix().lower())
    except OSError as exc:
        raise ConfigExportError(f"could not enumerate operator config home: {exc}") from exc
    for path in candidates:
        if path.is_symlink():
            relative = path.relative_to(root).as_posix()
            raise ConfigExportError(f"candidate must not be a symlink: {relative}")
        if path.is_dir():
            continue
        relative = path.relative_to(root)
        data = _read_bounded(path, max_bytes=max_bytes)
        parser = _parser_type(path)
        classification = _classification(relative)
        parsed = _parse(path, data, parser) if classification == "versionable" else None
        edges = _dependencies(root, path, parsed)
        dependency_edges.extend(edges)
        rows.append(
            {
                "path": relative.as_posix(),
                "classification": classification,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "parser": parser,
                "dependencies": [edge["target"] for edge in edges],
            }
        )
    return rows, dependency_edges


def inventory(
    home: str | os.PathLike[str] | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    """Return metadata-only classification for one effective operator home."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ConfigExportError("max_bytes must be a positive integer")
    root = _resolved_home(home)
    rows, edges = _inventory_rows(root, max_bytes=max_bytes)
    return {
        "schema": "operator-config-inventory/v1",
        "effective_home": str(root),
        "read_only": True,
        "files": rows,
        "dependency_edges": edges,
        "installed_revisions": _installed_revisions(),
    }


def _safe_secret_reference(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("source") in {"env", "file"}
        and isinstance(value.get("id"), str)
        and bool(value["id"].strip())
        and set(value) <= {"source", "provider", "id"}
    )


def _assert_no_secret_literals(value: Any, *, path: str, key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            child_name = str(child_key)
            normalized = child_name.lower().replace("-", "_")
            if _SECRET_KEY_RE.search(normalized) and not normalized.endswith("_env"):
                if _safe_secret_reference(child):
                    continue
                raise ConfigExportError(
                    f"versionable config contains a secret-like field without a SecretRef: "
                    f"{path}:{child_name}"
                )
            _assert_no_secret_literals(child, path=path, key=child_name)
    elif isinstance(value, list):
        for child in value:
            _assert_no_secret_literals(child, path=path, key=key)
    elif isinstance(value, str) and key.lower().endswith("_env"):
        if not _ENV_NAME_RE.fullmatch(value):
            raise ConfigExportError(
                f"versionable config has an invalid environment reference: {path}:{key}"
            )
    elif isinstance(value, str):
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme and (parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ConfigExportError(
                f"versionable config contains a capability-bearing URL: {path}:{key}"
            )


def _assert_text_config_safe(text: str, *, parser: str, path: str) -> None:
    if parser == "env":
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            normalized = name.strip().lower()
            value = value.strip()
            if _SECRET_KEY_RE.search(normalized) and value and not (
                value.startswith("${") or value.startswith("<")
            ):
                raise ConfigExportError(
                    f"versionable config contains a secret-like field: {path}:{name.strip()}"
                )
        return
    if parser != "yaml":
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, value = line.split(":", 1)
        normalized = name.strip().lower().replace("-", "_")
        value = value.strip().strip("'\"")
        if normalized.endswith("_env"):
            if value and not _ENV_NAME_RE.fullmatch(value):
                raise ConfigExportError(
                    f"versionable config has an invalid environment reference: "
                    f"{path}:{name.strip()}"
                )
        elif _SECRET_KEY_RE.search(normalized) and value:
            raise ConfigExportError(
                f"versionable config contains a secret-like field: {path}:{name.strip()}"
            )


def _sanitize_gateway(value: Any, *, key: str = "") -> tuple[Any, int]:
    if _safe_secret_reference(value):
        return dict(value), 0
    if isinstance(value, dict):
        if key.lower() == "env":
            return {str(name): "<redacted>" for name in sorted(value)}, len(value)
        result = {}
        count = 0
        for child_key, child in value.items():
            normalized = str(child_key).lower().replace("-", "_")
            if _SECRET_KEY_RE.search(normalized) and not normalized.endswith("_env"):
                if _safe_secret_reference(child):
                    result[child_key] = dict(child)
                    continue
                result[child_key] = "<redacted>"
                count += 1
                continue
            sanitized, child_count = _sanitize_gateway(child, key=str(child_key))
            result[child_key] = sanitized
            count += child_count
        return result, count
    if isinstance(value, list):
        result = []
        count = 0
        for child in value:
            sanitized, child_count = _sanitize_gateway(child, key=key)
            result.append(sanitized)
            count += child_count
        return result, count
    if isinstance(value, str) and key.lower().endswith(("url", "uri")):
        parsed = urllib.parse.urlsplit(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return "<redacted-capability-url>", 1
    return value, 0


def _gateway_fragment(path: Path, *, max_bytes: int) -> tuple[dict, dict, int]:
    if path.name.lower() != "openclaw.json":
        raise ConfigExportError("gateway configuration must be named openclaw.json")
    if path.is_symlink():
        raise ConfigExportError("gateway configuration must not be a symlink")
    data = _read_bounded(path, max_bytes=max_bytes)
    parsed = _parse(path, data, "json")
    if not isinstance(parsed, dict):
        raise ConfigExportError("gateway configuration must be a JSON object")
    fragment: dict[str, Any] = {}
    providers = parsed.get("models", {}).get("providers", {})
    if isinstance(providers, dict) and "anvil" in providers:
        fragment["models"] = {"providers": {"anvil": providers["anvil"]}}
    models = parsed.get("agents", {}).get("defaults", {}).get("models", {})
    if isinstance(models, dict):
        selected = {name: value for name, value in models.items() if name.startswith("anvil/")}
        if selected:
            fragment["agents"] = {"defaults": {"models": selected}}
    talk = parsed.get("talk")
    if isinstance(talk, dict):
        selected_talk = {
            key: talk[key]
            for key in (
                "consultModel",
                "consultThinkingLevel",
                "consultBootstrapContextMode",
            )
            if key in talk
        }
        realtime = talk.get("realtime")
        if isinstance(realtime, dict):
            selected_realtime = {}
            if realtime.get("provider") == "anvil":
                selected_realtime["provider"] = "anvil"
            realtime_providers = realtime.get("providers")
            if isinstance(realtime_providers, dict) and "anvil" in realtime_providers:
                selected_realtime["providers"] = {"anvil": realtime_providers["anvil"]}
            if selected_realtime:
                selected_talk["realtime"] = selected_realtime
        if selected_talk:
            fragment["talk"] = selected_talk
    servers = parsed.get("mcpServers")
    if isinstance(servers, dict):
        selected_servers = {
            name: value for name, value in servers.items()
            if str(name).lower() in {"anvil", "anvil-serving", "anvil_controller"}
        }
        if selected_servers:
            fragment["mcpServers"] = selected_servers
    sanitized, redactions = _sanitize_gateway(fragment)
    metadata = {
        "path": path.name,
        "classification": "versionable",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "parser": "json",
    }
    return sanitized, metadata, redactions


def export(
    home: str | os.PathLike[str] | None = None,
    *,
    gateway_path: str | os.PathLike[str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    """Return exact safe config files and an allowlisted, redacted gateway fragment."""

    report = inventory(home, max_bytes=max_bytes)
    root = Path(report["effective_home"])
    exported = []
    for row in report["files"]:
        if row["classification"] != "versionable":
            continue
        path = root / Path(row["path"])
        data = _read_bounded(path, max_bytes=max_bytes)
        parsed = _parse(path, data, row["parser"])
        _assert_no_secret_literals(parsed, path=row["path"])
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigExportError(
                f"versionable config is not UTF-8 text: {row['path']}"
            ) from exc
        _assert_text_config_safe(content, parser=row["parser"], path=row["path"])
        exported.append({**row, "content": content})
    fragment = {}
    gateway_metadata = None
    redaction_count = 0
    if gateway_path:
        resolved_gateway = Path(gateway_path).expanduser().resolve(strict=False)
        fragment, gateway_metadata, redaction_count = _gateway_fragment(
            resolved_gateway, max_bytes=max_bytes
        )
    return {
        "schema": "operator-config-export/v1",
        "effective_home": report["effective_home"],
        "read_only": True,
        "files": exported,
        "dependency_edges": report["dependency_edges"],
        "gateway_fragment": fragment,
        "gateway_metadata": gateway_metadata,
        "installed_revisions": report["installed_revisions"],
        "redaction_count": redaction_count,
        "excluded_counts": {
            classification: sum(
                row["classification"] == classification for row in report["files"]
            )
            for classification in ("secret", "runtime", "backup", "cache", "unknown")
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anvil-serving host config")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "export"):
        command = subparsers.add_parser(name)
        command.add_argument("--home")
        command.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
        if name == "export":
            command.add_argument("--gateway-path")
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            result = inventory(args.home, max_bytes=args.max_bytes)
        else:
            result = export(
                args.home,
                gateway_path=args.gateway_path,
                max_bytes=args.max_bytes,
            )
    except ConfigExportError as exc:
        print(f"[anvil-serving] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
