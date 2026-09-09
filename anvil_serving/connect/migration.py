"""Pure, host-independent Observatory-to-Connect migration planning.

The preview establishes one canonical browser origin.  It neither starts a
service nor changes the Observatory application, its private state, or any
credential.  A selected deployment and external cutover remain an operator
review decision.
"""
from __future__ import annotations

from pathlib import Path
import os
import stat
from typing import Any

from .config import read_manifest
from ..observability.dashboard.contracts import strict_json

_MAX_OBSERVATORY_CONFIG = 262_144


def _read_observatory_config(path: str | Path) -> dict[str, Any]:
    """Read the preview inputs through one bounded no-follow regular FD."""
    value = Path(path)
    if not value.is_absolute() or str(value) != str(path):
        raise MigrationError("Observatory configuration path is invalid")
    try:
        descriptor = os.open(value, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise MigrationError("Observatory configuration is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_OBSERVATORY_CONFIG:
            raise MigrationError("Observatory configuration is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = _MAX_OBSERVATORY_CONFIG + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > _MAX_OBSERVATORY_CONFIG:
            raise MigrationError("Observatory configuration exceeds its bound")
    finally:
        os.close(descriptor)
    try:
        config = strict_json(raw)
    except Exception as exc:
        raise MigrationError("Observatory configuration is invalid") from exc
    required = {"schema", "origin", "base_path", "users", "authentication", "inventory", "prometheus_url", "state_path"}
    # Keep this top-level shape aligned with Observatory's closed loader.  The
    # migration plan does not consume or disclose the optional log collector
    # binding; it only needs to accept an otherwise-valid current app config.
    allowed = required | {"operate", "grafana_url", "controller", "workload", "build", "fixture", "strip_prefix", "evidence", "logs"}
    if not isinstance(config, dict) or set(config) - allowed or required - set(config) or config.get("schema") != "anvil-observatory/config/v1":
        raise MigrationError("Observatory configuration is invalid")
    return config


class MigrationError(RuntimeError):
    """The selected app and Connect declarations cannot preserve one origin."""


def _browser_resource(data: dict[str, Any], resource_id: str) -> dict[str, Any]:
    if not isinstance(resource_id, str) or not resource_id:
        raise MigrationError("Observatory resource identifier is invalid")
    matches = [item for item in data["gateway"]["gateway"]["resources"] if item["rule"]["id"] == resource_id]
    if len(matches) != 1:
        raise MigrationError("Observatory resource is not declared exactly once")
    selected = matches[0]
    rule = selected["rule"]
    if rule["access"] != "browser" or rule["native_auth"] != "passthrough":
        raise MigrationError("Observatory requires a browser passthrough resource")
    return selected


def _base_prefix(base_path: Any) -> str:
    if not isinstance(base_path, str) or not base_path.startswith("/") or not base_path.endswith("/"):
        raise MigrationError("Observatory base path is invalid")
    prefix = "/" if base_path == "/" else base_path[:-1]
    if base_path != "/" and prefix == "/":
        raise MigrationError("Observatory path mapping is invalid")
    _canonical_path(prefix)
    return prefix


def _canonical_path(value: Any) -> None:
    """Accept one literal browser path map, never a URL normalization request."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise MigrationError("Observatory path mapping is invalid")
    if value == "/":
        return
    if value != "/" and value.endswith("/"):
        raise MigrationError("Observatory path mapping is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value) or any(character in value for character in "%?#\\"):
        raise MigrationError("Observatory path mapping is invalid")
    segments = value.split("/")[1:]
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise MigrationError("Observatory path mapping is invalid")
    if value == "/_anvil-connect" or value.startswith("/_anvil-connect/"):
        raise MigrationError("Observatory path mapping overlaps reserved Connect controls")


def _connector_binding(data: dict[str, Any], selected: dict[str, Any]) -> tuple[str, str]:
    connector_id = selected["connector"]
    connectors = [item for item in data["connectors"] if item["id"] == connector_id]
    if len(connectors) != 1:
        raise MigrationError("Observatory connector is not declared exactly once")
    rule = selected["rule"]
    matching = [resource for resource in connectors[0]["resources"] if resource["envelope"]["rule"] == rule]
    if len(matching) != 1:
        raise MigrationError("Observatory resource lacks one fixed connector envelope")
    return connector_id, matching[0]["envelope"]["origin_url"]


def preview_observatory(manifest_path: str | Path, observatory_config_path: str | Path, *, resource_id: str) -> dict[str, Any]:
    """Return a no-write, exact-origin migration/cutover/rollback plan.

    The Observatory origin must already equal the selected browser resource's
    public HTTPS origin.  A hostname change invalidates application cookies and
    needs a separately reviewed re-login migration; this function refuses to
    imply that both old and new origins can remain unchanged.
    """
    data = read_manifest(manifest_path)
    config = _read_observatory_config(observatory_config_path)
    selected = _browser_resource(data, resource_id)
    rule = selected["rule"]
    _canonical_path(rule["path_prefix"])
    canonical_origin = "https://" + rule["host"]
    if config["origin"] != canonical_origin:
        raise MigrationError("origin change requires an explicit cookie re-login migration")
    base_path = config["base_path"]
    if _base_prefix(base_path) != rule["path_prefix"] or config.get("strip_prefix", True) is not False:
        raise MigrationError("Observatory base path requires an exact non-stripping browser resource mapping")
    if not {"GET", "POST"}.issubset(set(rule["methods"])):
        raise MigrationError("Observatory browser resource must allow exact GET and POST methods")
    connector_id, origin_url = _connector_binding(data, selected)
    return {
        "schema": "anvil-connect.observatory-migration/v1",
        "action": "observatory-migration-preview",
        "applied": False,
        "canonical_origin": canonical_origin,
        "resource": {
            "id": rule["id"],
            "host": rule["host"],
            "path_prefix": rule["path_prefix"],
            "base_path": base_path,
            "access": "browser",
            "native_auth": "passthrough",
            "connector": connector_id,
            "declared_loopback_origin": origin_url,
        },
        "steps": [
            {"order": 1, "phase": "verify", "action": "run-app-side-session-origin-csrf-action-checks"},
            {"order": 2, "phase": "prepare", "action": "validate-managed-gateway-and-connector-declarations"},
            {"order": 3, "phase": "prepare", "action": "initialize-gateway-and-explicitly-enroll-connector"},
            {"order": 4, "phase": "activate", "action": "start-managed-gateway-before-selected-connector"},
            {"order": 5, "phase": "cutover", "action": "route-only-the-canonical-origin-through-the-fixed-browser-resource"},
            {"order": 6, "phase": "cutover", "action": "retire-the-prior-direct-public-route-before-readiness-is-claimed"},
        ],
        "rollback": [
            {"order": 1, "action": "restore-the-prior-public-route-before-disabling-the-selected-connector"},
            {"order": 2, "action": "stop-selected-connect-role-and-restore-prior-rendered-public-config"},
            {"order": 3, "action": "retain-Observatory-session-state-action-grants-and-native-credentials"},
        ],
        "pending": [
            "Select the external deployment and approve a concrete cutover window.",
            "Perform browser and origin qualification against that selected deployment.",
        ],
    }
