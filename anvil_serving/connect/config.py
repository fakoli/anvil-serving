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
_LITERAL_SECRET = re.compile(r"(?:password|secret|credential|private[_-]?key)", re.I)
_MAX_MANIFEST_BYTES = 1024 * 1024


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


def _loopback(value: Any, path: str) -> str:
    text = _string(value, path)
    match = re.fullmatch(r"127\.0\.0\.1:([1-9][0-9]{0,4})", text)
    if not match or int(match.group(1)) > 65535:
        raise _error(path, "must be a 127.0.0.1 TCP address")
    return text


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
    if access == "browser" and native_auth not in {"none", "passthrough"}:
        raise _error(path + ".native_auth", "browser resources require none or passthrough")
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
    raw = _mapping(value, "$", {"schema", "binary", "components", "config_root", "environment_files", "service_user", "gateway", "connectors", "clients", "caddy", "authelia"})
    if raw["schema"] != SCHEMA:
        raise _error("$.schema", f"must equal {SCHEMA}")
    binary = _abs_path(raw["binary"], "$.binary")
    components_raw = _mapping(raw["components"], "$.components", {"caddy", "authelia"})
    components = {name: _abs_path(components_raw[name], "$.components." + name) for name in ("caddy", "authelia")}
    config_root = _abs_path(raw["config_root"], "$.config_root")
    environment_raw = _mapping(raw["environment_files"], "$.environment_files", {"gateway", "connectors", "clients"})
    environment_gateway = _abs_path(environment_raw["gateway"], "$.environment_files.gateway")
    service_user = _ident(raw["service_user"], "$.service_user")

    gateway_raw = _mapping(raw["gateway"], "$.gateway", {"schema", "gateway", "control_host", "tunnel_host", "state_directory", "tunnel_binary", "tunnel_listen", "oidc"})
    if gateway_raw["schema"] != "anvil-connect.gateway-runtime/v1":
        raise _error("$.gateway.schema", "must equal anvil-connect.gateway-runtime/v1")
    embedded = _mapping(gateway_raw["gateway"], "$.gateway.gateway", {"schema", "listen", "max_concurrent", "resources"})
    if embedded["schema"] != "anvil-connect.gateway/v1":
        raise _error("$.gateway.gateway.schema", "must equal anvil-connect.gateway/v1")
    gateway_listen = _loopback(embedded["listen"], "$.gateway.gateway.listen")
    resource_raws = _list(embedded["resources"], "$.gateway.gateway.resources")
    resources: list[dict[str, Any]] = []
    for index, resource_raw in enumerate(resource_raws):
        item_path = f"$.gateway.gateway.resources[{index}]"
        item = _mapping(resource_raw, item_path, {"connector", "tunnel_address", "rule"})
        resources.append({
            "connector": _ident(item["connector"], item_path + ".connector"),
            "tunnel_address": _loopback(item["tunnel_address"], item_path + ".tunnel_address"),
            "rule": _rule(item["rule"], item_path + ".rule"),
        })
    if len({r["rule"]["id"] for r in resources}) != len(resources):
        raise _error("$.gateway.gateway.resources", "contains duplicate rule ids")
    if len({r["rule"]["host"] for r in resources}) != len(resources):
        raise _error("$.gateway.gateway.resources", "contains duplicate resource hosts")
    if len({r["tunnel_address"] for r in resources}) != len(resources) or any(r["tunnel_address"] == gateway_listen for r in resources):
        raise _error("$.gateway.gateway.resources", "contains duplicate or listener-equal tunnel addresses")
    if not any(resource["rule"]["access"] == "browser" for resource in resources):
        raise _error("$.gateway.gateway.resources", "managed deployment requires at least one browser resource")
    oidc = _mapping(gateway_raw["oidc"], "$.gateway.oidc", {"issuer", "client_id", "client_secret_env"})
    gateway = {
        "schema": gateway_raw["schema"],
        "gateway": {"schema": embedded["schema"], "listen": gateway_listen, "max_concurrent": _positive(embedded["max_concurrent"], "$.gateway.gateway.max_concurrent", 512), "resources": sorted(resources, key=lambda r: r["rule"]["id"])},
        "control_host": _host(gateway_raw["control_host"], "$.gateway.control_host"),
        "tunnel_host": _host(gateway_raw["tunnel_host"], "$.gateway.tunnel_host"),
        "state_directory": _abs_path(gateway_raw["state_directory"], "$.gateway.state_directory"),
        "tunnel_binary": _abs_path(gateway_raw["tunnel_binary"], "$.gateway.tunnel_binary"),
        "tunnel_listen": _loopback(gateway_raw["tunnel_listen"], "$.gateway.tunnel_listen"),
        "oidc": {"issuer": _issuer(oidc["issuer"], "$.gateway.oidc.issuer"), "client_id": _client_id(oidc["client_id"], "$.gateway.oidc.client_id"), "client_secret_env": _env(oidc["client_secret_env"], "$.gateway.oidc.client_secret_env")},
    }
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
        item = _mapping(connector_raw, path, {"schema", "id", "control_host", "tunnel_host", "state_directory", "tunnel_binary", "public_trust_file", "http_proxy_url", "resources"})
        if item["schema"] != "anvil-connect.connector-runtime/v1":
            raise _error(path + ".schema", "must equal anvil-connect.connector-runtime/v1")
        connector_resources = []
        for resource_index, resource_raw in enumerate(_list(item["resources"], path + ".resources")):
            rp = f"{path}.resources[{resource_index}]"
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
        item = _mapping(client_raw, path, {"schema", "rule", "listen", "local_key_env", "remote_key_env"})
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
        clients.append({"schema": item["schema"], "rule": rule, "listen": listen, "local_key_env": local_key_env, "remote_key_env": remote_key_env})
    if len({client["rule"]["id"] for client in clients}) != len(clients):
        raise _error("$.clients", "contains duplicate client rule ids")
    gateway_api_rules = {resource["rule"]["id"]: resource["rule"] for resource in gateway["gateway"]["resources"] if resource["rule"]["access"] == "api"}
    if any(gateway_api_rules.get(client["rule"]["id"]) != client["rule"] for client in clients):
        raise _error("$.clients", "every client rule must exactly match a gateway API rule")
    clients.sort(key=lambda c: c["rule"]["id"])
    connector_env_raw = _mapping(environment_raw["connectors"], "$.environment_files.connectors", set(connector_index))
    client_env_raw = _mapping(environment_raw["clients"], "$.environment_files.clients", {client["rule"]["id"] for client in clients})
    environment_files = {
        "gateway": environment_gateway,
        "connectors": {name: _abs_path(connector_env_raw[name], "$.environment_files.connectors." + name) for name in sorted(connector_env_raw)},
        "clients": {name: _abs_path(client_env_raw[name], "$.environment_files.clients." + name) for name in sorted(client_env_raw)},
    }

    caddy_raw = _mapping(raw["caddy"], "$.caddy", {"service_name", "tls"})
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
    caddy_name = _ident(caddy_raw["service_name"], "$.caddy.service_name")
    if caddy_name != "anvil-connect-caddy":
        raise _error("$.caddy.service_name", "must equal anvil-connect-caddy")
    caddy = {"service_name": caddy_name, "tls": {"mode": tls_mode, "certificate_file": certificate_file, "key_file": key_file}}

    authelia_raw = _mapping(raw["authelia"], "$.authelia", {"service_name", "host", "listen", "state_directory", "users_file", "client_secret_file", "session_secret_file", "storage_encryption_key_file", "identity_validation_secret_file", "oidc_hmac_secret_file", "oidc_rsa_private_key_file"})
    authelia = {key: _secret_file(authelia_raw[key], "$.authelia." + key) for key in ("users_file", "client_secret_file", "session_secret_file", "storage_encryption_key_file", "identity_validation_secret_file", "oidc_hmac_secret_file", "oidc_rsa_private_key_file")}
    authelia_name = _ident(authelia_raw["service_name"], "$.authelia.service_name")
    if authelia_name != "anvil-connect-authelia":
        raise _error("$.authelia.service_name", "must equal anvil-connect-authelia")
    authelia.update({"service_name": authelia_name, "host": _host(authelia_raw["host"], "$.authelia.host"), "listen": _loopback(authelia_raw["listen"], "$.authelia.listen"), "state_directory": _abs_path(authelia_raw["state_directory"], "$.authelia.state_directory")})
    all_hosts = {gateway["control_host"], gateway["tunnel_host"], *(r["rule"]["host"] for r in gateway["gateway"]["resources"])}
    if authelia["host"] in all_hosts:
        raise _error("$.authelia.host", "must be distinct from public resource, control, and tunnel hosts")
    if authelia["listen"] in all_listens:
        raise _error("$.authelia.listen", "must not reuse a native listener")
    issuer_host = gateway["oidc"]["issuer"].removeprefix("https://")
    if issuer_host != authelia["host"]:
        raise _error("$.gateway.oidc.issuer", "must equal the managed Authelia HTTPS host")

    return {"schema": SCHEMA, "binary": binary, "components": components, "config_root": config_root, "environment_files": environment_files, "service_user": service_user, "gateway": gateway, "connectors": connectors, "clients": clients, "caddy": caddy, "authelia": authelia}


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
