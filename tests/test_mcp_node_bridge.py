from __future__ import annotations

import os
from pathlib import Path

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.node_bridge import run_node_bridge
from anvil_serving.control_plane.mcp.stdio import parse_main_args


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


def test_packaged_bridge_launcher_reads_file_token_only_into_child_environment(
    tmp_path,
    monkeypatch,
):
    token = "file-controller-secret"
    auth_file = tmp_path / "controller.token"
    auth_file.write_text(token + "\n", encoding="utf-8")
    monkeypatch.delenv("ANVIL_MCP_CONTROLLER_TOKEN", raising=False)
    monkeypatch.setattr(
        "anvil_serving.control_plane.mcp.node_bridge.resolve_controller_token_file",
        lambda value: token if value == str(auth_file) else pytest.fail("wrong auth file"),
    )
    seen = {}

    def call(argv, *, env):
        seen["argv"] = argv
        seen["env"] = env
        return 8

    result = run_node_bridge(
        "http://127.0.0.1:8765",
        "",
        "0.17.0",
        auth_file=str(auth_file),
        replace_process=False,
        which=lambda _name: "/opt/homebrew/bin/node",
        call=call,
    )

    assert result == 8
    assert "file-controller-secret" not in "\0".join(seen["argv"])
    assert seen["argv"][seen["argv"].index("--auth-env") + 1] == "ANVIL_MCP_CONTROLLER_TOKEN"
    assert seen["env"]["ANVIL_MCP_CONTROLLER_TOKEN"] == token
    assert "ANVIL_MCP_CONTROLLER_TOKEN" not in os.environ


@pytest.mark.parametrize(
    "contents",
    ["", "must-not-leak\nsecond", b"\xff", "x" * 4097],
)
def test_packaged_bridge_launcher_rejects_malformed_auth_file(tmp_path, contents):
    auth_file = tmp_path / "controller.token"
    if isinstance(contents, bytes):
        auth_file.write_bytes(contents)
    else:
        auth_file.write_text(contents, encoding="utf-8")

    with pytest.raises(ToolError) as exc:
        run_node_bridge(
            "http://127.0.0.1:8765",
            "",
            "0.17.0",
            auth_file=str(auth_file),
            replace_process=False,
            which=lambda _name: "/opt/homebrew/bin/node",
        )

    assert exc.value.code == "bad_auth_file"
    assert "must-not-leak" not in exc.value.message


def test_mcp_proxy_arguments_preserve_env_mode_and_accept_auth_file():
    assert parse_main_args([]) == ("", "", "", False)
    assert parse_main_args(["--list-tools"]) == ("", "", "", True)
    assert parse_main_args(
        [
            "--controller-url",
            "http://127.0.0.1:8765",
            "--auth-env",
            "ANVIL_CONTROLLER_TOKEN",
        ]
    ) == (
        "http://127.0.0.1:8765",
        "ANVIL_CONTROLLER_TOKEN",
        "",
        False,
    )


def test_mcp_proxy_arguments_require_one_auth_source():
    with pytest.raises(SystemExit):
        parse_main_args(
            [
                "--controller-url",
                "http://127.0.0.1:8765",
                "--auth-env",
                "ANVIL_CONTROLLER_TOKEN",
                "--auth-file",
                "C:/private/controller.token",
            ]
        )
    with pytest.raises(SystemExit):
        parse_main_args(["--controller-url", "http://127.0.0.1:8765"])
    assert parse_main_args(
        [
            "--controller-url",
            "http://127.0.0.1:8765",
            "--auth-file",
            "C:/private/controller.token",
        ]
    ) == (
        "http://127.0.0.1:8765",
        "",
        "C:/private/controller.token",
        False,
    )


def test_mcp_cli_preserves_local_stdio_and_list_tools_modes(monkeypatch, capsys):
    called = []

    def serve():
        called.append(True)
        return 11

    monkeypatch.setattr(mcp, "serve_stdio", serve)

    assert mcp.main([]) == 11
    assert called == [True]
    assert mcp.main(["--list-tools"]) == 0
    assert '"tools"' in capsys.readouterr().out


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


def test_mcp_proxy_cli_delegates_file_without_exposing_token(tmp_path, monkeypatch, capsys):
    token = "file-controller-secret"
    auth_file = tmp_path / "controller.token"
    auth_file.write_text(token, encoding="utf-8")
    seen = {}

    def run(controller_url, auth_env, version, *, auth_file=""):
        seen.update(
            controller_url=controller_url,
            auth_env=auth_env,
            version=version,
            auth_file=auth_file,
        )
        return 10

    monkeypatch.setattr(mcp, "_run_node_bridge", run)
    monkeypatch.setattr(mcp, "resolve_controller_token_file", lambda value: token)

    assert mcp.main([
        "--controller-url", "http://127.0.0.1:8765", "--auth-file", str(auth_file)
    ]) == 10
    assert seen["auth_file"] == str(auth_file)
    assert seen["auth_env"] == ""
    assert token not in capsys.readouterr().err


def test_packaged_bridge_is_catalog_driven_and_has_no_media_endpoint_or_token():
    asset = Path(mcp.__file__).parent / "_node" / "mcp_proxy.mjs"
    text = asset.read_text(encoding="utf-8")
    assert "for (const tool of listed.tools)" in text
    assert "Media generation accepts named workflows only." in text
    assert "ANVIL_MEDIA_BACKEND_URL" not in text
    assert "test-controller-token" not in text
