from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
LOCK = ROOT / "connect/lab/edge-tools.json"
_SHA256 = re.compile(r"[0-9a-f]{64}$")
_SHA512 = re.compile(r"[0-9a-f]{128}$")


def test_pinned_upstream_validators_accept_render_and_reject_negative_controls() -> None:
    if os.environ.get("ANVIL_CONNECT_EDGE_QUALIFY") != "1":
        pytest.skip("set ANVIL_CONNECT_EDGE_QUALIFY=1 to download and run pinned upstream validators")
    harness = ROOT / "connect/lab/validate-edge-tools.sh"
    result = subprocess.run(
        [str(harness)], cwd=ROOT, check=True, capture_output=True, text=True, timeout=420,
    )
    assert "validated Caddy 2.11.3 and Authelia 4.39.28" in result.stdout
