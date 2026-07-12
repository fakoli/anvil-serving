"""Configure and inspect optional external collector adapters."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .operator_output import redact


SCHEMA_VERSION = 1
SUPPORTED_ADAPTERS = ("anvil-json-v1",)
_MAX_RESPONSE_BYTES = 256 * 1024
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_PRIVATE_V4 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)
_PRIVATE_V6 = ipaddress.ip_network("fc00::/7")


@dataclass(frozen=True, slots=True)
class CollectorConfig:
    name: str
    adapter: str
    endpoint: str
    capabilities: tuple[str, ...]
    auth_env: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 128:
            raise ValueError("collector name must contain 1-128 characters")
        if self.adapter not in SUPPORTED_ADAPTERS:
            raise ValueError("unsupported collector adapter")
        normalized_endpoint, loopback = _validate_endpoint(self.endpoint)
        object.__setattr__(self, "endpoint", normalized_endpoint)
        capabilities = tuple(self.capabilities)
        if not capabilities or len(capabilities) > 64:
            raise ValueError("collector capabilities must contain 1-64 entries")
        if any(
            not isinstance(item, str)
            or not _CAPABILITY.fullmatch(item)
            for item in capabilities
        ):
            raise ValueError("collector capability identifiers are invalid")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("collector capabilities must not contain duplicates")
        object.__setattr__(self, "capabilities", tuple(sorted(capabilities)))
        if self.auth_env is not None and (
            not isinstance(self.auth_env, str)
            or not _ENV_NAME.fullmatch(self.auth_env)
        ):
            raise ValueError("auth_env must name an uppercase environment variable")
        if not loopback and self.auth_env is None:
            raise ValueError("private non-loopback collector endpoints require auth_env")
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> CollectorConfig:
        if not isinstance(data, Mapping):
            raise TypeError("collector configuration must be an object")
        allowed = {
            "schema_version",
            "name",
            "adapter",
            "endpoint",
            "capabilities",
            "auth_env",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("unknown collector configuration fields: " + ", ".join(sorted(unknown)))
        capabilities = data.get("capabilities")
        if not isinstance(capabilities, list):
            raise TypeError("collector capabilities must be an array")
        return cls(
            name=data.get("name"),
            adapter=data.get("adapter"),
            endpoint=data.get("endpoint"),
            capabilities=tuple(capabilities),
            auth_env=data.get("auth_env"),
            schema_version=data.get("schema_version"),
        )


def inspect_adapter(
    config: CollectorConfig,
    *,
    environment: Mapping[str, str] | None = None,
    opener=None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Perform one bounded authenticated read from an explicit endpoint."""

    if not 0 < timeout <= 60:
        raise ValueError("timeout must be between 0 and 60 seconds")
    env = os.environ if environment is None else environment
    headers = {"Accept": "application/json"}
    secret = ""
    if config.auth_env:
        secret = (env.get(config.auth_env) or "").strip()
        if not secret:
            return _degraded(config, "permission-denied", "authentication environment is unset")
        headers["Authorization"] = "Bearer " + secret
    request = urllib.request.Request(config.endpoint, headers=headers, method="GET")
    open_request = opener or _urlopen_no_proxy_no_redirect
    try:
        with open_request(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if not isinstance(raw, bytes):
            raise ValueError("collector response body must be bytes")
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("collector response exceeds 256 KiB")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise TypeError("collector response must be an object")
        reported = payload.get("capabilities")
        if payload.get("status") != "ok" or not isinstance(reported, list) or not all(
            isinstance(item, str) for item in reported
        ):
            raise ValueError("collector response has an invalid capability document")
        available = sorted(set(config.capabilities) & set(reported))
        missing = sorted(set(config.capabilities) - set(reported))
        return redact(
            {
                "ok": not missing,
                "status": "ok" if not missing else "degraded",
                "capability_status": "ok" if not missing else "missing",
                "collector": config.name,
                "adapter": config.adapter,
                "endpoint": config.endpoint,
                "authentication": "bearer-env" if config.auth_env else "none-loopback",
                "available_capabilities": available,
                "missing_capabilities": missing,
            },
            secrets=(secret,),
        )
    except urllib.error.HTTPError as exc:
        status = "permission-denied" if exc.code in {401, 403} else "failed"
        return _degraded(config, status, f"collector returned HTTP {exc.code}", secret)
    except Exception as exc:
        return _degraded(config, "failed", str(exc), secret)


def _degraded(
    config: CollectorConfig, status: str, detail: str, secret: str = ""
) -> dict[str, Any]:
    return redact(
        {
            "ok": False,
            "status": "degraded",
            "capability_status": status,
            "collector": config.name,
            "adapter": config.adapter,
            "endpoint": config.endpoint,
            "available_capabilities": [],
            "missing_capabilities": list(config.capabilities),
            "detail": detail,
        },
        secrets=(secret,),
    )


def _validate_endpoint(value: object) -> tuple[str, bool]:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("collector endpoint must be a non-empty URL")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("collector endpoint must use http or https with an IP host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("collector endpoint must not contain credentials, query, or fragment")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("collector endpoint host must be an explicit IP address") from exc
    loopback = address.is_loopback
    private = (
        loopback
        or any(address in network for network in _PRIVATE_V4 if address.version == 4)
        or (address.version == 6 and address in _PRIVATE_V6)
    )
    if not private:
        raise ValueError("collector endpoint must be loopback or private/tailnet scoped")
    return value.rstrip("/"), loopback


def _urlopen_no_proxy_no_redirect(request, timeout: float):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    ).open(request, timeout=timeout)


def _load_config(path: str) -> CollectorConfig:
    target = Path(path)
    if target.stat().st_size > 256 * 1024:
        raise ValueError("collector configuration exceeds 256 KiB")
    payload = target.read_text(encoding="utf-8")
    if len(payload.encode("utf-8")) > 256 * 1024:
        raise ValueError("collector configuration exceeds 256 KiB")
    return CollectorConfig.from_mapping(json.loads(payload))


def _config_from_args(args) -> CollectorConfig:
    if args.config:
        if any(
            value is not None
            for value in (args.name, args.adapter, args.endpoint, args.auth_env)
        ) or args.capability:
            raise ValueError("--config cannot be combined with inline collector fields")
        return _load_config(args.config)
    if not args.name or not args.endpoint or not args.capability:
        raise ValueError("inline configuration requires --name, --endpoint, and --capability")
    return CollectorConfig(
        name=args.name,
        adapter=args.adapter or SUPPORTED_ADAPTERS[0],
        endpoint=args.endpoint,
        capabilities=tuple(args.capability),
        auth_env=args.auth_env,
    )


def _write_config(path: str, config: CollectorConfig) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(config.as_dict(), stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving collectors",
        description="Configure and inspect optional read-only collector adapters.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action, help_text in (
        ("configure", "Validate and optionally write adapter configuration."),
        ("validate", "Validate adapter configuration without network access."),
        ("capabilities", "Report configured adapter capabilities offline."),
        ("inspect", "Perform one bounded read-only adapter inspection."),
    ):
        command = subparsers.add_parser(action, help=help_text)
        command.add_argument("--config")
        command.add_argument("--name")
        command.add_argument("--adapter", choices=SUPPORTED_ADAPTERS)
        command.add_argument("--endpoint")
        command.add_argument("--capability", action="append", default=[])
        command.add_argument("--auth-env")
        if action == "configure":
            command.add_argument("--output")
        if action == "inspect":
            command.add_argument("--timeout", type=float, default=5.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "capabilities" and not args.config and not args.name:
            result = {
                "ok": True,
                "status": "not-configured",
                "capability_status": "unsupported",
                "available_capabilities": [],
            }
        else:
            config = _config_from_args(args)
            if args.action == "configure":
                if args.output:
                    _write_config(args.output, config)
                result = {"ok": True, "status": "configured", "config": config.as_dict()}
            elif args.action == "validate":
                result = {"ok": True, "status": "valid", "config": config.as_dict()}
            elif args.action == "capabilities":
                result = {
                    "ok": True,
                    "status": "configured",
                    "capability_status": "ok",
                    "collector": config.name,
                    "available_capabilities": list(config.capabilities),
                }
            else:
                if not 0 < args.timeout <= 60:
                    raise ValueError("--timeout must be between 0 and 60 seconds")
                result = inspect_adapter(config, timeout=args.timeout)
        print(json.dumps(redact(result), sort_keys=True))
        return 0 if result.get("ok") else 1
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "status": "invalid", "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
