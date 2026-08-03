import hashlib
import json
import os

import pytest

from anvil_serving import cli, init, mcp, operator_config


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_inventory_classifies_files_without_returning_contents(tmp_path):
    _write(tmp_path / "operator-topology.toml", "schema_version = 1\n")
    _write(tmp_path / ".env", "TOKEN=do-not-return\n")
    _write(tmp_path / "controller-operations.sqlite3", "runtime\n")
    _write(tmp_path / "voice.toml.anvil.bak.1", "backup\n")
    _write(tmp_path / "cache" / "catalog.lock", "cache\n")
    _write(tmp_path / "notes.txt", "unknown\n")

    result = operator_config.inventory(str(tmp_path))

    by_path = {row["path"]: row for row in result["files"]}
    assert by_path["operator-topology.toml"]["classification"] == "versionable"
    assert by_path[".env"]["classification"] == "secret"
    assert by_path["controller-operations.sqlite3"]["classification"] == "runtime"
    assert by_path["voice.toml.anvil.bak.1"]["classification"] == "backup"
    assert by_path["cache/catalog.lock"]["classification"] == "cache"
    assert by_path["notes.txt"]["classification"] == "unknown"
    assert by_path["operator-topology.toml"]["sha256"] == hashlib.sha256(
        (tmp_path / "operator-topology.toml").read_bytes()
    ).hexdigest()
    assert all("content" not in row for row in result["files"])
    assert result["effective_home"] == str(tmp_path.resolve())
    assert result["installed_revisions"]["anvil_serving"]


def test_inventory_refuses_symlink(tmp_path):
    target = _write(tmp_path / "target.toml", "schema_version = 1\n")
    link = tmp_path / "linked.toml"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(operator_config.ConfigExportError, match="symlink"):
        operator_config.inventory(str(tmp_path))


def test_export_refuses_gateway_symlink_before_resolution(tmp_path):
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    target = _write(
        tmp_path.parent / "gateway-target" / "openclaw.json",
        json.dumps({"models": {"providers": {"anvil": {"baseUrl": "http://127.0.0.1:8000/v1"}}}}),
    )
    link = tmp_path.parent / f"{tmp_path.name}-gateway-link" / "openclaw.json"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(operator_config.ConfigExportError, match="gateway.*symlink"):
        operator_config.export(str(tmp_path), gateway_path=str(link))


def test_inventory_refuses_oversized_file(tmp_path):
    _write(tmp_path / "voice.toml", "x" * 32)
    with pytest.raises(operator_config.ConfigExportError, match="size limit"):
        operator_config.inventory(str(tmp_path), max_bytes=16)


def test_inventory_refuses_missing_and_outside_dependencies(tmp_path):
    _write(
        tmp_path / "serves.toml",
        'router_config = "{dir}/missing-router.toml"\n',
    )
    with pytest.raises(operator_config.ConfigExportError, match="unresolved dependency"):
        operator_config.inventory(str(tmp_path))

    outside = _write(tmp_path.parent / "outside-router.toml", "[router]\n")
    _write(tmp_path / "serves.toml", f'router_config = "{outside.as_posix()}"\n')
    with pytest.raises(operator_config.ConfigExportError, match="outside approved root"):
        operator_config.inventory(str(tmp_path))


def test_inventory_follows_compose_dependency_in_lifecycle_command(tmp_path):
    _write(
        tmp_path / "serves.toml",
        'up = "docker compose -f {dir}/voice-compose.yml up -d proxy"\n',
    )
    with pytest.raises(operator_config.ConfigExportError, match="unresolved dependency"):
        operator_config.inventory(str(tmp_path))

    _write(tmp_path / "voice-compose.yml", "services: {}\n")
    result = operator_config.inventory(str(tmp_path))
    assert result["dependency_edges"] == [
        {"source": "serves.toml", "target": "voice-compose.yml"}
    ]

def test_export_returns_safe_config_and_only_sanitized_anvil_gateway_fragment(tmp_path):
    _write(tmp_path / "router.toml", "[router]\n")
    _write(
        tmp_path / "serves.toml",
        'router_config = "{dir}/router.toml"\nauth_env = "ANVIL_ROUTER_TOKEN"\n',
    )
    _write(tmp_path / ".env", "ANVIL_ROUTER_TOKEN=never-return-this\n")
    gateway = {
        "models": {
            "providers": {
                "anvil": {
                    "baseUrl": "http://127.0.0.1:8000/v1",
                    "apiKey": "raw-secret",
                    "models": [{"id": "llm.primary"}],
                },
                "unrelated": {"apiKey": "other-secret"},
            }
        },
        "agents": {
            "defaults": {
                "models": {"anvil/llm.primary": {}, "unrelated/model": {}}
            }
        },
        "talk": {
            "realtime": {
                "provider": "anvil",
                "providers": {
                    "anvil": {
                        "realtimeUrl": "ws://127.0.0.1:30110/v1/realtime",
                        "apiKey": {"source": "env", "provider": "default", "id": "VOICE_KEY"},
                    }
                },
            }
        },
        "mcpServers": {
            "anvil-serving": {
                "command": "anvil-serving",
                "args": ["mcp", "serve"],
                "env": {"ANVIL_CONTROLLER_TOKEN": "raw-controller-secret"},
            },
            "unrelated": {"command": "other"},
        },
        "unrelated": {"private": "must-not-return"},
    }
    gateway_path = _write(
        tmp_path.parent / f"{tmp_path.name}-gateway" / "openclaw.json",
        json.dumps(gateway),
    )

    result = operator_config.export(str(tmp_path), gateway_path=str(gateway_path))

    files = {row["path"]: row for row in result["files"]}
    assert set(files) == {"router.toml", "serves.toml"}
    assert files["serves.toml"]["content"].splitlines()[-1] == (
        'auth_env = "ANVIL_ROUTER_TOKEN"'
    )
    rendered = json.dumps(result, sort_keys=True)
    assert "never-return-this" not in rendered
    assert "raw-secret" not in rendered
    assert "other-secret" not in rendered
    assert "raw-controller-secret" not in rendered
    assert "must-not-return" not in rendered
    assert result["gateway_fragment"]["models"]["providers"].keys() == {"anvil"}
    assert result["gateway_fragment"]["agents"]["defaults"]["models"].keys() == {
        "anvil/llm.primary"
    }
    assert result["gateway_fragment"]["mcpServers"].keys() == {"anvil-serving"}
    assert result["redaction_count"] == 2


