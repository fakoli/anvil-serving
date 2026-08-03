import hashlib
import json
import os

import pytest

from anvil_serving import cli, init, mcp, operator_config


_PEM_PRIVATE_KEY_TOML = (
    'privateKey = "-----BEGIN PRIVATE ' + 'KEY-----\\nreusable-secret"\n'
)
_PGP_PRIVATE_KEY_TOML = (
    'content = "-----BEGIN PGP PRIVATE ' + 'KEY BLOCK-----reusable-secret"\n'
)
_SSH2_PRIVATE_KEY_TOML = (
    'content = "---- BEGIN SSH2 ENCRYPTED PRIVATE ' + 'KEY ----reusable-secret"\n'
)
_PUTTY_PRIVATE_KEY = "PuTTY-User-" + "Key-File-3: ssh-rsa\nreusable-secret"
_PUTTY_PRIVATE_KEY_TOML = (
    'content = "PuTTY-User-' + 'Key-File-3: ssh-rsa\\nreusable-secret"\n'
)


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


def test_gateway_sanitizer_closes_structural_credential_bypasses(tmp_path):
    marker = "reusable-secret-marker"
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    gateway = {
        "models": {
            "providers": {
                "anvil": {
                    "baseUrl": "http://127.0.0.1:8000/v1",
                    "apiKey": {
                        "source": "env",
                        "provider": "default",
                        "id": marker,
                    },
                    "accessToken": marker,
                    "headerPairs": [["Cookie", f"session={marker}"]],
                    "headersList": [["X-Auth-Token", marker]],
                    "defaultHeaders": ["Cookie", f"session={marker}"],
                    "customHeaderPairs": [["X-Auth-Token", marker]],
                    "relativeCallback": f"/cb?sig={marker}",
                    "oauthRedirect": f"?code={marker}",
                    "stateCallback": f"/cb?state={marker}",
                    "ticketCallback": f"?ticket={marker}",
                    "pathSession": f"/cb;session={marker}",
                    "encodedSignature": f"?%73ig={marker}",
                    "encodedToken": f"?access_%74oken={marker}",
                    "fullyEncodedRelative": f"%2Fcb%3Ftoken%3D{marker}",
                    "fullyEncodedAbsolute": (
                        f"https%3A%2F%2Fexample.invalid%2Fcb%3Ftoken%3D{marker}"
                    ),
                    "keyMaterial": _PUTTY_PRIVATE_KEY.replace(
                        "reusable-secret", marker
                    ),
                    "badUrl": "//[",
                    "headerObjects": [
                        {"Name": "Cookie", "Values": [f"session={marker}"]},
                        {"headerName": "Cookie", "headerValue": marker},
                        {
                            "name": "Cookie",
                            "value": {
                                "source": "env",
                                "provider": "default",
                                "id": "COOKIE_REF",
                            },
                            "values": [marker],
                        },
                        {"name": "Cookie", "Name": "Cookie", "value": marker},
                        {
                            "name": "Cookie",
                            "key": "X-Auth-Token",
                            "value": marker,
                        },
                        {
                            "name": "Cookie",
                            "value": {
                                "source": "env",
                                "provider": "default",
                                "id": "COOKIE_REF",
                            },
                            "headerName": "X-Auth-Token",
                            "headerValue": marker,
                        },
                    ],
                }
            }
        },
        "mcpServers": {
            "anvil-serving": {
                "command": "anvil-serving",
                "args": [
                    f"--callback=https://example.invalid/cb?token={marker}",
                    f"Authorization: Bearer {marker}",
                    f"--header=Cookie: session={marker}",
                    f"-HCookie: session={marker}",
                    f"X-Auth-Token: {marker}",
                    f"//user:{marker}@example.invalid/path",
                    f"--dsn=postgresql://user:{marker}@example.invalid/database",
                    f"/cb?token={marker}",
                    f"?token={marker}",
                    f"https:opaque?token={marker}",
                ],
                "headers": [["Authorization", f"Bearer {marker}"]],
                "headerObjects": [
                    {"name": "Cookie", "value": f"session={marker}"},
                    {"key": "X-Api-Key", "value": marker},
                    {"Name": "Cookie", "Value": f"session={marker}"},
                ],
            }
        },
    }
    gateway_path = _write(
        tmp_path.parent / f"{tmp_path.name}-gateway-structural" / "openclaw.json",
        json.dumps(gateway),
    )

    result = operator_config.export(str(tmp_path), gateway_path=str(gateway_path))

    rendered = json.dumps(result, sort_keys=True)
    assert marker not in rendered
    assert "mcpServers" not in result["gateway_fragment"]
    assert result["redaction_count"] == 24


