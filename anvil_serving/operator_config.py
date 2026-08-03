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
_VERSIONABLE_NAME_RE = re.compile(
    r"^(?:anvil-router|router|serves|voice|host|operator-topology|serve-recipes)"
    r"(?:\.[a-z0-9][a-z0-9._-]*)?\.toml$"
)
_UNSUPPORTED_VERSIONABLE_SUFFIXES = (".yaml", ".yml")
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
    r"(?:^|_)(?:api_?key|token|secret|password|credential|"
    r"authorization|proxy_authorization|cookie|set_cookie|private_?key)(?:$|_)",
    re.IGNORECASE,
)
_ENV_NAME_RE = re.compile(r"^(?=.{1,128}$)[A-Z_][A-Z0-9_]*$")
_REFERENCE_PROVIDER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_JSON_POINTER_RE = re.compile(
    r"^/(?:[^~/\x00-\x1f]|~[01])+(?:/(?:[^~/\x00-\x1f]|~[01])+)*$"
)
_AUTH_VALUE_RE = re.compile(r"^\s*(?:bearer|basic)\s+\S+\s*$", re.IGNORECASE)
_HEADER_CREDENTIAL_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:[a-z0-9-]*(?:authorization|token|api-?key|cookie))"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_CLI_SECRET_ARG_RE = re.compile(
    r"(?:^|\s)--?(?:api-?key|token|cookie|authorization)\s+\S+", re.IGNORECASE
)
_PRIVATE_KEY_RE = re.compile(
    r"(?:-{4,5}\s*BEGIN (?:PGP |SSH2 )?(?:ENCRYPTED )?PRIVATE KEY"
    r"(?: BLOCK)?\s*-{4,5}|PuTTY-User-Key-File-[23]:)",
    re.IGNORECASE,
)
_URL_CANDIDATE_RE = re.compile(
    r"(?:[a-z][a-z0-9+.-]*:|//|/|\?)[^\s'\"<>]+", re.IGNORECASE
)


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
        or any(
            token in name
            for token in ("secret", "credential", "device-auth", "cookie")
        )
        or ("token" in name and not name.endswith(".example"))
    ):
        return "secret"
    if name.endswith(_RUNTIME_SUFFIXES) or any(
        token in name for token in ("operation-state", "controller-operations")
    ):
        return "runtime"
    if name.endswith(_UNSUPPORTED_VERSIONABLE_SUFFIXES):
        return "unsupported"
    if name in _VERSIONABLE_NAMES or _VERSIONABLE_NAME_RE.fullmatch(name):
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


def _assert_no_link_components(path: Path, *, label: str) -> None:
    current = path
    while True:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ConfigExportError(f"could not inspect {label} path component: {exc}") from exc
        else:
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            file_attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                reparse_flag and file_attributes & reparse_flag
            ):
                raise ConfigExportError(f"{label} path must not contain a symlink or junction")
        if current.parent == current:
            return
        current = current.parent


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


def _normalized_key(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def _is_secret_key(value: Any) -> bool:
    return bool(_SECRET_KEY_RE.search(_normalized_key(value)))


def _looks_like_secret_reference(value: Any) -> bool:
    return isinstance(value, dict) and (
        value.get("source") in {"env", "file"}
        or ({"source", "id"} <= set(value) and set(value) <= {"source", "provider", "id"})
    )


def _safe_secret_reference(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"source", "provider", "id"}:
        return False
    source = value.get("source")
    provider = value.get("provider")
    reference_id = value.get("id")
    if (
        source not in {"env", "file"}
        or not isinstance(provider, str)
        or not _REFERENCE_PROVIDER_RE.fullmatch(provider)
        or not isinstance(reference_id, str)
    ):
        return False
    if source == "env":
        return bool(_ENV_NAME_RE.fullmatch(reference_id))
    return len(reference_id) <= 256 and bool(_JSON_POINTER_RE.fullmatch(reference_id))


def _is_capability_url(value: str) -> bool:
    decoded = value
    for _ in range(2):
        next_value = urllib.parse.unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    for match in _URL_CANDIDATE_RE.finditer(decoded):
        candidate = match.group(0).rstrip("),];}")
        try:
            parsed = urllib.parse.urlsplit(candidate)
        except ValueError:
            return True
        if (
            parsed.username
            or parsed.password
            or (parsed.query and "=" in parsed.query)
            or parsed.fragment
            or re.search(r";[^;/?#=]+=", parsed.path)
        ):
            return True
    return False


def _looks_like_secret_literal(value: str) -> bool:
    return bool(
        _AUTH_VALUE_RE.search(value)
        or _HEADER_CREDENTIAL_RE.search(value)
        or _CLI_SECRET_ARG_RE.search(value)
        or _PRIVATE_KEY_RE.search(value)
    )


def _header_pair(value: Any) -> tuple[str, Any] | None:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], str)
    ):
        return value[0], value[1]
    return None


