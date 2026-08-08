"""Controller bind, authentication, strict JSON, and redaction policy."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import re
import socket
import uuid
from typing import Any, Callable, Mapping, Optional, Sequence

from ...envfile import env_sources, resolve_env_value
from .errors import ControllerError


DEFAULT_AUTH_TOKEN_ENV = "ANVIL_CONTROLLER_TOKEN"

_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_RFC1918_V4 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_ULA_V6 = ipaddress.ip_network("fc00::/7")
_DOCUMENTATION_V4 = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
_WILDCARD_BINDS = {"", "0", "0.0.0.0", "::"}
_TOKEN_HEADER = "x-api-key"
_REQUEST_ID_HEADER = "X-Request-Id"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[^\s'\"\\]+"),
    re.compile(
        r"(?i)\b((?:access[_-]?key|api[_-]?key|authorization|client[_-]?secret|"
        r"private[_-]?key|secret[_-]?access[_-]?key|session[_-]?token|x-api-key)"
        r"\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"
    ),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{8,}\b"),
)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class BindSafetyError(ControllerError):
    """Raised when a requested bind address violates controller safety rules."""


@dataclass(frozen=True)
class BindAssessment:
    """Result of classifying a controller bind address."""

    host: str
    addresses: tuple[str, ...]
    loopback: bool
    private: bool
    tailscale: bool
    public: bool
    requires_auth: bool


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _strict_json_loads(value: str) -> Any:
    def reject_constant(constant: str) -> None:
        raise ValueError("non-finite JSON number: " + constant)

    return json.loads(value, parse_constant=reject_constant)


def _redact_secret(value: Any, secret: Optional[str]) -> Any:
    if isinstance(value, str):
        if secret:
            value = value.replace(secret, "<redacted>")
        for pattern in _SECRET_TEXT_PATTERNS:
            value = pattern.sub(
                lambda match: match.group(1) + "<redacted>" if match.lastindex else "<redacted>",
                value,
            )
        return value
    if isinstance(value, list):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, tuple):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            str(_redact_secret(str(key), secret)): _redact_secret(item, secret)
            for key, item in value.items()
        }
    return value


def _sanitize_persisted_value(value: Any, secret: Optional[str]) -> Any:
    if isinstance(value, str):
        return _redact_secret(value, secret)
    if isinstance(value, list):
        return [_sanitize_persisted_value(item, secret) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_persisted_value(item, secret) for item in value]
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(_redact_secret(str(key), secret))
            rendered[key_text] = (
                "<redacted>"
                if _is_sensitive_key(str(key))
                else _sanitize_persisted_value(item, secret)
            )
        return rendered
    return value


def _is_sensitive_key(key: str) -> bool:
    parts = tuple(part for part in re.split(r"[^a-z0-9]+", key.lower()) if part)
    compact = "".join(parts)
    if set(parts) & {"authorization", "credential", "credentials", "password", "secret", "token"}:
        return True
    return any(
        shape in compact
        for shape in (
            "accesskey",
            "accesstoken",
            "apikey",
            "authorization",
            "bearertoken",
            "clientsecret",
            "privatekey",
            "refreshtoken",
            "secretaccesskey",
            "sessiontoken",
        )
    )


def _is_tailscale_ip(addr: IPAddress) -> bool:
    if addr.version == 4:
        return addr in _TAILSCALE_V4
    return addr in _TAILSCALE_V6


def _is_safe_private_ip(addr: IPAddress) -> bool:
    if addr.is_unspecified or addr.is_link_local or addr.is_multicast or addr.is_reserved:
        return False
    if addr.version == 4:
        return bool(
            addr.is_loopback
            or _is_tailscale_ip(addr)
            or any(addr in network for network in _RFC1918_V4)
        )
    return bool(addr.is_loopback or addr in _ULA_V6 or _is_tailscale_ip(addr))


def _is_forbidden_bind_ip(addr: IPAddress) -> bool:
    if addr.is_unspecified or addr.is_link_local or addr.is_multicast or addr.is_reserved:
        return True
    if addr.version == 4 and any(addr in network for network in _DOCUMENTATION_V4):
        return True
    return False


def _resolve_bind_ips(
    host: str,
    *,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
) -> tuple[IPAddress, ...]:
    if host in _WILDCARD_BINDS:
        return ()
    if host.strip().lower() == "localhost":
        raise BindSafetyError(
            "localhost_not_allowed",
            "use 127.0.0.1 or ::1 instead of localhost",
            status=400,
            details={"host": host},
        )
    try:
        return (ipaddress.ip_address(host),)
    except ValueError:
        pass

    getaddrinfo = resolver or socket.getaddrinfo
    try:
        infos = getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise BindSafetyError(
            "bind_host_unresolved",
            "could not resolve bind host",
            status=400,
            details={"host": host, "error": str(exc)},
        ) from exc

    addrs: list[IPAddress] = []
    for info in infos:
        try:
            raw_addr = info[4][0]
            addrs.append(ipaddress.ip_address(raw_addr))
        except (IndexError, TypeError, ValueError):
            continue
    if not addrs:
        raise BindSafetyError(
            "bind_host_unresolved",
            "could not resolve bind host to an IP address",
            status=400,
            details={"host": host},
        )
    seen: set[str] = set()
    unique: list[IPAddress] = []
    for addr in addrs:
        rendered = str(addr)
        if rendered not in seen:
            unique.append(addr)
            seen.add(rendered)
    return tuple(unique)


def _env_has_token(auth_token_env: Optional[str], env: Optional[Mapping[str, str]]) -> bool:
    """True when the token resolves from ``env`` or, for the real process
    environment (``env is None``), from the operator dotenv fallback chain."""
    if not auth_token_env:
        return False
    value, _source = resolve_env_value(auth_token_env, env=env)
    return value is not None


def validate_bind_safety(
    host: str,
    *,
    allow_public_bind: bool = False,
    allow_unauthenticated_loopback: bool = False,
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    env: Optional[Mapping[str, str]] = None,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
) -> BindAssessment:
    """Validate controller bind safety and return the bind classification."""
    addrs = _resolve_bind_ips(host, resolver=resolver)

    wildcard_resolved = bool(addrs and any(addr.is_unspecified for addr in addrs))
    if host in _WILDCARD_BINDS or wildcard_resolved:
        loopback = False
        private = False
        tailscale = False
        public = True
        addresses: tuple[str, ...] = (
            (host,) if host in _WILDCARD_BINDS else tuple(str(addr) for addr in addrs)
        )
    else:
        loopback = all(addr.is_loopback for addr in addrs)
        private = all(addr.is_private for addr in addrs)
        tailscale = any(_is_tailscale_ip(addr) for addr in addrs)
        public = any(not _is_safe_private_ip(addr) for addr in addrs)
        addresses = tuple(str(addr) for addr in addrs)

    if not (host in _WILDCARD_BINDS or wildcard_resolved) and any(
        _is_forbidden_bind_ip(addr) for addr in addrs
    ):
        raise BindSafetyError(
            "unsafe_bind_address",
            "refusing to bind controller to a link-local, reserved, multicast, or documentation address",
            status=400,
            details={"host": host, "addresses": [str(addr) for addr in addrs]},
        )
    if public and not allow_public_bind:
        raise BindSafetyError(
            "public_bind_refused",
            "refusing to bind controller to a public address without --allow-public-bind",
            status=400,
            details={"host": host, "addresses": list(addresses)},
        )

    requires_auth = not (loopback and allow_unauthenticated_loopback)
    if requires_auth and not _env_has_token(auth_token_env, env):
        raise BindSafetyError(
            "auth_token_required",
            "controller binds require an auth token environment variable",
            status=400,
            details={
                "host": host,
                "auth_token_env": auth_token_env or None,
                "addresses": list(addresses),
                "sources_checked": env_sources(auth_token_env) if auth_token_env else [],
            },
        )
    return BindAssessment(
        host=host,
        addresses=addresses,
        loopback=loopback,
        private=private,
        tailscale=tailscale,
        public=public,
        requires_auth=requires_auth,
    )


def resolve_auth_token(
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    *,
    env: Optional[Mapping[str, str]] = None,
    required: bool = False,
) -> Optional[str]:
    if not auth_token_env:
        if required:
            raise ControllerError(
                "auth_token_required",
                "auth token environment variable name is required",
                status=400,
            )
        return None
    token, _source = resolve_env_value(auth_token_env, env=env)
    if token:
        return token
    if required:
        raise ControllerError(
            "auth_token_missing",
            "auth token environment variable is not set",
            status=400,
            details={
                "auth_token_env": auth_token_env,
                "sources_checked": env_sources(auth_token_env),
            },
        )
    return None


def _extract_request_token(headers: Any) -> Optional[str]:
    auth_header = headers.get("Authorization")
    if auth_header:
        scheme, _, value = auth_header.partition(" ")
        if scheme.strip().lower() == "bearer" and value.strip():
            return value.strip()
        return None
    api_key = headers.get(_TOKEN_HEADER)
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def _safe_request_id(value: Optional[str]) -> str:
    if value and _REQUEST_ID_RE.fullmatch(value):
        return value
    return uuid.uuid4().hex
