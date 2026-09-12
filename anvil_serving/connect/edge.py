"""Publish declared Connect resources through one Cloudflare-managed edge.

The deployment manifest remains the closed source of truth for WHAT is
published. This module derives the required public DNS records and tunnel
ingress rules from that manifest, compares them with the live Cloudflare
state, and applies exactly the difference. Cloudflare credentials are read
from one environment reference, never from configuration or output.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Mapping

from .. import paths

API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_TOKEN_ENV = "ANVIL_CLOUDFLARE_API_TOKEN"
DEFAULT_ORIGIN_SERVICE = "https://127.0.0.1:19443"
_TIMEOUT_SECONDS = 20
_MAX_OUTPUT = 8 * 1024
_ACCOUNT_RE = re.compile(r"^[0-9a-f]{32}$")
_TUNNEL_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_TOKEN_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
_HOSTNAME_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class EdgeError(ValueError):
    """The Cloudflare edge declaration, credentials, or state is not usable."""


def _absolute_path(value: object, label: str) -> Path:
    if type(value) is not str:
        raise EdgeError(f"{label} must be an absolute private path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise EdgeError(f"{label} must be a safe absolute private path")
    return path


@dataclass(frozen=True)
class EdgeConfig:
    """One reviewed Cloudflare publishing declaration."""

    account_id: str
    zone_name: str
    tunnel_id: str
    ca_pool: Path
    api_token_env: str = DEFAULT_TOKEN_ENV
    origin_service: str = DEFAULT_ORIGIN_SERVICE
    origin_server_name: str | None = None


def edge_config(value: Mapping[str, object]) -> EdgeConfig:
    """Parse only the declared edge-publishing fields, strictly."""
    if not isinstance(value, Mapping):
        raise EdgeError("edge publishing configuration must be an object")
    declared = value.get("edge_publishing", value)
    if not isinstance(declared, Mapping):
        raise EdgeError("edge publishing configuration must be an object")
    allowed = {
        "schema", "account_id", "zone_name", "tunnel_id", "ca_pool",
        "api_token_env", "origin_service", "origin_server_name",
    }
    if set(declared) - allowed:
        raise EdgeError("edge publishing configuration has unsupported fields")
    schema = declared.get("schema")
    if schema is not None and schema != "anvil-connect.edge-publishing/v1":
        raise EdgeError("edge publishing schema must be anvil-connect.edge-publishing/v1")
    account_id = declared.get("account_id")
    if type(account_id) is not str or not _ACCOUNT_RE.fullmatch(account_id):
        raise EdgeError("edge_publishing.account_id must be a 32-character hexadecimal Cloudflare account id")
    zone = declared.get("zone_name")
    if type(zone) is not str or not _HOSTNAME_RE.fullmatch(zone):
        raise EdgeError("edge_publishing.zone_name must be a public DNS zone name")
    tunnel_id = declared.get("tunnel_id")
    if type(tunnel_id) is not str or not _TUNNEL_RE.fullmatch(tunnel_id):
        raise EdgeError("edge_publishing.tunnel_id must be the cloudflared tunnel UUID")
    ca_pool = _absolute_path(declared.get("ca_pool"), "edge_publishing.ca_pool")
    token_env = declared.get("api_token_env", DEFAULT_TOKEN_ENV)
    if type(token_env) is not str or not _TOKEN_ENV_RE.fullmatch(token_env):
        raise EdgeError("edge_publishing.api_token_env must be an environment variable name")
    origin_service = declared.get("origin_service", DEFAULT_ORIGIN_SERVICE)
    if type(origin_service) is not str or origin_service not in {
        "https://127.0.0.1:19443", "http://127.0.0.1:19443",
    }:
        raise EdgeError("edge_publishing.origin_service must be the loopback edge listener URL")
    server_name = declared.get("origin_server_name")
    if server_name is not None and (type(server_name) is not str or not _HOSTNAME_RE.fullmatch(server_name)):
        raise EdgeError("edge_publishing.origin_server_name must be an exact host name")
    return EdgeConfig(
        account_id=account_id,
        zone_name=zone,
        tunnel_id=tunnel_id,
        ca_pool=ca_pool,
        api_token_env=token_env,
        origin_service=origin_service,
        origin_server_name=server_name,
    )


def load_config(path: str | os.PathLike[str] | None = None) -> EdgeConfig:
    """Load the explicit or conventional edge-publishing configuration."""
    source = Path(path).expanduser() if path is not None else Path(
        paths.config_path("connect", "edge-cloudflare.json")
    )
    if not source.is_file():
        raise EdgeError(
            "edge publishing configuration is required; create "
            f"{Path(paths.config_path('connect', 'edge-cloudflare.json'))} "
            "with the account id, zone, tunnel id, and CA pool path"
        )
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EdgeError(f"cannot read edge publishing configuration {source}: {exc}") from exc
    return edge_config(value)


def published_hosts(data: Mapping[str, object]) -> tuple[str, ...]:
    """Derive the public host names a closed manifest publishes, sorted."""
    hosts: set[str] = set()
    connectors = data.get("connectors")
    if not isinstance(connectors, list):
        raise EdgeError("deployment declaration has no connectors")
    for connector in connectors:
        if not isinstance(connector, Mapping):
            continue
        for resource in connector.get("resources", ()) or ():
            envelope = resource.get("envelope", resource) if isinstance(resource, Mapping) else None
            rule = envelope.get("rule") if isinstance(envelope, Mapping) else None
            if isinstance(rule, Mapping) and type(rule.get("host")) is str:
                hosts.add(rule["host"].lower())
    if not hosts:
        raise EdgeError("deployment declaration publishes no resources")
    return tuple(sorted(hosts))


def _token(config: EdgeConfig) -> str:
    token = os.environ.get(config.api_token_env, "")
    if not token:
        raise EdgeError(
            f"Cloudflare API token is not available; set {config.api_token_env} "
            "with Zone.DNS Edit and Account.Cloudflare Tunnel Edit scopes"
        )
    return token


def _fetch_json(
    opener: Callable[[str, str, str | None], object],
    config: EdgeConfig,
    method: str,
    path: str,
    payload: Mapping[str, object] | None = None,
) -> object:
    url = f"{API_BASE}{path}"
    token = _token(config)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    try:
        result = opener(url, method, body, token)
    except EdgeError:
        raise
    except (OSError, ValueError) as exc:
        raise EdgeError(f"Cloudflare API request failed: {type(exc).__name__}") from exc
    if not isinstance(result, Mapping) or result.get("success") is not True:
        codes = [
            str(item.get("code"))
            for item in (result.get("errors") or []) if isinstance(item, Mapping)
        ] if isinstance(result, Mapping) else []
        raise EdgeError(f"Cloudflare API rejected {method} {path.split('?')[0]}; error codes: {codes}")
    return result.get("result")


def _default_opener(url: str, method: str, body: bytes | None, token: str) -> object:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return json.loads(response.read(_MAX_OUTPUT * 4).decode("utf-8"))


def _ingress_rule(
    config: EdgeConfig, host: str
) -> dict[str, object]:
    origin_request: dict[str, object] = {"caPool": str(config.ca_pool), "noTLSVerify": False}
    if config.origin_server_name:
        origin_request["originServerName"] = config.origin_server_name
    return {"hostname": host, "service": config.origin_service, "originRequest": origin_request}


def _managed_ingress(
    config: EdgeConfig, hosts: tuple[str, ...], current: Mapping[str, object]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Split the current ingress into preserved rules and a new catch-all."""
    managed = set(hosts)
    rules = current.get("ingress")
    preserved: list[dict[str, object]] = []
    catch_all: dict[str, object] | None = None
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, Mapping):
                preserved.append(dict(rule))
                continue
            hostname = rule.get("hostname")
            if hostname is None and catch_all is None:
                catch_all = dict(rule)
                continue
            if isinstance(hostname, str) and hostname.lower() in managed:
                continue
            preserved.append(dict(rule))
    if catch_all is None:
        catch_all = {"service": "http_status:404"}
    desired = [_ingress_rule(config, host) for host in hosts] + preserved + [catch_all]
    return desired, preserved


