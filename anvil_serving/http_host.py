"""Host-header guard for HTTP listeners that run without a token.

DNS rebinding lets a web page point its own hostname at 127.0.0.1, so a
browser can reach a loopback listener while still sending
``Host: attacker.example``. A listener with no credential check accepts only
Host names that a browser on another origin cannot produce: the IPv4/IPv6
loopback literals and the configured bind host.

Standard library only and free of package imports so the router, voice,
dashboard, and multiplexer listeners can all share it.
"""

from __future__ import annotations

from typing import Optional

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
# A wildcard bind address is not a name a client should use; Chromium has
# historically routed http://0.0.0.0 to the local machine.
_WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::"})


def _hostname(value: str) -> Optional[str]:
    """Return the lowercase hostname of a Host header value, or None if malformed."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or (value[end + 1:] and not value[end + 1:].startswith(":")):
            return None
        host, port = value[1:end], value[end + 2:]
    elif value.count(":") > 1:
        host, port = value, ""  # bare IPv6 literal, no port
    else:
        host, _, port = value.partition(":")
    if port and not port.isdigit():
        return None
    return host or None


def host_header_allowed(value: Optional[str], bind_host: str) -> bool:
    """True when ``value`` names loopback or the configured non-wildcard bind host."""
    if not value:
        return False
    host = _hostname(value)
    if host is None:
        return False
    if host in LOOPBACK_HOSTS:
        return True
    bind = bind_host.strip().strip("[]").lower()
    return bind not in _WILDCARD_HOSTS and host == bind


def request_host_allowed(headers, bind_host: str) -> bool:
    """Apply :func:`host_header_allowed` to a message; duplicate Host headers fail."""
    values = headers.get_all("Host") or []
    return len(values) == 1 and host_header_allowed(values[0], bind_host)