def test_export_refuses_secret_literal_in_versionable_config(tmp_path):
    _write(tmp_path / "voice.toml", 'api_key = "raw-secret"\n')
    with pytest.raises(operator_config.ConfigExportError, match="secret-like field"):
        operator_config.export(str(tmp_path))


@pytest.mark.parametrize(
    "content",
    [
        '[headers]\nAuthorization = "Bearer reusable-secret"\n',
        '[headers]\nCookie = "session=reusable-secret"\n',
        '[headers]\nproxy-authorization = "Basic reusable-secret"\n',
        '[headers]\nset-cookie = "session=reusable-secret"\n',
    ],
)
def test_export_refuses_http_credential_literals(tmp_path, content):
    _write(tmp_path / "router.toml", content)

    with pytest.raises(operator_config.ConfigExportError, match="secret-like field"):
        operator_config.export(str(tmp_path))


def test_export_accepts_http_credential_secret_reference(tmp_path):
    _write(
        tmp_path / "router.toml",
        "[headers.Authorization]\n"
        'source = "env"\n'
        'provider = "default"\n'
        'id = "ROUTER_AUTHORIZATION"\n',
    )

    result = operator_config.export(str(tmp_path))

    assert result["files"][0]["content"]


@pytest.mark.parametrize(
    "content",
    [
        'accessToken = "reusable-secret"\n',
        'headers = [["Authorization", "Bearer reusable-secret"]]\n',
        'headers = ["Cookie", "session=reusable-secret"]\n',
        'headerPairs = [["Cookie", "session=reusable-secret"]]\n',
        'headersList = [["X-Auth-Token", "reusable-secret"]]\n',
        'defaultHeaders = [["Cookie", "session=reusable-secret"]]\n',
        'customHeaderPairs = [["X-Auth-Token", "reusable-secret"]]\n',
        _PEM_PRIVATE_KEY_TOML,
        'args = ["--header=Authorization: Bearer reusable-secret"]\n',
        'args = ["Cookie: session=reusable-secret"]\n',
        'args = ["--cookie session=reusable-secret"]\n',
        'args = ["-HCookie: session=reusable-secret"]\n',
        'args = ["X-Auth-Token: reusable-secret"]\n',
        'args = ["--callback=https://example.invalid/cb?token=reusable-secret"]\n',
        'args = ["//user:reusable-secret@example.invalid/path"]\n',
        'args = ["/cb?token=reusable-secret"]\n',
        'args = ["?token=reusable-secret"]\n',
        'args = ["https:opaque?token=reusable-secret"]\n',
        'args = ["/cb?sig=reusable-secret"]\n',
        'args = ["?code=reusable-secret"]\n',
        'args = ["/cb?state=reusable-secret"]\n',
        'args = ["?ticket=reusable-secret"]\n',
        'args = ["/cb;session=reusable-secret"]\n',
        'args = ["?%73ig=reusable-secret"]\n',
        'args = ["?access_%74oken=reusable-secret"]\n',
        'args = ["%2Fcb%3Ftoken%3Dreusable-secret"]\n',
        'args = ["https%3A%2F%2Fexample.invalid%2Fcb%3Ftoken%3Dreusable-secret"]\n',
        'args = ["//["]\n',
        'headerObjects = [{name = "Cookie", value = "session=reusable-secret"}]\n',
        'headerObjects = [{key = "X-Api-Key", value = "reusable-secret"}]\n',
        'headerObjects = [{Name = "Cookie", Value = "session=reusable-secret"}]\n',
        'headerObjects = [{Name = "Cookie", Values = ["session=reusable-secret"]}]\n',
        'headerObjects = [{headerName = "Cookie", headerValue = "reusable-secret"}]\n',
        'headerObjects = [{name = "Cookie", value = {source = "env", provider = "default", id = "COOKIE_REF"}, values = ["reusable-secret"]}]\n',
        'headerObjects = [{name = "Cookie", Name = "Cookie", value = "reusable-secret"}]\n',
        'headerObjects = [{name = "Cookie", key = "X-Auth-Token", value = "reusable-secret"}]\n',
        'headerObjects = [{name = "Cookie", value = {source = "env", provider = "default", id = "COOKIE_REF"}, headerName = "X-Auth-Token", headerValue = "reusable-secret"}]\n',
        _PGP_PRIVATE_KEY_TOML,
        _SSH2_PRIVATE_KEY_TOML,
        _PUTTY_PRIVATE_KEY_TOML,
    ],
)
def test_export_refuses_alternate_credential_shapes(tmp_path, content):
    _write(tmp_path / "router.toml", content)

    with pytest.raises(operator_config.ConfigExportError):
        operator_config.export(str(tmp_path))


