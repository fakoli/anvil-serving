"""Static contracts for the optional gateway-owned Observatory Access shell."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "anvil_serving/observability/dashboard/static"


def test_connect_access_api_rejects_oversized_streams_and_malformed_tokens() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("requires Node for the browser response-boundary fixture")
    completed = subprocess.run(
        [node, str(Path(__file__).with_name("connect_access_api_ui.cjs")), str(STATIC / "views/connect_access_api.js")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_connect_access_view_disables_malformed_records_without_mutation() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("requires Node for the browser view-boundary fixture")
    completed = subprocess.run(
        [node, str(Path(__file__).with_name("connect_access_view_ui.cjs")), str(STATIC / "views/connect_access.js")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_connect_access_browser_boundary_is_same_origin_memory_only_and_fail_closed() -> None:
    source = (STATIC / "views/connect_access_api.js").read_text()
    assert "localStorage" not in source and "sessionStorage" not in source
    assert "candidate.origin !== window.location.origin" in source
    assert 'credentials: "same-origin"' in source
    assert 'redirect: "error"' in source
    assert 'headers["X-CSRF-Token"] = csrf' in source
    assert "{ ...body, csrf }" in source
    for status in ("status === 401", "status === 403", "status === 409", "status === 429"):
        assert status in source
    assert "Access changed before this request completed. Reload and review the current inventory." in source


def test_connect_access_view_uses_text_only_metadata_manual_refresh_and_no_account_creation() -> None:
    source = (STATIC / "views/connect_access.js").read_text()
    assert "innerHTML" not in source
    assert "Refresh access inventory" in source
    assert "Load next page" in source
    assert "Disable access" in source
    assert "Creating identity-provider accounts or invitations is not available here." in source
    assert "do not claim that a device is online" in source
    assert "crypto.randomUUID()" in source


def test_access_route_skips_fleet_reads_and_auto_refresh() -> None:
    source = (STATIC / "observatory.js").read_text()
    assert 'if (target.page !== "access") {' in source
    assert '!["configuration", "experiments", "settings", "logs", "access"].includes(target.page)' in source
    assert '!["configuration", "experiments", "settings", "access"].includes(parseRoute().page)' in source