def _is_header_collection_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    return normalized in {"headers", "header_pairs", "headers_list"} or normalized.endswith(
        ("_headers", "_header_pairs", "_headers_list")
    )


def _named_secret_record(value: Any) -> tuple[str, Any | None] | None:
    if not isinstance(value, dict):
        return None
    normalized_fields = [(_normalized_key(field), field) for field in value]
    name_fields = {"name", "key", "header", "header_name", "key_name"}
    value_fields = {
        "value",
        "values",
        "header_value",
        "header_values",
        "key_value",
        "key_values",
    }
    secret_names = [
        (actual, value[actual])
        for normalized, actual in normalized_fields
        if normalized in name_fields
        and isinstance(value[actual], str)
        and _is_secret_key(value[actual])
    ]
    if not secret_names:
        return None
    actual_values = [
        actual for normalized, actual in normalized_fields if normalized in value_fields
    ]
    label = str(secret_names[0][1])
    if len(secret_names) != 1 or len(actual_values) != 1:
        return label, None
    return label, actual_values[0]


def _safe_named_secret_value(value: Any) -> bool:
    return _safe_secret_reference(value) or (
        isinstance(value, list)
        and bool(value)
        and all(_safe_secret_reference(item) for item in value)
    )


def _assert_no_secret_literals(value: Any, *, path: str, key: str = "") -> None:
    if _looks_like_secret_reference(value):
        if _safe_secret_reference(value):
            return
        raise ConfigExportError(f"versionable config contains an invalid SecretRef: {path}:{key}")
    named_secret = _named_secret_record(value)
    if named_secret and (
        named_secret[1] is None
        or not _safe_named_secret_value(value[named_secret[1]])
    ):
        raise ConfigExportError(
            f"versionable config contains a named secret value: {path}:{named_secret[0]}"
        )
    if isinstance(value, dict):
        for child_key, child in value.items():
            child_name = str(child_key)
            normalized = _normalized_key(child_name)
            if _is_secret_key(child_name) and not normalized.endswith("_env"):
                if _safe_secret_reference(child):
                    continue
                if _looks_like_secret_reference(child):
                    raise ConfigExportError(
                        f"versionable config contains an invalid SecretRef: "
                        f"{path}:{child_name}"
                    )
                raise ConfigExportError(
                    f"versionable config contains a secret-like field without a SecretRef: "
                    f"{path}:{child_name}"
                )
            _assert_no_secret_literals(child, path=path, key=child_name)
    elif isinstance(value, list):
        if _is_header_collection_key(key):
            flat_pair = _header_pair(value)
            candidates = [value] if flat_pair else value
            for child in candidates:
                pair = _header_pair(child)
                if (
                    pair
                    and _is_secret_key(pair[0])
                    and not _safe_secret_reference(pair[1])
                ):
                    raise ConfigExportError(
                        f"versionable config contains a secret-like header field: "
                        f"{path}:{pair[0]}"
                    )
        for child in value:
            _assert_no_secret_literals(child, path=path, key=key)
    elif isinstance(value, str) and _normalized_key(key).endswith("_env"):
        if not _ENV_NAME_RE.fullmatch(value):
            raise ConfigExportError(
                f"versionable config has an invalid environment reference: {path}:{key}"
            )
    elif isinstance(value, str):
        if _is_capability_url(value):
            raise ConfigExportError(
                f"versionable config contains a capability-bearing URL: {path}:{key}"
            )
        if _looks_like_secret_literal(value):
            raise ConfigExportError(
                f"versionable config contains a credential-like value: {path}:{key}"
            )


