from __future__ import annotations

from pathlib import Path

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.node_bridge import run_node_bridge


def test_packaged_bridge_launcher_keeps_controller_token_out_of_argv(
    monkeypatch,
):
    monkeypatch.setenv("ANVIL_CONTROLLER_TOKEN", "controller-secret")
    seen = {}

    def call(argv):
        seen["argv"] = argv
        return 7

    result = run_node_bridge(
        "http://127.0.0.1:8765",
        "ANVIL_CONTROLLER_TOKEN",
        "0.17.0",
        replace_process=False,
        which=lambda _name: "/opt/homebrew/bin/node",
        call=call,
    )

    assert result == 7
    assert Path(seen["argv"][1]).name == "mcp_proxy.mjs"
    assert seen["argv"][-2:] == ["--server-version", "0.17.0"]
    assert "controller-secret" not in "\0".join(seen["argv"])


def test_packaged_bridge_launcher_requires_node(monkeypatch):
    monkeypatch.setenv("ANVIL_CONTROLLER_TOKEN", "controller-secret")

    with pytest.raises(ToolError) as exc:
        run_node_bridge(
            "http://127.0.0.1:8765",
            "ANVIL_CONTROLLER_TOKEN",
            "0.17.0",
            replace_process=False,
            which=lambda _name: None,
        )

    assert exc.value.code == "node_runtime_missing"


def test_mcp_proxy_cli_delegates_to_packaged_bridge(monkeypatch):
    monkeypatch.setenv("ANVIL_CONTROLLER_TOKEN", "controller-secret")
    seen = {}

    def run(controller_url, auth_env, version):
        seen.update(
            controller_url=controller_url,
            auth_env=auth_env,
            version=version,
        )
        return 9

    monkeypatch.setattr(mcp, "_run_node_bridge", run)

    assert (
        mcp.main(
            [
                "--controller-url",
                "http://127.0.0.1:8765",
                "--auth-env",
                "ANVIL_CONTROLLER_TOKEN",
            ]
        )
        == 9
    )
    assert seen == {
        "controller_url": "http://127.0.0.1:8765",
        "auth_env": "ANVIL_CONTROLLER_TOKEN",
        "version": mcp.__version__,
    }


def test_packaged_bridge_is_catalog_driven_and_has_no_media_endpoint_or_token():
    asset = Path(mcp.__file__).parent / "_node" / "mcp_proxy.mjs"
    text = asset.read_text(encoding="utf-8")
    assert "for (const tool of listed.tools)" in text
    assert "Media generation accepts named workflows only." in text
    assert "ANVIL_MEDIA_BACKEND_URL" not in text
    assert "test-controller-token" not in text
