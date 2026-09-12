"""Closed configuration contract for the managed Anvil Connect renderer.

The renderer intentionally accepts a small deployment description.  It does not
read process environments, provision certificates, or contact an IdP.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import os
import posixpath
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "anvil-connect.deployment/v1"
_MAX_ITEMS = 64
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ID = re.compile(r"[a-z][a-z0-9-]{0,62}$")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,127}$")
_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
_DEVICE_HUMAN = re.compile(r"human:[0-9a-f]{64}$")
_LITERAL_SECRET = re.compile(r"(?:password|secret|credential|private[_-]?key)", re.I)
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_LINUX_ID = 2147483647
_MAX_MEMORY_MAX_BYTES = (1 << 63) - 1
_MAX_TASKS_MAX = 2147483647


class ManifestError(ValueError):
    """The public deployment manifest is invalid or unsafe to render."""


def _error(path: str, message: str) -> ManifestError:
    return ManifestError(f"{path}: {message}")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_load(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ManifestError(f"non-finite JSON constant: {value}")

    try:
        return json.loads(text, object_pairs_hook=_object, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON: {exc.msg}") from exc


def _mapping(value: Any, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, "must be an object")
    actual = set(value)
    missing = keys - actual
    unknown = actual - keys
    if missing:
        raise _error(path, "missing keys: " + ", ".join(sorted(missing)))
    if unknown:
        raise _error(path, "unknown keys: " + ", ".join(sorted(unknown)))
    if any(v is None for v in value.values()):
        raise _error(path, "null is not allowed")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(path, "must be a non-empty string")
    if any(c in value for c in ("\x00", "\r", "\n", "\t")):
        raise _error(path, "contains an unsafe control character")
    return value


def _abs_path(value: Any, path: str) -> str:
    """Validate a Linux target path without inheriting the runner's grammar."""
    text = _string(value, path)
    candidate = PurePosixPath(text)
    if (not text.startswith("/") or text.startswith("//") or str(candidate) != text
            or str(candidate) == "/" or ".." in candidate.parts):
        raise _error(path, "must be a clean absolute path")
    return str(candidate)


def _host(value: Any, path: str) -> str:
    text = _string(value, path)
    try:
        is_ip = ipaddress.ip_address(text) is not None
    except ValueError:
        is_ip = False
    if text != text.lower() or len(text) > 253 or "." not in text or is_ip or any(not _LABEL.fullmatch(label) for label in text.split(".")):
        raise _error(path, "must be a lower-case DNS host")
    return text


def _ident(value: Any, path: str) -> str:
    text = _string(value, path)
    if not _ID.fullmatch(text):
        raise _error(path, "must be a lower-case identifier")
    return text


def _env(value: Any, path: str) -> str:
    text = _string(value, path)
    if not _ENV.fullmatch(text):
        raise _error(path, "must be an environment variable name")
    return text


def _client_id(value: Any, path: str) -> str:
    text = _string(value, path)
    if len(text) > 128 or " " in text:
        raise _error(path, "must be a native OIDC client identifier")
    return text


def _positive(value: Any, path: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise _error(path, f"must be an integer between 1 and {maximum}")
    return value


def _identity(value: Any, path: str) -> dict[str, int]:
    raw = _mapping(value, path, {"uid", "gid"})
    return {
        "uid": _positive(raw["uid"], path + ".uid", _MAX_LINUX_ID),
        "gid": _positive(raw["gid"], path + ".gid", _MAX_LINUX_ID),
    }


def _identity_map(value: Any, path: str) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict) or len(value) > _MAX_ITEMS or any(item is None for item in value.values()):
        raise _error(path, f"must contain at most {_MAX_ITEMS} identity mappings")
    normalized: dict[str, dict[str, int]] = {}
    for name, identity in value.items():
        normalized[_ident(name, path + ".key")] = _identity(identity, path + "." + str(name))
    return {name: normalized[name] for name in sorted(normalized)}


def _service_identities(value: Any, path: str) -> dict[str, Any]:
    raw = _mapping(value, path, {"gateway", "edge", "idp", "connectors", "clients", "ingress"})
    ingress = _mapping(raw["ingress"], path + ".ingress", {"group_id", "directory"})
    normalized: dict[str, Any] = {
        "gateway": _identity(raw["gateway"], path + ".gateway"),
        "edge": _identity(raw["edge"], path + ".edge"),
        "idp": _identity(raw["idp"], path + ".idp"),
        "connectors": _identity_map(raw["connectors"], path + ".connectors"),
        "clients": _identity_map(raw["clients"], path + ".clients"),
        "ingress": {
            "group_id": _positive(ingress["group_id"], path + ".ingress.group_id", _MAX_LINUX_ID),
            "directory": _abs_path(ingress["directory"], path + ".ingress.directory"),
        },
    }
    roles = [normalized["gateway"], normalized["edge"], normalized["idp"], *normalized["connectors"].values(), *normalized["clients"].values()]
    if len({item["uid"] for item in roles}) != len(roles):
        raise _error(path, "role UIDs must be distinct")
    if len({item["gid"] for item in roles}) != len(roles):
        raise _error(path, "role primary GIDs must be distinct")
    if normalized["ingress"]["group_id"] in {item["gid"] for item in roles}:
        raise _error(path + ".ingress.group_id", "must differ from every role primary GID")
    return normalized


def _service_limit(value: Any, path: str) -> dict[str, int]:
    raw = _mapping(value, path, {"memory_max_bytes", "tasks_max"})
    return {
        "memory_max_bytes": _positive(raw["memory_max_bytes"], path + ".memory_max_bytes", _MAX_MEMORY_MAX_BYTES),
        "tasks_max": _positive(raw["tasks_max"], path + ".tasks_max", _MAX_TASKS_MAX),
    }


def _service_limit_map(value: Any, path: str) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict) or len(value) > _MAX_ITEMS or any(item is None for item in value.values()):
        raise _error(path, f"must contain at most {_MAX_ITEMS} service limit mappings")
    normalized: dict[str, dict[str, int]] = {}
    for name, limit in value.items():
        normalized[_ident(name, path + ".key")] = _service_limit(limit, path + "." + str(name))
    return {name: normalized[name] for name in sorted(normalized)}