def _assert_text_config_safe(text: str, *, parser: str, path: str) -> None:
    if parser == "env":
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            normalized = _normalized_key(name.strip())
            value = value.strip()
            if _is_secret_key(normalized) and value and not (
                value.startswith("${") or value.startswith("<")
            ):
                raise ConfigExportError(
                    f"versionable config contains a secret-like field: {path}:{name.strip()}"
                )
            if _looks_like_secret_literal(value) or _is_capability_url(value):
                raise ConfigExportError(
                    f"versionable config contains a credential-like value: "
                    f"{path}:{name.strip()}"
                )
        return
    if parser != "yaml":
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, value = line.split(":", 1)
        normalized = _normalized_key(name.strip())
        value = value.strip().strip("'\"")
        if normalized.endswith("_env"):
            if value and not _ENV_NAME_RE.fullmatch(value):
                raise ConfigExportError(
                    f"versionable config has an invalid environment reference: "
                    f"{path}:{name.strip()}"
                )
        elif _is_secret_key(normalized) and value:
            raise ConfigExportError(
                f"versionable config contains a secret-like field: {path}:{name.strip()}"
            )


def _sanitize_gateway(value: Any, *, key: str = "") -> tuple[Any, int]:
    if _looks_like_secret_reference(value):
        if _safe_secret_reference(value):
            return dict(value), 0
        return "<redacted-invalid-secret-ref>", 1
    if isinstance(value, dict):
        if _normalized_key(key) == "env":
            sanitized_env = {str(name): "<redacted>" for name in sorted(value)}
            redactions = sum(value[name] != "<redacted>" for name in value)
            return sanitized_env, redactions
        named_secret = _named_secret_record(value)
        if named_secret and named_secret[1] is None:
            return "<redacted-invalid-named-secret>", 1
        result = {}
        count = 0
        for child_key, child in value.items():
            normalized = _normalized_key(child_key)
            if named_secret and child_key == named_secret[1]:
                if _safe_secret_reference(child):
                    result[child_key] = dict(child)
                elif _safe_named_secret_value(child):
                    result[child_key] = [dict(item) for item in child]
                else:
                    result[child_key] = "<redacted>"
                    count += 1
                continue
            if _is_secret_key(child_key):
                if _safe_secret_reference(child):
                    result[child_key] = dict(child)
                    continue
                if (
                    normalized.endswith("_env")
                    and isinstance(child, str)
                    and _ENV_NAME_RE.fullmatch(child)
                ):
                    result[child_key] = child
                    continue
                result[child_key] = "<redacted>"
                count += 1
                continue
            sanitized, child_count = _sanitize_gateway(child, key=str(child_key))
            result[child_key] = sanitized
            count += child_count
        return result, count
    if isinstance(value, list):
        flat_pair = _header_pair(value) if _is_header_collection_key(key) else None
        if flat_pair and _is_secret_key(flat_pair[0]):
            if _safe_secret_reference(flat_pair[1]):
                return [flat_pair[0], dict(flat_pair[1])], 0
            return [flat_pair[0], "<redacted>"], 1
        result = []
        count = 0
        for child in value:
            pair = _header_pair(child)
            if (
                _is_header_collection_key(key)
                and pair
                and _is_secret_key(pair[0])
            ):
                if _safe_secret_reference(pair[1]):
                    result.append([pair[0], dict(pair[1])])
                else:
                    result.append([pair[0], "<redacted>"])
                    count += 1
                continue
            sanitized, child_count = _sanitize_gateway(child, key=key)
            result.append(sanitized)
            count += child_count
        return result, count
    if isinstance(value, str):
        if _is_capability_url(value):
            return "<redacted-capability-url>", 1
        if _looks_like_secret_literal(value):
            return "<redacted-credential>", 1
    return value, 0


def _safe_mcp_server_fragment(value: Any) -> tuple[dict | None, int]:
    """Return only the known local stdio launch schema; omit every other shape."""

    if not isinstance(value, dict):
        return None, 1
    command = value.get("command")
    args = value.get("args")
    accepted = {
        "anvil-serving": ["mcp", "serve"],
        "python": ["-m", "anvil_serving.cli", "mcp", "serve"],
        "python3": ["-m", "anvil_serving.cli", "mcp", "serve"],
        "py": ["-m", "anvil_serving.cli", "mcp", "serve"],
    }
    if command not in accepted or args != accepted[command]:
        return None, 1
    if "type" in value and value["type"] != "stdio":
        return None, 1

    result: dict[str, Any] = {"command": command, "args": list(args)}
    if value.get("type") == "stdio":
        result["type"] = "stdio"
    if "enabled" in value:
        if not isinstance(value["enabled"], bool):
            return None, 1
        result["enabled"] = value["enabled"]

    redactions = 0
    env = value.get("env")
    if env is not None:
        if not isinstance(env, dict) or not all(
            isinstance(name, str) and _ENV_NAME_RE.fullmatch(name) for name in env
        ):
            return None, 1
        result["env"] = {name: "<redacted>" for name in sorted(env)}
        redactions += len(env)

    allowed_fields = {"type", "command", "args", "env", "enabled"}
    redactions += len(set(value) - allowed_fields)
    return result, redactions


