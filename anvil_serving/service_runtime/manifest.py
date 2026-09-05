"""Strict private service bindings and atomic, compare-and-swap persistence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from urllib.parse import urlsplit

from .contracts import ENGINES, MAX_BYTES, ServiceError, identifier

FIELDS = frozenset({"id", "resource", "manager", "engine", "support", "dependencies",
    "endpoint", "model", "definition", "source_definition", "definition_sha256", "label", "owner_uid",
    "container", "image_id", "identity_labels", "startup_policy", "api_key_env",
    "health_path", "models_path", "serve", "serve_manifest", "feature", "memory_mib"})


def _read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ServiceError("bad_config", "service manifest must be a regular, non-symlink file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ServiceError("bad_config", "service manifest exceeds size limit")
    return raw


def digest(path: str | Path) -> str:
    path = Path(path)
    return hashlib.sha256(_read(path)).hexdigest() if path.exists() else ""


def validate(data: dict, parent: Path) -> dict[str, dict]:
    if set(data) - {"schema", "service"} or data.get("schema") != "anvil-services/v1":
        raise ServiceError("bad_config", "expected anvil-services/v1 schema")
    rows = data.get("service", [])
    if not isinstance(rows, list) or len(rows) > 128:
        raise ServiceError("bad_config", "service must contain at most 128 declarations")
    result, identities = {}, set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - FIELDS:
            raise ServiceError("bad_config", "unknown service field; secrets and commands are not bindings")
        item = dict(row)
        for key in ("id", "resource", "manager", "engine"):
            identifier(item.get(key), key)
        name = item["id"]
        if name in result:
            raise ServiceError("bad_config", "duplicate service id")
        if item["manager"] not in {"launchd", "docker"}:
            raise ServiceError("bad_config", "manager must be launchd or docker")
        if item["engine"] not in ENGINES:
            raise ServiceError("bad_config", "unknown engine adapter")
        if item.get("support", "supported") not in {"supported", "legacy"}:
            raise ServiceError("bad_config", "support must be supported or legacy")
        item.setdefault("support", "supported")
        deps = item.setdefault("dependencies", [])
        if not isinstance(deps, list) or len(deps) > 32 or len(set(map(str, deps))) != len(deps):
            raise ServiceError("bad_config", "dependencies must be distinct service ids")
        for dep in deps:
            identifier(dep, "dependency")
        if item["manager"] == "launchd":
            identifier(item.get("label"), "label")
            uid = item.get("owner_uid")
            if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
                raise ServiceError("bad_config", "owner_uid must be a nonnegative integer")
            if not isinstance(item.get("definition"), str) or not item["definition"]:
                raise ServiceError("bad_config", "launchd requires a definition")
            if not re.fullmatch(r"[a-f0-9]{64}", str(item.get("definition_sha256", ""))):
                raise ServiceError("bad_config", "definition_sha256 must pin the definition")
            identity = ("launchd", uid, item["label"])
        else:
            identifier(item.get("container"), "container")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(item.get("image_id", ""))):
                raise ServiceError("bad_config", "image_id must pin a Docker image")
            labels = item.get("identity_labels")
            if not isinstance(labels, dict) or not labels or len(labels) > 32 or any(
                not isinstance(k, str) or not isinstance(v, str) or len(k) > 128 or len(v) > 256
                for k, v in labels.items()
            ):
                raise ServiceError("bad_config", "Docker identity_labels must be pinned")
            identity = ("docker", item["container"])
        if identity in identities:
            raise ServiceError("bad_config", "duplicate supervisor identity")
        identities.add(identity)
        for key in ("definition", "source_definition", "serve_manifest"):
            if key in item:
                if not isinstance(item[key], str) or not item[key] or len(item[key]) > 4096:
                    raise ServiceError("bad_config", f"invalid {key}")
                value = Path(os.path.expanduser(item[key]))
                item[key] = str(value if value.is_absolute() else parent / value)
        for key in ("model", "endpoint", "health_path", "models_path", "api_key_env", "feature", "serve"):
            if key in item and (not isinstance(item[key], str) or not item[key] or len(item[key]) > 2048
                                or any(ord(c) < 32 for c in item[key])):
                raise ServiceError("bad_config", f"invalid {key}")
        if "api_key_env" in item and not re.fullmatch(r"[A-Z][A-Z0-9_]*", item["api_key_env"]):
            raise ServiceError("bad_config", "api_key_env must be an environment variable name")
        if "endpoint" in item:
            url = urlsplit(item["endpoint"])
            if url.scheme not in {"http", "https"} or url.hostname != "127.0.0.1" or url.username or url.password or url.query or url.fragment:
                raise ServiceError("bad_config", "service endpoint must be host-relative loopback without credentials")
            try:
                url.port
            except ValueError as exc:
                raise ServiceError("bad_config", "invalid endpoint port") from exc
        for key in ("health_path", "models_path"):
            if key in item and (not item[key].startswith("/") or "?" in item[key] or "#" in item[key]):
                raise ServiceError("bad_config", f"invalid {key}")
        if item.get("startup_policy", "unless-stopped") not in {"always", "unless-stopped"}:
            raise ServiceError("bad_config", "unsupported startup_policy")
        if item["manager"] == "docker":
            item.setdefault("startup_policy", "unless-stopped")
        if "memory_mib" in item and (isinstance(item["memory_mib"], bool) or not isinstance(item["memory_mib"], int) or item["memory_mib"] <= 0):
            raise ServiceError("bad_config", "memory_mib must be a positive integer")
        result[name] = item
    visited, active = set(), set()
    def visit(name):
        if name in active:
            raise ServiceError("bad_config", "service dependency cycle")
        if name not in result:
            raise ServiceError("bad_config", "dependency names an undeclared service")
        if name in visited:
            return
        active.add(name)
        for dep in result[name]["dependencies"]:
            visit(dep)
        active.remove(name)
        visited.add(name)
    for name in result:
        visit(name)
    return result


def load_manifest(path: str | Path) -> dict[str, dict]:
    path = Path(path).absolute()
    try:
        data = tomllib.loads(_read(path).decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ServiceError("bad_config", "invalid service manifest TOML") from exc
    return validate(data, path.parent)


def _toml(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_toml(v)}" for k, v in sorted(value.items())) + " }"
    return json.dumps(value, ensure_ascii=False)


def save_manifest(path: str | Path, bindings: dict, *, expected_digest: str) -> None:
    """Caller holds the operator-home lock; refuse changes since its preview."""
    path = Path(path).absolute()
    validate({"schema": "anvil-services/v1", "service": list(bindings.values())}, path.parent)
    if digest(path) != expected_digest:
        raise ServiceError("state_changed", "service manifest changed since inspection")
    lines = ['schema = "anvil-services/v1"']
    for name in sorted(bindings):
        lines.append("\n[[service]]")
        lines.extend(f"{key} = {_toml(value)}" for key, value in sorted(bindings[name].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".services-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if digest(path) != expected_digest:
            raise ServiceError("state_changed", "service manifest changed before commit")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