def _service_limits(value: Any, path: str) -> dict[str, Any]:
    raw = _mapping(value, path, {"gateway", "edge", "idp", "connectors", "clients"})
    return {
        "gateway": _service_limit(raw["gateway"], path + ".gateway"),
        "edge": _service_limit(raw["edge"], path + ".edge"),
        "idp": _service_limit(raw["idp"], path + ".idp"),
        "connectors": _service_limit_map(raw["connectors"], path + ".connectors"),
        "clients": _service_limit_map(raw["clients"], path + ".clients"),
    }


def require_isolated(data: dict[str, Any]) -> None:
    """Reject a legacy declaration at a publishing or activation boundary."""
    if not isinstance(data, dict) or "service_identities" not in data:
        raise ManifestError("$.service_identities: isolated service identities are required")
    if "service_limits" not in data:
        raise ManifestError("$.service_limits: isolated service limits are required")


def role_identity(data: dict[str, Any], role: str, identifier: str | None = None) -> tuple[int, int]:
    """Return one normalized isolated role identity without host account lookup."""
    require_isolated(data)
    identities = data["service_identities"]
    if role in {"gateway", "edge", "idp"} and identifier is None:
        identity = identities[role]
    elif role in {"connector", "client"} and isinstance(identifier, str):
        collection = "connectors" if role == "connector" else "clients"
        identity = identities[collection].get(identifier)
        if identity is None:
            raise ManifestError(f"$.service_identities.{collection}: unknown role identifier")
    else:
        raise ManifestError("$.service_identities: invalid role identity lookup")
    return identity["uid"], identity["gid"]


def role_limits(data: dict[str, Any], role: str, identifier: str | None = None) -> tuple[int, int]:
    """Return one normalized isolated role limit pair without host discovery."""
    require_isolated(data)
    limits = data["service_limits"]
    if role in {"gateway", "edge", "idp"} and identifier is None:
        limit = limits[role]
    elif role in {"connector", "client"} and isinstance(identifier, str):
        collection = "connectors" if role == "connector" else "clients"
        limit = limits[collection].get(identifier)
        if limit is None:
            raise ManifestError(f"$.service_limits.{collection}: unknown role identifier")
    else:
        raise ManifestError("$.service_limits: invalid role limit lookup")
    return limit["memory_max_bytes"], limit["tasks_max"]


def _paths_disjoint(paths: dict[str, str], path: str) -> None:
    entries = sorted(paths.items())
    for index, (left_name, left) in enumerate(entries):
        for right_name, right in entries[index + 1:]:
            if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
                raise _error(path, f"{left_name} and {right_name} must be disjoint paths")


def _loopback(value: Any, path: str) -> str:
    text = _string(value, path)
    match = re.fullmatch(r"127\.0\.0\.1:([1-9][0-9]{0,4})", text)
    if not match or int(match.group(1)) > 65535:
        raise _error(path, "must be a 127.0.0.1 TCP address")
    return text


def _local_tunnel(value: Any, path: str, state: str, *, listener: bool) -> dict[str, str]:
    address = "listen" if listener else "address"
    files = {"trust_file", "certificate_file", "private_key_file"} if listener else {"trust_file"}
    raw = _mapping(value, path, {address, "server_name", "http_host"} | files)
    result = {
        address: _loopback(raw[address], path + "." + address),
        "server_name": _host(raw["server_name"], path + ".server_name"),
        "http_host": _host(raw["http_host"], path + ".http_host"),
        **{key: _abs_path(raw[key], path + "." + key) for key in sorted(files)},
    }
    references = [result[key] for key in files]
    if len(set(references)) != len(references) or any(file == state or file.startswith(state + "/") for file in references):
        raise _error(path, "file references must be distinct and outside runtime state")
    return result


def _validate_local_tunnels(data: dict[str, Any]) -> None:
    """Check declaration references only; PKI and service access need preflight."""
    gateway, connectors = data["gateway"], data["connectors"]
    listener = gateway.get("local_tunnel")
    for connector in connectors:
        endpoint = connector.get("local_tunnel")
        if endpoint is not None and (listener is None or any(
                endpoint[key] != listener["listen" if key == "address" else key]
                for key in ("address", "server_name", "http_host"))):
            raise _error("$.connectors.local_tunnel", "must match gateway local_tunnel")
    if listener is None:
        return
    hosts = {
        gateway["control_host"], gateway["tunnel_host"], data["authelia"]["host"],
        "admin.anvil-connect.internal", "gateway.anvil-connect.internal", "tunnel.anvil-connect.internal", "tunnel-gate.anvil-connect.internal",
        *(r["rule"]["host"] for r in gateway["gateway"]["resources"]),
        *(c["id"] + ".connector.anvil-connect.internal" for c in connectors),
    }
    if listener["server_name"] in hosts or listener["http_host"] in hosts:
        raise _error("$.gateway.local_tunnel", "must not reuse an existing service identity")
    addresses = {gateway["gateway"]["listen"], gateway["tunnel_listen"], data["authelia"]["listen"],
                 *(r["tunnel_address"] for r in gateway["gateway"]["resources"]),
                 *(c["listen"] for c in data["clients"])}
    for connector in connectors:
        for resource in connector["resources"]:
            addresses.update((resource["reverse_address"], resource["envelope"]["listen"],
                              resource["envelope"]["origin_url"].removeprefix("http://").removesuffix("/")))
    edge = data["caddy"].get("listen", ":443")
    if listener["listen"] in addresses or listener["listen"] == edge or (
            edge.startswith(":") and listener["listen"].rsplit(":", 1)[1] == edge[1:]):
        raise _error("$.gateway.local_tunnel", "must not reuse an existing listener")
    existing_files = {c["public_trust_file"] for c in connectors}
    existing_files.update(data["caddy"]["tls"][key] for key in ("certificate_file", "key_file"))
    existing_files.update(value for key, value in data["authelia"].items() if key.endswith("_file"))
    env = data["environment_files"]
    existing_files.update([env["gateway"], *env["connectors"].values(), *env["clients"].values()])
    if "gateway_identity" in env:
        existing_files.add(env["gateway_identity"])
    state_paths = [gateway["state_directory"], data["authelia"]["state_directory"],
                   *(c["state_directory"] for c in connectors)]
    if "state_directory" in data["caddy"]:
        state_paths.append(data["caddy"]["state_directory"])
    if "ingress" in gateway:
        state_paths.append(gateway["ingress"]["directory"])
    declarations = [listener, *(c["local_tunnel"] for c in connectors if "local_tunnel" in c)]
    for declaration in declarations:
        for key, file in declaration.items():
            if not key.endswith("_file"):
                continue
            if file in existing_files:
                raise _error("$.local_tunnel", "must not reuse an existing trust reference")
            if any(file == state or file.startswith(state + "/") for state in state_paths):
                raise _error("$.local_tunnel", "file references must be distinct and outside runtime state")
            if file == data["config_root"] or file.startswith(data["config_root"] + "/"):
                raise _error("$.local_tunnel", "file references must be outside rendered output")
    if any(c["local_tunnel"]["trust_file"] in {listener["certificate_file"], listener["private_key_file"]}
           for c in connectors if "local_tunnel" in c):
        raise _error("$.connectors.local_tunnel", "must not reuse an existing trust reference")