def _gateway_fragment(path: Path, *, max_bytes: int) -> tuple[dict, dict, int]:
    if path.name.lower() != "openclaw.json":
        raise ConfigExportError("gateway configuration must be named openclaw.json")
    _assert_no_link_components(path, label="gateway configuration")
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
    pre_redactions = 0
    servers = parsed.get("mcpServers")
    if isinstance(servers, dict):
        selected_servers = {}
        for name, value in servers.items():
            if str(name).lower() not in {"anvil", "anvil-serving", "anvil_controller"}:
                continue
            selected, count = _safe_mcp_server_fragment(value)
            pre_redactions += count
            if selected is not None:
                selected_servers[name] = selected
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
    return sanitized, metadata, pre_redactions + redactions


def export(
    home: str | os.PathLike[str] | None = None,
    *,
    gateway_path: str | os.PathLike[str] | None = None,
    paths: list[str] | tuple[str, ...] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    """Return exact safe config files and an allowlisted, redacted gateway fragment."""

    report = inventory(home, max_bytes=max_bytes)
    root = Path(report["effective_home"])
    by_path = {row["path"]: row for row in report["files"]}
    selected_paths: list[str] | None = None
    if paths is None:
        selected = {
            row["path"]
            for row in report["files"]
            if row["classification"] in {"versionable", "unsupported"}
        }
    else:
        if not isinstance(paths, (list, tuple)):
            raise ConfigExportError("paths must be an array of relative file paths")
        selected_paths = []
        for raw in paths:
            if not isinstance(raw, str) or not raw.strip():
                raise ConfigExportError("paths must contain non-empty strings")
            relative = Path(raw.strip())
            if relative.is_absolute() or ".." in relative.parts:
                raise ConfigExportError(f"selected path must stay relative: {raw}")
            normalized = relative.as_posix()
            if normalized not in selected_paths:
                selected_paths.append(normalized)
        selected = set(selected_paths)

    pending = list(selected)
    while pending:
        relative = pending.pop()
        row = by_path.get(relative)
        if row is None:
            raise ConfigExportError(f"selected path does not exist: {relative}")
        if row["classification"] == "unsupported":
            raise ConfigExportError(
                "operator config export does not support YAML without a safe "
                f"stdlib parser: {relative}"
            )
        if row["classification"] != "versionable":
            raise ConfigExportError(
                f"selected path is not safe versionable config: {relative}"
            )
        for dependency in row["dependencies"]:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)

    exported = []
    for row in report["files"]:
        if row["path"] not in selected:
            continue
        path = root / Path(row["path"])
        data = _read_bounded(path, max_bytes=max_bytes)
        parsed = _parse(path, data, row["parser"])
        if row["parser"] in {"toml", "json"}:
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
        resolved_gateway = Path(gateway_path).expanduser().absolute()
        fragment, gateway_metadata, redaction_count = _gateway_fragment(
            resolved_gateway, max_bytes=max_bytes
        )
    return {
        "schema": "operator-config-export/v1",
        "effective_home": report["effective_home"],
        "read_only": True,
        "selected_paths": selected_paths,
        "dependency_complete": True,
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
            for classification in (
                "secret",
                "runtime",
                "backup",
                "cache",
                "unsupported",
                "unknown",
            )
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
            command.add_argument("--path", dest="paths", action="append")
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            result = inventory(args.home, max_bytes=args.max_bytes)
        else:
            result = export(
                args.home,
                gateway_path=args.gateway_path,
                paths=args.paths,
                max_bytes=args.max_bytes,
            )
    except ConfigExportError as exc:
        print(f"[anvil-serving] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