def _dns_state(
    opener: Callable[[str, str, str | None], object],
    config: EdgeConfig,
    zone_id: str,
    hosts: tuple[str, ...],
) -> dict[str, str]:
    target = f"{config.tunnel_id}.cfargotunnel.com"
    state: dict[str, str] = {}
    for host in hosts:
        records = _fetch_json(
            opener, config,
            "GET",
            f"/zones/{zone_id}/dns_records?type=CNAME&name={urllib.parse.quote(host, safe='')}&per_page=5",
        )
        entries = records if isinstance(records, list) else []
        exact = [
            item for item in entries
            if isinstance(item, Mapping) and str(item.get("name", "")).lower() == host
        ]
        if not exact:
            state[host] = "create"
        else:
            record = exact[0]
            content = str(record.get("content", ""))
            proxied = record.get("proxied") is True
            state[host] = "unchanged" if content == target and proxied else "update"
    return state


def plan(
    config: EdgeConfig,
    manifest_path: str | os.PathLike[str],
    *,
    fetch: Callable[[str, str, str | None], object] | None = None,
) -> dict[str, object]:
    """Read-only comparison of the declared edge with the live Cloudflare state."""
    from . import manage

    data = manage.read_manifest(manifest_path)
    hosts = published_hosts(data)
    outside = [host for host in hosts if not host.endswith("." + config.zone_name)]
    if outside:
        raise EdgeError(f"declared hosts outside the configured zone {config.zone_name}: {outside}")
    fetch = fetch or _default_opener
    zones = _fetch_json(fetch, config, "GET", f"/zones?name={urllib.parse.quote(config.zone_name, safe='')}")
    if not isinstance(zones, list) or not zones:
        raise EdgeError(f"zone {config.zone_name} is not visible to the configured token")
    zone_id = str(zones[0].get("id", ""))
    tunnel = _fetch_json(fetch, config, "GET", f"/accounts/{config.account_id}/cfd_tunnel/{config.tunnel_id}")
    current_config = _fetch_json(
        fetch, config, "GET", f"/accounts/{config.account_id}/cfd_tunnel/{config.tunnel_id}/configurations"
    )
    desired, preserved = _managed_ingress(config, hosts, current_config if isinstance(current_config, Mapping) else {})
    dns_state = _dns_state(fetch, config, zone_id, hosts)
    ingress_changed = desired != (
        current_config.get("ingress") if isinstance(current_config, Mapping) else None
    )
    return {
        "schema": "anvil-connect.edge-plan/v1",
        "zone": config.zone_name,
        "tunnel": {
            "id": config.tunnel_id,
            "status": tunnel.get("status") if isinstance(tunnel, Mapping) else "unknown",
            "connections": len(tunnel.get("connections") or []) if isinstance(tunnel, Mapping) else 0,
        },
        "dns": dns_state,
        "ingress": {
            "managed_hosts": list(hosts),
            "preserved_rules": len(preserved),
            "changed": ingress_changed,
        },
    }