def test_export_refuses_invalid_secret_reference(tmp_path):
    _write(
        tmp_path / "router.toml",
        "[headers.Authorization]\n"
        'source = "env"\n'
        'provider = "default"\n'
        'id = "literal reusable secret"\n',
    )

    with pytest.raises(operator_config.ConfigExportError, match="invalid SecretRef"):
        operator_config.export(str(tmp_path))


def test_export_accepts_header_pair_secret_reference(tmp_path):
    _write(
        tmp_path / "router.toml",
        "headers = [[\"Authorization\", "
        '{source = "env", provider = "default", id = "ROUTER_AUTHORIZATION"}]]\n',
    )

    result = operator_config.export(str(tmp_path))

    assert result["files"][0]["content"]


def test_export_preserves_benign_pairs_and_descriptions(tmp_path):
    _write(
        tmp_path / "router.toml",
        'description = "Supports basic authentication mode; Cookie policy is disabled"\n'
        'dimensions = [["token", "count"]]\n'
        'tokenizer = "Qwen/Qwen3"\n'
        'tokenizer_id = "cl100k_base"\n'
        'keyboard = "us"\n'
        'monkey = "banana"\n'
        'auth_mode = "auth=none"\n'
        'session_mode = "session=default"\n'
        'code_mode = "code=python"\n',
    )

    result = operator_config.export(str(tmp_path))

    assert "Supports basic authentication mode" in result["files"][0]["content"]
    assert 'dimensions = [["token", "count"]]' in result["files"][0]["content"]


@pytest.mark.parametrize("reference_id", ["X", "AB", "_TOKEN"])
def test_export_accepts_product_valid_env_secret_reference(tmp_path, reference_id):
    _write(
        tmp_path / "router.toml",
        "[headers.Authorization]\n"
        'source = "env"\n'
        'provider = "default"\n'
        f'id = "{reference_id}"\n',
    )

    result = operator_config.export(str(tmp_path))

    assert result["files"][0]["content"]


def test_export_accepts_bounded_file_secret_reference(tmp_path):
    _write(
        tmp_path / "router.toml",
        "[headers.Authorization]\n"
        'source = "file"\n'
        'provider = "default"\n'
        'id = "/gateway/authToken"\n',
    )

    result = operator_config.export(str(tmp_path))

    assert result["files"][0]["content"]


def test_cookie_and_arbitrary_json_are_not_versionable(tmp_path):
    marker = "reusable-secret-cookie"
    _write(
        tmp_path / "cookies.json",
        json.dumps([{"name": "session", "value": marker}]),
    )
    _write(tmp_path / "arbitrary.json", json.dumps({"value": marker}))

    inventory = operator_config.inventory(str(tmp_path))
    by_path = {row["path"]: row for row in inventory["files"]}
    assert by_path["cookies.json"]["classification"] == "secret"
    assert by_path["arbitrary.json"]["classification"] == "unknown"

    result = operator_config.export(str(tmp_path))
    assert marker not in json.dumps(result)


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