def _url(value: Any, path: str) -> str:
    text = _string(value, path)
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/?", text)
    if not match or int(match.group(1)) > 65535:
        raise _error(path, "must be an http://127.0.0.1:<port> origin URL")
    return text


def _proxy_url(value: Any, path: str) -> str:
    """Validate the native connector's no-path HTTP proxy grammar."""
    text = _string(value, path)
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", text)
    if not match or int(match.group(1)) > 65535:
        raise _error(path, "must be an http://127.0.0.1:<port> proxy URL without a path")
    return text


def _canonical_nonroot_path(value: Any, path: str) -> str:
    """Accept one conservative, non-root path with no normalization ambiguity."""
    text = _string(value, path)
    segments = text.split("/")
    if (not text.startswith("/") or text == "/" or any(char in text for char in "%\\?#")
            or "//" in text or any(ord(char) < 33 or ord(char) > 126 for char in text)
            or any(segment in {".", ".."} for segment in segments)
            or text.endswith("/") or posixpath.normpath(text) != text):
        raise _error(path, "must be a conservative canonical non-root path")
    return text


def _device_label(value: Any, path: str) -> str:
    text = _string(value, path)
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _error(path, "must contain only valid Unicode scalar values") from exc
    if len(encoded) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise _error(path, "must be at most 128 UTF-8 bytes without ASCII controls")
    return text


def _device_methods(value: Any, path: str, allowed: set[str]) -> list[str]:
    methods = _list(value, path, 7)
    if any(not isinstance(method, str) or method not in _METHODS for method in methods):
        raise _error(path, "contains an unsupported HTTP method")
    if len(set(methods)) != len(methods):
        raise _error(path, "contains duplicate methods")
    if not set(methods).issubset(allowed):
        raise _error(path, "must be a subset of the fixed resource method intersection")
    return sorted(methods)


def _issuer(value: Any, path: str) -> str:
    text = _string(value, path)
    match = re.fullmatch(r"https://([^/:?#]+)", text)
    if not match:
        raise _error(path, "must be an https issuer URL without path or port")
    _host(match.group(1), path)
    return text


def _secret_file(value: Any, path: str) -> str:
    return _abs_path(value, path)


def _list(value: Any, path: str, maximum: int = _MAX_ITEMS) -> list[Any]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise _error(path, f"must contain between 1 and {maximum} items")
    if any(item is None for item in value):
        raise _error(path, "null is not allowed")
    return value


def _rule(value: Any, path: str) -> dict[str, Any]:
    raw = _mapping(value, path, {"id", "host", "path_prefix", "methods", "access", "native_auth", "limits"})
    rule_id = _ident(raw["id"], path + ".id")
    host = _host(raw["host"], path + ".host")
    prefix = _string(raw["path_prefix"], path + ".path_prefix")
    segments = prefix.split("/")
    if (not prefix.startswith("/") or any(char in prefix for char in "%\\?#")
            or "//" in prefix or any(ord(char) < 33 or ord(char) > 126 for char in prefix)
            or any(segment in {".", ".."} for segment in segments)
            or (prefix != "/" and prefix.endswith("/")) or posixpath.normpath(prefix) != prefix):
        raise _error(path + ".path_prefix", "must be a conservative canonical path prefix")
    methods = _list(raw["methods"], path + ".methods", 7)
    if any(not isinstance(method, str) or method not in _METHODS for method in methods):
        raise _error(path + ".methods", "contains an unsupported HTTP method")
    if len(set(methods)) != len(methods):
        raise _error(path + ".methods", "contains duplicate methods")
    access = raw["access"]
    native_auth = raw["native_auth"]
    if access not in {"api", "browser"}:
        raise _error(path + ".access", "must be api or browser")
    if access == "api" and native_auth != "delegate-bearer":
        raise _error(path + ".native_auth", "API resources require delegate-bearer")
    if access == "browser" and native_auth not in {"none", "passthrough", "signed-identity"}:
        raise _error(path + ".native_auth", "browser resources require none, passthrough, or signed-identity")
    raw_limits = _mapping(raw["limits"], path + ".limits", {"request_bytes", "concurrent", "buffer_bytes", "idle_seconds", "duration_seconds"})
    limits = {
        "request_bytes": _positive(raw_limits["request_bytes"], path + ".limits.request_bytes", 64 * 1024 * 1024),
        "concurrent": _positive(raw_limits["concurrent"], path + ".limits.concurrent", 256),
        "buffer_bytes": _positive(raw_limits["buffer_bytes"], path + ".limits.buffer_bytes", 256 * 1024),
        "idle_seconds": _positive(raw_limits["idle_seconds"], path + ".limits.idle_seconds", 300),
        "duration_seconds": _positive(raw_limits["duration_seconds"], path + ".limits.duration_seconds", 86400),
    }
    if limits["buffer_bytes"] < 4096:
        raise _error(path + ".limits.buffer_bytes", "must be between 4096 and 262144")
    if limits["duration_seconds"] < limits["idle_seconds"]:
        raise _error(path + ".limits.duration_seconds", "must be at least idle_seconds")
    return {
        "id": rule_id,
        "host": host,
        "path_prefix": prefix,
        "methods": sorted(methods),
        "access": access,
        "native_auth": native_auth,
        "limits": limits,
    }


def _envelope(value: Any, path: str) -> dict[str, Any]:
    raw = _mapping(value, path, {"rule", "listen", "origin_url", "token_env"})
    rule = _rule(raw["rule"], path + ".rule")
    listen = _loopback(raw["listen"], path + ".listen")
    origin_url = _url(raw["origin_url"], path + ".origin_url")
    token_env = raw["token_env"]
    if rule["native_auth"] == "delegate-bearer":
        token_env = _env(token_env, path + ".token_env")
    elif token_env != "":
        raise _error(path + ".token_env", "must be empty when native_auth is not delegate-bearer")
    if origin_url.removeprefix("http://").removesuffix("/") == listen:
        raise _error(path, "origin_url may not point to its listener")
    return {"rule": rule, "listen": listen, "origin_url": origin_url, "token_env": token_env}