def test_export_refuses_secret_literal_in_versionable_config(tmp_path):
    _write(tmp_path / "voice.toml", 'api_key = "raw-secret"\n')
    with pytest.raises(operator_config.ConfigExportError, match="secret-like field"):
        operator_config.export(str(tmp_path))


def test_export_refuses_secret_literals_in_env_example(tmp_path):
    _write(tmp_path / ".env.example", "API_TOKEN=raw-secret\n")
    with pytest.raises(operator_config.ConfigExportError, match="secret-like field"):
        operator_config.export(str(tmp_path))


@pytest.mark.parametrize(
    "content",
    [
        "broken: [\n",
        "router_config: router.toml\n",
    ],
)
def test_inventory_marks_yaml_unsupported_and_export_fails_closed(tmp_path, content):
    _write(tmp_path / "config.yaml", content)
    _write(tmp_path / "router.toml", "[router]\n")

    result = operator_config.inventory(str(tmp_path))
    row = next(row for row in result["files"] if row["path"] == "config.yaml")
    assert row["classification"] == "unsupported"
    assert row["parser"] == "yaml"
    assert row["dependencies"] == []

    with pytest.raises(operator_config.ConfigExportError, match="does not support YAML"):
        operator_config.export(str(tmp_path))


def test_selected_export_ignores_unselected_yaml_and_closes_dependencies(tmp_path):
    _write(tmp_path / "docker-compose.yml", "services: {}\n")
    _write(tmp_path / "router.toml", "[router]\n")
    _write(tmp_path / "serves.toml", 'router_config = "router.toml"\n')

    result = operator_config.export(str(tmp_path), paths=["serves.toml"])

    assert result["selected_paths"] == ["serves.toml"]
    assert result["dependency_complete"] is True
    assert [row["path"] for row in result["files"]] == [
        "router.toml",
        "serves.toml",
    ]
    assert result["excluded_counts"]["unsupported"] == 1


@pytest.mark.parametrize("path", ["docker-compose.yml", "../router.toml", ".env"])
def test_selected_export_refuses_unsupported_escaping_and_secret_paths(tmp_path, path):
    _write(tmp_path / "docker-compose.yml", "services: {}\n")
    _write(tmp_path / ".env", "TOKEN=secret\n")
    with pytest.raises(operator_config.ConfigExportError):
        operator_config.export(str(tmp_path), paths=[path])


def test_full_openclaw_document_inside_home_is_never_exported(tmp_path):
    _write(tmp_path / "openclaw.json", json.dumps({"unrelated": {"private": "value"}}))
    result = operator_config.export(str(tmp_path))
    assert result["files"] == []
    assert result["excluded_counts"]["secret"] == 1


def test_export_refuses_capability_bearing_url_in_versionable_config(tmp_path):
    _write(
        tmp_path / "voice.toml",
        'endpoint = "https://example.invalid/path?token=hidden"\n',
    )
    with pytest.raises(operator_config.ConfigExportError, match="capability-bearing URL"):
        operator_config.export(str(tmp_path))


def test_mcp_inventory_and_export_are_read_only_typed_tools(tmp_path, monkeypatch):
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    _write(tmp_path / "docker-compose.yml", "services: {}\n")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    monkeypatch.setattr(operator_config, "default_gateway_path", lambda: None)

    inventory = mcp.call_tool("operator_config_inventory", {})
    exported = mcp.call_tool("operator_config_export", {"paths": ["host.toml"]})

    assert inventory["ok"] is True
    inventory_by_path = {row["path"]: row for row in inventory["data"]["files"]}
    assert inventory_by_path["host.toml"]["classification"] == "versionable"
    assert inventory_by_path["docker-compose.yml"]["classification"] == "unsupported"
    assert exported["ok"] is True
    assert exported["data"]["files"][0]["content"].splitlines() == [
        "schema_version = 1"
    ]


def test_mcp_refuses_remote_filesystem_root_overrides():
    for tool, argument in (
        ("operator_config_inventory", {"home": "C:/other"}),
        ("operator_config_export", {"gateway_path": "C:/other/openclaw.json"}),
    ):
        result = mcp.call_tool(tool, argument)
        assert result["ok"] is False
        assert result["error"]["code"] == "bad_argument"


def test_local_cli_inventory_is_read_only_and_machine_parseable(tmp_path, capsys):
    _write(tmp_path / "operator-topology.toml", init.render_starter_topology())
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.iterdir()
    }

    rc = cli.main(
        [
            "host", "config", "inventory",
            "--home", str(tmp_path),
            "--topology", str(tmp_path / "operator-topology.toml"),
            "--transport", "local",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["command"].startswith("host config inventory ")
    assert json.loads(payload["data"])["schema"] == "operator-config-inventory/v1"
    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.iterdir()
    }
    assert after == before
