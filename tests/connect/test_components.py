from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
LOCK = ROOT / "connect/lab/edge-tools.json"
_SHA256 = re.compile(r"[0-9a-f]{64}$")
_SHA512 = re.compile(r"[0-9a-f]{128}$")


def test_edge_tools_lock_has_pinned_primary_provenance_and_oss_licenses() -> None:
    value = json.loads(LOCK.read_text(encoding="utf-8"))
    assert value["schema"] == "anvil-connect.edge-tools/v1"
    components = {item["name"]: item for item in value["components"]}
    assert set(components) == {"caddy", "authelia"}
    assert components["caddy"]["version"] == "2.11.3"
    assert components["authelia"]["version"] == "4.39.20"
    for name, component in components.items():
        assert component["url"].startswith(f"https://github.com/{'caddyserver/caddy' if name == 'caddy' else 'authelia/authelia'}/releases/download/")
        assert component["checksum"]["provenance_url"].startswith("https://github.com/")
        assert component["license"]["spdx"] == "Apache-2.0"
        assert component["license"]["evidence_url"].startswith("https://github.com/")
        assert _SHA256.fullmatch(component["binary_sha256"])
    assert _SHA512.fullmatch(components["caddy"]["checksum"]["value"])
    assert _SHA256.fullmatch(components["authelia"]["checksum"]["value"])


def test_pinned_upstream_validators_accept_render_and_reject_negative_controls() -> None:
    if os.environ.get("ANVIL_CONNECT_EDGE_QUALIFY") != "1":
        pytest.skip("set ANVIL_CONNECT_EDGE_QUALIFY=1 to download and run pinned upstream validators")
    harness = ROOT / "connect/lab/validate-edge-tools.sh"
    result = subprocess.run(
        [str(harness)], cwd=ROOT, check=True, capture_output=True, text=True, timeout=420,
    )
    assert "validated Caddy 2.11.3 and Authelia 4.39.20" in result.stdout