def _reject_literal_credentials(value: Any, path: str = "$") -> None:
    """Ensure secret-shaped fields are references, never inline credentials."""
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_literal_credentials(child, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    for key, child in value.items():
        lower = key.lower()
        if _LITERAL_SECRET.search(lower) and not (lower.endswith("_env") or lower.endswith("_file")):
            raise _error(f"{path}.{key}", "literal credentials are forbidden; use an _env or _file reference")
        _reject_literal_credentials(child, f"{path}.{key}")


def validate_manifest(value: Any) -> dict[str, Any]:
    """Validate and normalize a closed deployment manifest without side effects."""
    _reject_literal_credentials(value)
    root_fields = {"schema", "binary", "components", "config_root", "environment_files", "gateway", "connectors", "clients", "caddy", "authelia"}
    if isinstance(value, dict):
        root_fields.update({"service_user", "service_identities", "service_limits"}.intersection(value))
    raw = _mapping(value, "$", root_fields)
    if raw["schema"] != SCHEMA:
        raise _error("$.schema", f"must equal {SCHEMA}")
    binary = _abs_path(raw["binary"], "$.binary")
    components_raw = _mapping(raw["components"], "$.components", {"caddy", "authelia"})
    components = {name: _abs_path(components_raw[name], "$.components." + name) for name in ("caddy", "authelia")}
    config_root = _abs_path(raw["config_root"], "$.config_root")
    environment_fields = {"gateway", "connectors", "clients"}
    if isinstance(raw["environment_files"], dict) and "gateway_identity" in raw["environment_files"]:
        environment_fields.add("gateway_identity")
    environment_raw = _mapping(raw["environment_files"], "$.environment_files", environment_fields)
    environment_gateway = _abs_path(environment_raw["gateway"], "$.environment_files.gateway")
    environment_gateway_identity = (_abs_path(environment_raw["gateway_identity"], "$.environment_files.gateway_identity")
                                    if "gateway_identity" in environment_raw else None)
    has_legacy_user = "service_user" in raw
    has_identities = "service_identities" in raw
    has_limits = "service_limits" in raw
    if has_legacy_user == has_identities:
        raise _error("$", "must contain exactly one of service_user or service_identities")
    if has_identities != has_limits:
        raise _error("$", "service_limits must be present exactly with service_identities")
    service_user = _ident(raw["service_user"], "$.service_user") if has_legacy_user else None
    service_identities = _service_identities(raw["service_identities"], "$.service_identities") if has_identities else None
    service_limits = _service_limits(raw["service_limits"], "$.service_limits") if has_limits else None

    gateway_fields = {"schema", "gateway", "control_host", "tunnel_host", "state_directory", "tunnel_binary", "tunnel_listen", "oidc"}
    if isinstance(raw["gateway"], dict) and "browser_session_lifetime_seconds" in raw["gateway"]:
        gateway_fields.add("browser_session_lifetime_seconds")
    if isinstance(raw["gateway"], dict) and "ingress" in raw["gateway"]:
        gateway_fields.add("ingress")
    if isinstance(raw["gateway"], dict) and "local_tunnel" in raw["gateway"]:
        gateway_fields.add("local_tunnel")
    gateway_raw = _mapping(raw["gateway"], "$.gateway", gateway_fields)
    if gateway_raw["schema"] != "anvil-connect.gateway-runtime/v1":
        raise _error("$.gateway.schema", "must equal anvil-connect.gateway-runtime/v1")
    embedded_fields = {"schema", "listen", "max_concurrent", "resources"}
    if isinstance(gateway_raw["gateway"], dict) and "device_authorizations" in gateway_raw["gateway"]:
        embedded_fields.add("device_authorizations")
    if isinstance(gateway_raw["gateway"], dict) and "browser_administration" in gateway_raw["gateway"]:
        embedded_fields.add("browser_administration")
    embedded = _mapping(gateway_raw["gateway"], "$.gateway.gateway", embedded_fields)
    if embedded["schema"] != "anvil-connect.gateway/v1":
        raise _error("$.gateway.gateway.schema", "must equal anvil-connect.gateway/v1")
    gateway_listen = _loopback(embedded["listen"], "$.gateway.gateway.listen")
    resource_raws = _list(embedded["resources"], "$.gateway.gateway.resources")
    resources: list[dict[str, Any]] = []
    identity_envs: set[str] = set()
    identity_ids: set[str] = set()
    for index, resource_raw in enumerate(resource_raws):
        item_path = f"$.gateway.gateway.resources[{index}]"
        identity_fields = {"identity_key_env", "identity_key_id"}
        optional_fields = identity_fields.intersection(resource_raw) if isinstance(resource_raw, dict) else set()
        item = _mapping(resource_raw, item_path, {"connector", "tunnel_address", "rule"} | optional_fields)
        resource = {
            "connector": _ident(item["connector"], item_path + ".connector"),
            "tunnel_address": _loopback(item["tunnel_address"], item_path + ".tunnel_address"),
            "rule": _rule(item["rule"], item_path + ".rule"),
        }
        if resource["rule"]["native_auth"] == "signed-identity":
            if optional_fields != identity_fields:
                raise _error(item_path, "signed-identity requires identity_key_env and identity_key_id")
            key_env = _env(item["identity_key_env"], item_path + ".identity_key_env")
            if key_env in identity_envs:
                raise _error(item_path + ".identity_key_env", "must be distinct for each signed-identity resource")
            identity_envs.add(key_env)
            key_id = _ident(item["identity_key_id"], item_path + ".identity_key_id")
            if key_id in identity_ids:
                raise _error(item_path + ".identity_key_id", "must be distinct for each signed-identity resource")
            identity_ids.add(key_id)
            resource.update(identity_key_env=key_env, identity_key_id=key_id)
        elif optional_fields:
            raise _error(item_path, "identity key references require signed-identity mode")
        resources.append(resource)
    if len({r["rule"]["id"] for r in resources}) != len(resources):
        raise _error("$.gateway.gateway.resources", "contains duplicate rule ids")
    if len({r["rule"]["host"] for r in resources}) != len(resources):
        raise _error("$.gateway.gateway.resources", "contains duplicate resource hosts")
    if len({r["tunnel_address"] for r in resources}) != len(resources) or any(r["tunnel_address"] == gateway_listen for r in resources):
        raise _error("$.gateway.gateway.resources", "contains duplicate or listener-equal tunnel addresses")
    if not any(resource["rule"]["access"] == "browser" for resource in resources):
        raise _error("$.gateway.gateway.resources", "managed deployment requires at least one browser resource")
    oidc = _mapping(gateway_raw["oidc"], "$.gateway.oidc", {"issuer", "client_id", "client_secret_env"})
    gateway_resource_index = {resource["rule"]["id"]: resource for resource in resources}
    device_authorizations: list[dict[str, Any]] = []
    if "device_authorizations" in embedded:
        raw_authorizations = _list(embedded["device_authorizations"], "$.gateway.gateway.device_authorizations")
        browser_resources: set[str] = set()
        api_resources: set[str] = set()
        for index, raw_authorization in enumerate(raw_authorizations):
            path = f"$.gateway.gateway.device_authorizations[{index}]"
            authorization = _mapping(raw_authorization, path, {"browser_resource", "api_resource", "methods", "label", "principals"})
            browser_id = _ident(authorization["browser_resource"], path + ".browser_resource")
            api_id = _ident(authorization["api_resource"], path + ".api_resource")
            browser = gateway_resource_index.get(browser_id)
            api = gateway_resource_index.get(api_id)
            if browser is None or browser["rule"]["access"] != "browser":
                raise _error(path + ".browser_resource", "must name a declared browser resource")
            if api is None or api["rule"]["access"] != "api":
                raise _error(path + ".api_resource", "must name a declared API resource")
            if browser_id == api_id or browser_id in browser_resources or api_id in api_resources:
                raise _error(path, "browser_resource and api_resource must each be unique")
            if not {"GET", "POST"}.issubset(browser["rule"]["methods"]):
                raise _error(path + ".browser_resource", "device approval browser resource must allow GET and POST")
            browser_resources.add(browser_id)
            api_resources.add(api_id)
            methods = _device_methods(authorization["methods"], path + ".methods", set(browser["rule"]["methods"]).intersection(api["rule"]["methods"]))
            principals = authorization["principals"]
            if not isinstance(principals, dict) or not 1 <= len(principals) <= _MAX_ITEMS or any(item is None for item in principals.values()):
                raise _error(path + ".principals", "must contain between 1 and 64 opaque human mappings")
            normalized_principals: dict[str, str] = {}
            for human, principal in principals.items():
                if not isinstance(human, str) or not _DEVICE_HUMAN.fullmatch(human):
                    raise _error(path + ".principals", "keys must be canonical opaque human identifiers")
                normalized_principals[human] = _ident(principal, path + ".principals." + human)
            device_authorizations.append({
                "browser_resource": browser_id,
                "api_resource": api_id,
                "methods": methods,
                "label": _device_label(authorization["label"], path + ".label"),
                "principals": {human: normalized_principals[human] for human in sorted(normalized_principals)},
            })
        device_authorizations.sort(key=lambda item: item["browser_resource"])
    browser_administration = None
    if "browser_administration" in embedded:
        raw_admin = _mapping(embedded["browser_administration"], "$.gateway.gateway.browser_administration", {"browser_resource", "operators"})
        browser_id = _ident(raw_admin["browser_resource"], "$.gateway.gateway.browser_administration.browser_resource")
        browser = gateway_resource_index.get(browser_id)
        operators = _list(raw_admin["operators"], "$.gateway.gateway.browser_administration.operators")
        if browser is None or browser["rule"]["access"] != "browser" or not {"GET", "POST"}.issubset(browser["rule"]["methods"]) or not 1 <= len(operators) <= _MAX_ITEMS or len(set(operators)) != len(operators) or any(not isinstance(item, str) or not _DEVICE_HUMAN.fullmatch(item) for item in operators):
            raise _error("$.gateway.gateway.browser_administration", "must name a GET/POST browser resource and unique opaque operators")
        browser_administration = {"browser_resource": browser_id, "operators": sorted(operators)}
    gateway_embedded = {"schema": embedded["schema"], "listen": gateway_listen, "max_concurrent": _positive(embedded["max_concurrent"], "$.gateway.gateway.max_concurrent", 512), "resources": sorted(resources, key=lambda r: r["rule"]["id"])}
    if "device_authorizations" in embedded:
        gateway_embedded["device_authorizations"] = device_authorizations
    if browser_administration is not None:
        gateway_embedded["browser_administration"] = browser_administration
    gateway = {
        "schema": gateway_raw["schema"],
        "gateway": gateway_embedded,
        "control_host": _host(gateway_raw["control_host"], "$.gateway.control_host"),
        "tunnel_host": _host(gateway_raw["tunnel_host"], "$.gateway.tunnel_host"),
        "state_directory": _abs_path(gateway_raw["state_directory"], "$.gateway.state_directory"),
        "tunnel_binary": _abs_path(gateway_raw["tunnel_binary"], "$.gateway.tunnel_binary"),
        "tunnel_listen": _loopback(gateway_raw["tunnel_listen"], "$.gateway.tunnel_listen"),
        "oidc": {"issuer": _issuer(oidc["issuer"], "$.gateway.oidc.issuer"), "client_id": _client_id(oidc["client_id"], "$.gateway.oidc.client_id"), "client_secret_env": _env(oidc["client_secret_env"], "$.gateway.oidc.client_secret_env")},
    }
    if "local_tunnel" in gateway_raw:
        gateway["local_tunnel"] = _local_tunnel(gateway_raw["local_tunnel"], "$.gateway.local_tunnel", gateway["state_directory"], listener=True)
    if service_identities is not None:
        derived_ingress = {
            "directory": service_identities["ingress"]["directory"],
            "gateway_uid": service_identities["gateway"]["uid"],
            "edge_uid": service_identities["edge"]["uid"],
            "group_id": service_identities["ingress"]["group_id"],
        }
        if "ingress" in gateway_raw:
            supplied = _mapping(gateway_raw["ingress"], "$.gateway.ingress", {"directory", "gateway_uid", "edge_uid", "group_id"})
            normalized_supplied = {
                "directory": _abs_path(supplied["directory"], "$.gateway.ingress.directory"),
                "gateway_uid": _positive(supplied["gateway_uid"], "$.gateway.ingress.gateway_uid", _MAX_LINUX_ID),
                "edge_uid": _positive(supplied["edge_uid"], "$.gateway.ingress.edge_uid", _MAX_LINUX_ID),
                "group_id": _positive(supplied["group_id"], "$.gateway.ingress.group_id", _MAX_LINUX_ID),
            }
            if normalized_supplied != derived_ingress:
                raise _error("$.gateway.ingress", "must equal the derived isolated ingress policy")
        gateway["ingress"] = derived_ingress
    elif "ingress" in gateway_raw:
        raise _error("$.gateway.ingress", "requires service_identities")
    if "browser_session_lifetime_seconds" in gateway_raw:
        lifetime = gateway_raw["browser_session_lifetime_seconds"]
        if isinstance(lifetime, bool) or not isinstance(lifetime, int) or not 60 <= lifetime <= 86400:
            raise _error("$.gateway.browser_session_lifetime_seconds", "must be an integer between 60 and 86400")
        gateway["browser_session_lifetime_seconds"] = lifetime
    if any(item["rule"]["limits"]["concurrent"] > gateway["gateway"]["max_concurrent"] for item in resources):
        raise _error("$.gateway.gateway.resources", "resource concurrent limit exceeds gateway max_concurrent")
    if gateway["control_host"] == gateway["tunnel_host"]:
        raise _error("$.gateway", "control_host and tunnel_host must differ")
    if gateway["tunnel_listen"] == gateway_listen:
        raise _error("$.gateway", "tunnel_listen must differ from gateway listener")
    for resource in resources:
        if resource["rule"]["host"] in {gateway["control_host"], gateway["tunnel_host"]}:
            raise _error("$.gateway.gateway.resources", "resource host may not equal control_host or tunnel_host")
        if resource["tunnel_address"] == gateway["tunnel_listen"]:
            raise _error("$.gateway.gateway.resources", "resource tunnel_address may not equal tunnel_listen")

    connector_raws = _list(raw["connectors"], "$.connectors")
    connectors: list[dict[str, Any]] = []
    for index, connector_raw in enumerate(connector_raws):
        path = f"$.connectors[{index}]"
        connector_fields = {"schema", "id", "control_host", "tunnel_host", "state_directory", "tunnel_binary", "public_trust_file", "http_proxy_url", "resources"}
        if isinstance(connector_raw, dict) and "local_tunnel" in connector_raw:
            connector_fields.add("local_tunnel")
        item = _mapping(connector_raw, path, connector_fields)
        if item["schema"] != "anvil-connect.connector-runtime/v1":
            raise _error(path + ".schema", "must equal anvil-connect.connector-runtime/v1")
        connector_resources = []
        for resource_number, resource_raw in enumerate(_list(item["resources"], path + ".resources")):
            rp = f"{path}.resources[{resource_number}]"
            resource = _mapping(resource_raw, rp, {"envelope", "reverse_address"})
            connector_resources.append({"envelope": _envelope(resource["envelope"], rp + ".envelope"), "reverse_address": _loopback(resource["reverse_address"], rp + ".reverse_address")})
        connector = {
            "schema": item["schema"], "id": _ident(item["id"], path + ".id"),
            "control_host": _host(item["control_host"], path + ".control_host"), "tunnel_host": _host(item["tunnel_host"], path + ".tunnel_host"),
            "state_directory": _abs_path(item["state_directory"], path + ".state_directory"), "tunnel_binary": _abs_path(item["tunnel_binary"], path + ".tunnel_binary"),
            "public_trust_file": _abs_path(item["public_trust_file"], path + ".public_trust_file"),
            "http_proxy_url": "" if item["http_proxy_url"] == "" else _proxy_url(item["http_proxy_url"], path + ".http_proxy_url"),
            "resources": sorted(connector_resources, key=lambda r: r["envelope"]["rule"]["id"]),
        }
        if "local_tunnel" in item:
            connector["local_tunnel"] = _local_tunnel(item["local_tunnel"], path + ".local_tunnel", connector["state_directory"], listener=False)
        if connector["control_host"] != gateway["control_host"] or connector["tunnel_host"] != gateway["tunnel_host"]:
            raise _error(path, "control_host and tunnel_host must match the gateway")
        if len({r["envelope"]["rule"]["id"] for r in connector_resources}) != len(connector_resources):
            raise _error(path + ".resources", "contains duplicate rule ids")
        if len({r["envelope"]["rule"]["host"] for r in connector_resources}) != len(connector_resources):
            raise _error(path + ".resources", "contains duplicate resource hosts")
        connector_listens = {r["envelope"]["listen"] for r in connector_resources}
        if len(connector_listens) != len(connector_resources):
            raise _error(path + ".resources", "contains duplicate connector listeners")
        if any(r["envelope"]["origin_url"].removeprefix("http://").removesuffix("/") in connector_listens for r in connector_resources):
            raise _error(path + ".resources", "origin_url may not target a connector listener")
        connectors.append(connector)
    if len({c["id"] for c in connectors}) != len(connectors):
        raise _error("$.connectors", "contains duplicate connector ids")
    connectors.sort(key=lambda c: c["id"])

    connector_index = {connector["id"]: connector for connector in connectors}
    bound_rules: set[str] = set()
    all_listens = {gateway_listen, gateway["tunnel_listen"]}
    for resource in gateway["gateway"]["resources"]:
        connector = connector_index.get(resource["connector"])
        if connector is None:
            raise _error("$.gateway.gateway.resources", f"connector {resource['connector']!r} is not declared")
        matched = [item for item in connector["resources"] if item["envelope"]["rule"] == resource["rule"] and item["reverse_address"] == resource["tunnel_address"]]
        if len(matched) != 1:
            raise _error("$.gateway.gateway.resources", "every gateway resource must have exactly one fixed connector binding")
        bound_rules.add(resource["rule"]["id"])
    if any(item["envelope"]["rule"]["id"] not in bound_rules for connector in connectors for item in connector["resources"]):
        raise _error("$.connectors", "contains an unbound connector resource")
    for connector in connectors:
        for resource in connector["resources"]:
            if resource["envelope"]["listen"] in all_listens:
                raise _error("$.connectors", "an origin listener may not equal a gateway listener")
            all_listens.add(resource["envelope"]["listen"])

    client_raws = raw["clients"]
    if not isinstance(client_raws, list) or len(client_raws) > _MAX_ITEMS or any(v is None for v in client_raws):
        raise _error("$.clients", f"must contain at most {_MAX_ITEMS} non-null items")
    clients: list[dict[str, Any]] = []
    for index, client_raw in enumerate(client_raws):
        path = f"$.clients[{index}]"
        client_fields = {"schema", "rule", "listen", "local_key_env", "remote_key_env"}
        if isinstance(client_raw, dict) and "device_authorization" in client_raw:
            client_fields.add("device_authorization")
        item = _mapping(client_raw, path, client_fields)
        if item["schema"] != "anvil-connect.client-runtime/v1":
            raise _error(path + ".schema", "must equal anvil-connect.client-runtime/v1")
        rule = _rule(item["rule"], path + ".rule")
        if rule["access"] != "api":
            raise _error(path + ".rule.access", "client rules must be API resources")
        listen = _loopback(item["listen"], path + ".listen")
        if listen in all_listens:
            raise _error(path + ".listen", "must not reuse another listener")
        all_listens.add(listen)
        local_key_env, remote_key_env = _env(item["local_key_env"], path + ".local_key_env"), _env(item["remote_key_env"], path + ".remote_key_env")
        if local_key_env == remote_key_env:
            raise _error(path, "local_key_env and remote_key_env must differ")
        client = {"schema": item["schema"], "rule": rule, "listen": listen, "local_key_env": local_key_env, "remote_key_env": remote_key_env}
        if "device_authorization" in item:
            raw_device = _mapping(item["device_authorization"], path + ".device_authorization", {"browser_host", "approval_path", "api_resource", "methods"})
            api_resource = _ident(raw_device["api_resource"], path + ".device_authorization.api_resource")
            if api_resource != rule["id"]:
                raise _error(path + ".device_authorization.api_resource", "must equal the client API resource")
            client["device_authorization"] = {
                "browser_host": _host(raw_device["browser_host"], path + ".device_authorization.browser_host"),
                "approval_path": _canonical_nonroot_path(raw_device["approval_path"], path + ".device_authorization.approval_path"),
                "api_resource": api_resource,
                "methods": _device_methods(raw_device["methods"], path + ".device_authorization.methods", set(rule["methods"])),
            }
        clients.append(client)
    if len({client["rule"]["id"] for client in clients}) != len(clients):
        raise _error("$.clients", "contains duplicate client rule ids")
    gateway_api_rules = {resource["rule"]["id"]: resource["rule"] for resource in gateway["gateway"]["resources"] if resource["rule"]["access"] == "api"}
    if any(gateway_api_rules.get(client["rule"]["id"]) != client["rule"] for client in clients):
        raise _error("$.clients", "every client rule must exactly match a gateway API rule")
    device_by_api = {item["api_resource"]: item for item in device_authorizations}
    for client in clients:
        device = client.get("device_authorization")
        if device is None:
            continue
        expected = device_by_api.get(client["rule"]["id"])
        if expected is None:
            raise _error("$.clients", "device authorization must copy a declared gateway authorization")
        browser = gateway_resource_index[expected["browser_resource"]]["rule"]
        approval_path = posixpath.join(browser["path_prefix"], "_anvil-connect/device")
        if (device["browser_host"] != browser["host"] or device["approval_path"] != approval_path
                or device["api_resource"] != expected["api_resource"] or device["methods"] != expected["methods"]):
            raise _error("$.clients", "device authorization must exactly copy the fixed gateway authorization")
    clients.sort(key=lambda c: c["rule"]["id"])
    connector_env_raw = _mapping(environment_raw["connectors"], "$.environment_files.connectors", set(connector_index))
    client_env_raw = _mapping(environment_raw["clients"], "$.environment_files.clients", {client["rule"]["id"] for client in clients})
    signed_identity = any(resource["rule"]["native_auth"] == "signed-identity" for resource in resources)
    if signed_identity and environment_gateway_identity is None:
        raise _error("$.environment_files.gateway_identity", "is required for signed-identity resources")
    if not signed_identity and environment_gateway_identity is not None:
        raise _error("$.environment_files.gateway_identity", "is only allowed for signed-identity resources")
    environment_files = {
        "gateway": environment_gateway,
        "connectors": {name: _abs_path(connector_env_raw[name], "$.environment_files.connectors." + name) for name in sorted(connector_env_raw)},
        "clients": {name: _abs_path(client_env_raw[name], "$.environment_files.clients." + name) for name in sorted(client_env_raw)},
    }
    if environment_gateway_identity is not None:
        environment_files["gateway_identity"] = environment_gateway_identity
    environment_paths = [
        environment_files["gateway"],
        *environment_files["connectors"].values(),
        *environment_files["clients"].values(),
    ]
    if "gateway_identity" in environment_files:
        environment_paths.append(environment_files["gateway_identity"])
    if len(environment_paths) != len(set(environment_paths)):
        raise _error("$.environment_files", "must not reuse canonical EnvironmentFile paths")

    caddy_fields = {"service_name", "tls"}
    if isinstance(raw["caddy"], dict) and "listen" in raw["caddy"]:
        caddy_fields.add("listen")
    if isinstance(raw["caddy"], dict) and "state_directory" in raw["caddy"]:
        caddy_fields.add("state_directory")
    caddy_raw = _mapping(raw["caddy"], "$.caddy", caddy_fields)
    caddy_listen = caddy_raw.get("listen", ":443")
    if caddy_listen != ":443":
        caddy_listen = _loopback(caddy_listen, "$.caddy.listen")
        if caddy_listen in all_listens:
            raise _error("$.caddy.listen", "must not reuse a native listener")
    tls_raw = _mapping(caddy_raw["tls"], "$.caddy.tls", {"mode", "certificate_file", "key_file"})
    tls_mode = tls_raw["mode"]
    if tls_mode not in {"acme", "provided"}:
        raise _error("$.caddy.tls.mode", "must be acme or provided")
    certificate_file = tls_raw["certificate_file"]
    key_file = tls_raw["key_file"]
    if tls_mode == "provided":
        certificate_file, key_file = _secret_file(certificate_file, "$.caddy.tls.certificate_file"), _secret_file(key_file, "$.caddy.tls.key_file")
    elif certificate_file != "" or key_file != "":
        raise _error("$.caddy.tls", "ACME mode may not name certificate files")
    if caddy_listen != ":443" and tls_mode != "provided":
        raise _error("$.caddy.tls", "a loopback edge requires provided TLS certificates")
    caddy_name = _ident(caddy_raw["service_name"], "$.caddy.service_name")
    if caddy_name != "anvil-connect-caddy":
        raise _error("$.caddy.service_name", "must equal anvil-connect-caddy")
    caddy = {"service_name": caddy_name, "tls": {"mode": tls_mode, "certificate_file": certificate_file, "key_file": key_file}}
    # Preserve existing canonical generations when the optional field is absent.
    if "listen" in caddy_raw:
        caddy["listen"] = caddy_listen
    if service_identities is not None:
        if "state_directory" not in caddy_raw:
            raise _error("$.caddy.state_directory", "is required with service_identities")
        caddy["state_directory"] = _abs_path(caddy_raw["state_directory"], "$.caddy.state_directory")
    elif "state_directory" in caddy_raw:
        caddy["state_directory"] = _abs_path(caddy_raw["state_directory"], "$.caddy.state_directory")

    authelia_fields = {"service_name", "host", "listen", "state_directory", "users_file", "client_secret_file", "session_secret_file", "storage_encryption_key_file", "identity_validation_secret_file", "oidc_hmac_secret_file", "oidc_rsa_private_key_file"}
    if isinstance(raw["authelia"], dict) and "webauthn" in raw["authelia"]:
        authelia_fields.add("webauthn")
    authelia_raw = _mapping(raw["authelia"], "$.authelia", authelia_fields)
    authelia = {key: _secret_file(authelia_raw[key], "$.authelia." + key) for key in ("users_file", "client_secret_file", "session_secret_file", "storage_encryption_key_file", "identity_validation_secret_file", "oidc_hmac_secret_file", "oidc_rsa_private_key_file")}
    authelia_name = _ident(authelia_raw["service_name"], "$.authelia.service_name")
    if authelia_name != "anvil-connect-authelia":
        raise _error("$.authelia.service_name", "must equal anvil-connect-authelia")
    authelia.update({"service_name": authelia_name, "host": _host(authelia_raw["host"], "$.authelia.host"), "listen": _loopback(authelia_raw["listen"], "$.authelia.listen"), "state_directory": _abs_path(authelia_raw["state_directory"], "$.authelia.state_directory")})
    if "webauthn" in authelia_raw:
        webauthn = _mapping(authelia_raw["webauthn"], "$.authelia.webauthn", {
            "enable_passkey_login", "experimental_enable_passkey_uv_two_factors", "discoverability", "user_verification",
        })
        for flag in ("enable_passkey_login", "experimental_enable_passkey_uv_two_factors"):
            if type(webauthn[flag]) is not bool:
                raise _error("$.authelia.webauthn." + flag, "must be a boolean")
        # This opt-in profile always requires device user verification and a
        # discoverable credential. Synced passkey providers remain compatible.
        for criterion in ("discoverability", "user_verification"):
            if webauthn[criterion] != "required":
                raise _error("$.authelia.webauthn." + criterion, "must equal required")
        if webauthn["experimental_enable_passkey_uv_two_factors"] and not webauthn["enable_passkey_login"]:
            raise _error("$.authelia.webauthn", "passkey two-factor acceptance requires passkey login")
        authelia["webauthn"] = dict(webauthn)
    all_hosts = {gateway["control_host"], gateway["tunnel_host"], *(r["rule"]["host"] for r in gateway["gateway"]["resources"])}
    if authelia["host"] in all_hosts:
        raise _error("$.authelia.host", "must be distinct from public resource, control, and tunnel hosts")
    if authelia["listen"] in all_listens or authelia["listen"] == caddy_listen:
        raise _error("$.authelia.listen", "must not reuse a native listener")
    issuer_host = gateway["oidc"]["issuer"].removeprefix("https://")
    if issuer_host != authelia["host"]:
        raise _error("$.gateway.oidc.issuer", "must equal the managed Authelia HTTPS host")

    result = {"schema": SCHEMA, "binary": binary, "components": components, "config_root": config_root, "environment_files": environment_files, "gateway": gateway, "connectors": connectors, "clients": clients, "caddy": caddy, "authelia": authelia}
    _validate_local_tunnels(result)
    if service_identities is None:
        result["service_user"] = service_user
        return result

    if set(service_identities["connectors"]) != set(connector_index):
        raise _error("$.service_identities.connectors", "must exactly match declared connector ids")
    if set(service_identities["clients"]) != {client["rule"]["id"] for client in clients}:
        raise _error("$.service_identities.clients", "must exactly match declared client rule ids")
    if set(service_limits["connectors"]) != set(connector_index):
        raise _error("$.service_limits.connectors", "must exactly match declared connector ids")
    if set(service_limits["clients"]) != {client["rule"]["id"] for client in clients}:
        raise _error("$.service_limits.clients", "must exactly match declared client rule ids")
    state_paths = {
        "gateway": gateway["state_directory"],
        "edge": caddy["state_directory"],
        "idp": authelia["state_directory"],
        **{"connector." + connector["id"]: connector["state_directory"] for connector in connectors},
        "ingress": service_identities["ingress"]["directory"],
    }
    _paths_disjoint(state_paths, "$.service_identities")
    result["service_identities"] = service_identities
    result["service_limits"] = service_limits
    return result


def _secure_read_flags() -> int:
    """Return the mandatory no-follow flags for a native manifest file read."""
    try:
        return os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW
    except AttributeError as exc:
        raise ManifestError("secure manifest file reads require platform no-follow descriptors") from exc


def _read_regular(path: Path, maximum: int) -> bytes:
    """Read a bounded, pinned regular file without following its final symlink."""
    flags = _secure_read_flags()
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManifestError(f"{path}: cannot open manifest") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ManifestError(f"{path}: must be a bounded regular file")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > maximum:
            raise ManifestError(f"{path}: exceeds bounded manifest read")
        return data
    finally:
        os.close(descriptor)


def parse_manifest_text(text: str) -> dict[str, Any]:
    """Parse one strict JSON declaration without touching the host filesystem."""
    if not isinstance(text, str):
        raise ManifestError("manifest text is not UTF-8")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ManifestError("manifest text is not UTF-8") from exc
    if len(encoded) > _MAX_MANIFEST_BYTES:
        raise ManifestError("manifest text exceeds the bounded manifest read")
    return validate_manifest(_json_load(text))


def read_manifest(path: str | Path) -> dict[str, Any]:
    """Securely read one native manifest file, then parse its closed JSON shape."""
    source = Path(path)
    try:
        text = _read_regular(source, _MAX_MANIFEST_BYTES).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{source}: manifest is not UTF-8") from exc
    return parse_manifest_text(text)


def canonical_manifest(manifest: dict[str, Any]) -> bytes:
    """Stable bytes used for render generation identity."""
    return (json.dumps(validate_manifest(copy.deepcopy(manifest)), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