def apply(
    config: EdgeConfig,
    manifest_path: str | os.PathLike[str],
    *,
    confirm: bool,
    fetch: Callable[[str, str, str | None], object] | None = None,
) -> dict[str, object]:
    """Apply exactly the declared edge difference; idempotent on repetition."""

    report = plan(config, manifest_path, fetch=fetch)
    if not confirm:
        return {"dry_run": True, **report}
    fetch = fetch or _default_opener
    hosts = tuple(report["ingress"]["managed_hosts"])  # type: ignore[index]
    applied: list[str] = []
    if report["ingress"]["changed"]:  # type: ignore[index]
        current_config = _fetch_json(
            fetch, config, "GET", f"/accounts/{config.account_id}/cfd_tunnel/{config.tunnel_id}/configurations"
        )
        desired, _ = _managed_ingress(
            config, hosts, current_config if isinstance(current_config, Mapping) else {}
        )
        _fetch_json(
            fetch, config, "PUT",
            f"/accounts/{config.account_id}/cfd_tunnel/{config.tunnel_id}/configurations",
            {"config": {"ingress": desired}},
        )
        applied.append("tunnel_ingress")
    zone_id = str(_fetch_json(
        fetch, config, "GET", f"/zones?name={urllib.parse.quote(config.zone_name, safe='')}"
    )[0].get("id", ""))
    target = f"{config.tunnel_id}.cfargotunnel.com"
    for host in hosts:
        action = report["dns"][host]  # type: ignore[index]
        if action == "unchanged":
            continue
        if action == "create":
            _fetch_json(fetch, config, "POST", f"/zones/{zone_id}/dns_records", {
                "type": "CNAME", "name": host, "content": target, "proxied": True, "ttl": 1,
            })
        else:
            records = _fetch_json(
                fetch, config, "GET",
                f"/zones/{zone_id}/dns_records?type=CNAME&name={urllib.parse.quote(host, safe='')}&per_page=5",
            )
            record_id = str(records[0].get("id", ""))
            _fetch_json(fetch, config, "PUT", f"/zones/{zone_id}/dns_records/{record_id}", {
                "type": "CNAME", "name": host, "content": target, "proxied": True, "ttl": 1,
            })
        applied.append(f"dns:{host}")
    verified = plan(config, manifest_path, fetch=fetch)
    tunnel = verified["tunnel"]  # type: ignore[index]
    if tunnel["status"] != "connected" or not tunnel["connections"]:  # type: ignore[index]
        raise EdgeError(
            "edge applied but the tunnel is not reporting an active connector; "
            "verify the cloudflared agent is running with this tunnel's token"
        )
    return {
        "schema": "anvil-connect.edge-apply/v1",
        "applied": applied,
        "preserved_rules": verified["ingress"]["preserved_rules"],  # type: ignore[index]
        "tunnel": tunnel,
        "dns": verified["dns"],  # type: ignore[index]
        "converged": not applied,
    }