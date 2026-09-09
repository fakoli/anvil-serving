from __future__ import annotations

import copy
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from anvil_serving.connect import config as connect_config
from anvil_serving.connect.config import ManifestError, parse_manifest_text, read_manifest, validate_manifest
from anvil_serving.connect.render import plan, render, stage


ROOT = Path(__file__).parents[2]
_LINUX_AMD64 = sys.platform == "linux" and platform.machine().lower() in {"x86_64", "amd64"}
_NATIVE = pytest.mark.skipif(not _LINUX_AMD64, reason="native Connect filesystem contract requires Linux amd64")


def manifest() -> dict:
    return json.loads((ROOT / "connect/examples/deployment.json").read_text())


def test_linux_target_paths_validate_independently_of_windows_runner_grammar() -> None:
    value = manifest()
    # Deployment paths name the Linux service target.  A Windows CI runner
    # must render that target without treating its POSIX paths as relative.
    assert not PureWindowsPath(value["binary"]).is_absolute()
    assert validate_manifest(value)["binary"] == value["binary"]


def test_manifest_text_parsing_is_portable_while_native_file_reads_fail_closed_without_hardened_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "deployment.json"
    source.write_text(json.dumps(manifest()), encoding="utf-8")
    for flag in ("O_CLOEXEC", "O_NONBLOCK", "O_NOFOLLOW"):
        monkeypatch.delattr(connect_config.os, flag, raising=False)
    assert parse_manifest_text(source.read_text(encoding="utf-8"))["schema"] == "anvil-connect.deployment/v1"
    with pytest.raises(ManifestError, match="secure manifest file reads"):
        read_manifest(source)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "not UTF-8"),
        (" " * (connect_config._MAX_MANIFEST_BYTES + 1), "bounded manifest read"),
        ("\ud800", "not UTF-8"),
    ],
    ids=["non-string", "oversized", "invalid-unicode"],
)
def test_manifest_text_parsing_rejects_non_utf8_and_oversized_input(value, message: str) -> None:
    with pytest.raises(ManifestError, match=message):
        parse_manifest_text(value)


def test_closed_shape_duplicate_case_and_null_are_rejected(tmp_path: Path) -> None:
    value = manifest()
    value["unexpected"] = True
    with pytest.raises(ManifestError, match="unknown keys"):
        validate_manifest(value)
    value = manifest()
    value["Gateway"] = value.pop("gateway")
    with pytest.raises(ManifestError, match="missing keys"):
        validate_manifest(value)
    value = manifest()
    value["gateway"]["tunnel_binary"] = None
    with pytest.raises(ManifestError, match="null"):
        validate_manifest(value)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"anvil-connect.deployment/v1","schema":"anvil-connect.deployment/v1"}')
    with pytest.raises(ManifestError, match="duplicate JSON key"):
        parse_manifest_text(duplicate.read_text(encoding="utf-8"))


def test_literal_credentials_and_unbound_bindings_are_rejected() -> None:
    value = manifest()
    value["authelia"]["client_secret"] = "do-not-inline"
    with pytest.raises(ManifestError, match="literal credentials"):
        validate_manifest(value)
    value = manifest()
    value["gateway"]["gateway"]["resources"][0]["tunnel_address"] = "127.0.0.1:17101"
    with pytest.raises(ManifestError, match="fixed connector binding"):
        validate_manifest(value)


def test_gateway_bindings_reject_native_control_and_tunnel_collisions() -> None:
    value = manifest()
    resource = value["gateway"]["gateway"]["resources"][0]
    connector_resource = value["connectors"][0]["resources"][0]
    resource["rule"]["host"] = value["gateway"]["control_host"]
    connector_resource["envelope"]["rule"]["host"] = value["gateway"]["control_host"]
    with pytest.raises(ManifestError, match="resource host may not equal control_host or tunnel_host"):
        validate_manifest(value)

    value = manifest()
    resource = value["gateway"]["gateway"]["resources"][0]
    connector_resource = value["connectors"][0]["resources"][0]
    resource["tunnel_address"] = value["gateway"]["tunnel_listen"]
    connector_resource["reverse_address"] = value["gateway"]["tunnel_listen"]
    with pytest.raises(ManifestError, match="resource tunnel_address may not equal tunnel_listen"):
        validate_manifest(value)

    value = manifest()
    value["connectors"][0]["http_proxy_url"] = "http://127.0.0.1:17070/"
    with pytest.raises(ManifestError, match="proxy URL without a path"):
        validate_manifest(value)
    value = manifest()
    value["gateway"]["state_directory"] = "//var/lib/anvil-connect/gateway"
    with pytest.raises(ManifestError, match="clean absolute path"):
        validate_manifest(value)
    value = manifest()
    del value["gateway"]["gateway"]["resources"][0]
    del value["connectors"][0]["resources"][0]
    with pytest.raises(ManifestError, match="requires at least one browser resource"):
        validate_manifest(value)


def test_render_is_stable_secret_free_and_matches_native_contract() -> None:
    first, second = render(manifest()), render(copy.deepcopy(manifest()))
    assert first == second
    assert first["generation"] == first["ownership"]["generation"]
    gateway = json.loads(first["files"]["gateway.json"])
    assert gateway["schema"] == "anvil-connect.gateway-runtime/v1"
    assert gateway["gateway"]["max_concurrent"] <= 512
    limits = gateway["gateway"]["resources"][0]["rule"]["limits"]
    assert set(limits) == {"request_bytes", "concurrent", "buffer_bytes", "idle_seconds", "duration_seconds"}
    assert 1 <= limits["request_bytes"] <= 64 * 1024 * 1024
    assert 4096 <= limits["buffer_bytes"] <= 256 * 1024
    assert limits["idle_seconds"] <= limits["duration_seconds"] <= 86400
    joined = "\n".join(first["files"].values())
    assert "do-not-inline" not in joined
    assert "ANVIL_CONNECT_OIDC_CLIENT_SECRET" in first["files"]["gateway.json"]
    assert "/etc/anvil-connect/secrets/oidc-client-secret-hash" in first["files"]["authelia/configuration.yml"]


def test_caddy_routes_keep_h2c_and_bound_websocket_path() -> None:
    document = json.loads(render(manifest())["files"]["caddy.json"])
    routes = document["apps"]["http"]["servers"]["anvil_connect"]["routes"]
    rendered = json.dumps(routes)
    assert "wstunnel" not in rendered
    tunnel = next(route for route in routes if route["match"][0].get("path") == ["/acv1/events"])
    assert tunnel["match"][0]["method"] == ["GET"]
    assert tunnel["handle"][1]["transport"]["versions"] == ["1.1"]
    upgrade_routes = [route for route in routes if "header_regexp" in route.get("match", [{}])[0]]
    assert len(upgrade_routes) == 1 + len(manifest()["gateway"]["gateway"]["resources"])
    # Exercise HTTP token semantics, rather than only snapshotting a pattern.
    import re
    for route in upgrade_routes:
        patterns = route["match"][0]["header_regexp"]
        for connection in ("upgrade", "Upgrade", "UPGRADE", "keep-alive, upgrade"):
            assert re.search(patterns["Connection"]["pattern"], connection)
        for connection in ("xupgrade", "upgrade-extra", "keep-alive"):
            assert not re.search(patterns["Connection"]["pattern"], connection)
        for upgrade in ("websocket", "WebSocket", "WEBSOCKET"):
            assert re.search(patterns["Upgrade"]["pattern"], upgrade)
    ordinary = next(route for route in routes if route["match"][0].get("method") == ["GET", "POST"])
    assert ordinary["handle"][1]["transport"]["versions"] == ["h2c"]
    assert "Forwarded" in ordinary["handle"][0]["request"]["delete"]
    assert ordinary["handle"][1]["upstreams"][0]["dial"] == "unix/" + manifest()["gateway"]["state_directory"] + "/ingress.sock"
    reserved = next(route for route in routes if route.get("match", [{}])[0].get("path") == ["/_anvil-connect/login", "/_anvil-connect/callback", "/_anvil-connect/logout"])
    assert reserved["handle"][1]["transport"]["versions"] == ["h2c"]
    assert routes[-1] == {"handle": [{"handler": "static_response", "status_code": 404}]}
    assert document["apps"]["http"]["servers"]["anvil_connect"]["automatic_https"] == {"disable_certificates": True}


def test_authelia_template_has_explicit_pkce_rs256_and_callbacks() -> None:
    text = render(manifest())["files"]["authelia/configuration.yml"]
    assert "require_pkce: true" in text
    assert "pkce_challenge_method: S256" in text
    assert "id_token_signed_response_alg: RS256" in text
    assert "- openid" in text
    assert "https://dash.example.test/_anvil-connect/callback" in text
    assert '{{- fileContent "/etc/anvil-connect/secrets/oidc-rs256-private-key" | nindent 10 }}' in text
    assert "identity_validation:" in text
    assert "notifier:" in text
    assert "default_policy: two_factor" in text


@_NATIVE
def test_plan_and_staging_preserve_drift_and_are_idempotent(tmp_path: Path) -> None:
    output = tmp_path / "rendered"
    output.mkdir()
    foreign = output / "keep.txt"
    foreign.write_text("operator owned")
    before = foreign.read_text()
    assert plan(manifest(), output)["state"] == "unmanaged"
    assert foreign.read_text() == before
    with pytest.raises(ManifestError, match="unmanaged"):
        stage(manifest(), output)

    output = tmp_path / "clean"
    first_stage = stage(manifest(), output)
    second_stage = stage(manifest(), output)
    assert first_stage == second_stage
    assert not output.exists()  # sibling staging never contaminates a deployment root
    assert (Path(first_stage["path"]) / "gateway.json").is_file()

    owned = tmp_path / "owned"
    owned.mkdir()
    rendered = render(manifest())["files"]
    for name, content in rendered.items():
        target = owned / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    assert plan(manifest(), owned)["state"] == "current"
    (owned / "gateway.json").write_text("tampered")
    report = plan(manifest(), owned)
    assert report["state"] == "drift"
    assert "gateway.json" in report["corrupt"]
    assert (owned / "gateway.json").read_text() == "tampered"


def test_systemd_units_have_real_argv_and_reject_unsafe_paths() -> None:
    files = render(manifest())["files"]
    assert "${CONFIG_ROOT}" not in "\n".join(files.values())
    assert "--adapter json" not in files["systemd/anvil-connect-caddy.service"]
    assert "--config.experimental.filters template" in files["systemd/anvil-connect-authelia.service"]
    assert "ExecStart=/opt/anvil-connect/components/caddy/2.11.3/caddy" in files["systemd/anvil-connect-caddy.service"]
    assert "ExecStart=/opt/anvil-connect/components/authelia/4.39.20/authelia" in files["systemd/anvil-connect-authelia.service"]
    assert "EnvironmentFile=/etc/anvil-connect/secrets/gateway.env" in files["systemd/anvil-connect-gateway.service"]
    assert "EnvironmentFile=/etc/anvil-connect/secrets/connectors/dashboard.env" in files["systemd/anvil-connect-connector-dashboard.service"]
    assert "EnvironmentFile=/etc/anvil-connect/secrets/clients/dashboard-api.env" in files["systemd/anvil-connect-client-dashboard-api.service"]
    value = manifest()
    value["config_root"] = "/etc/anvil connect"
    with pytest.raises(ManifestError, match="ExecStart argument"):
        render(value)


@_NATIVE
def test_plan_and_stage_reject_symlink_roots_markers_and_staging(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ManifestError, match="non-symlink"):
        plan(manifest(), root_link)

    output = tmp_path / "output"
    output.mkdir()
    (output / "managed.json").symlink_to(target / "marker")
    with pytest.raises(ManifestError, match="invalid ownership marker"):
        plan(manifest(), output)

    root = tmp_path / "clean"
    staging_link = tmp_path / ".clean.anvil-connect-staging"
    staging_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ManifestError, match="staging root"):
        stage(manifest(), root)


@_NATIVE
def test_rendered_native_runtime_json_passes_real_cli(tmp_path: Path) -> None:
    selected_go = os.environ.get("ANVIL_CONNECT_GO") or shutil.which("go")
    if not selected_go:
        pytest.skip("Anvil Connect Go toolchain is unavailable")
    binary = tmp_path / "anvil-connect-render-contract"
    subprocess.run(
        [selected_go, "-C", str(ROOT / "connect"), "build", "-o", str(binary), "./cmd/anvil-connect"],
        check=True, cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    files = render(manifest())["files"]
    paths = [("gateway", "gateway.json")]
    paths.extend(("connector", "connectors/" + item["id"] + ".json") for item in manifest()["connectors"])
    paths.extend(("client", "clients/" + item["rule"]["id"] + ".json") for item in manifest()["clients"])
    for mode, name in paths:
        config = tmp_path / name
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(files[name], encoding="utf-8")
        config.chmod(0o644)
        result = subprocess.run(
            [str(binary), "validate", "--mode", mode, "--config", str(config)],
            check=True, capture_output=True, text=True, timeout=10,
        )
        assert json.loads(result.stdout) == {"mode": mode, "status": "valid"}

    gateway = json.loads(files["gateway.json"])
    for name, mutate in {
        "control-host.json": lambda value: value["gateway"]["resources"][0]["rule"].__setitem__("host", value["control_host"]),
        "tunnel-listen.json": lambda value: value["gateway"]["resources"][0].__setitem__("tunnel_address", value["tunnel_listen"]),
    }.items():
        invalid = copy.deepcopy(gateway)
        mutate(invalid)
        config = tmp_path / name
        config.write_text(json.dumps(invalid), encoding="utf-8")
        config.chmod(0o644)
        result = subprocess.run(
            [str(binary), "validate", "--mode", "gateway", "--config", str(config)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0

    connector = json.loads(files["connectors/dashboard.json"])
    connector["http_proxy_url"] = "http://127.0.0.1:17070/"
    config = tmp_path / "connector-proxy-path.json"
    config.write_text(json.dumps(connector), encoding="utf-8")
    config.chmod(0o644)
    result = subprocess.run(
        [str(binary), "validate", "--mode", "connector", "--config", str(config)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0

    invalid = copy.deepcopy(gateway)
    invalid["state_directory"] = "//var/lib/anvil-connect/gateway"
    config = tmp_path / "gateway-double-slash.json"
    config.write_text(json.dumps(invalid), encoding="utf-8")
    config.chmod(0o644)
    result = subprocess.run(
        [str(binary), "validate", "--mode", "gateway", "--config", str(config)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


@_NATIVE
def test_plan_rejects_hostile_marker_without_reading_external_file(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    sentinel = tmp_path / "outside-sentinel"
    sentinel.write_text("must remain unread", encoding="utf-8")
    marker = {
        "schema": "anvil-connect.ownership/v1",
        "generation": "a" * 64,
        "files": {"../../outside-sentinel": "b" * 64},
    }
    (root / "managed.json").write_text(json.dumps(marker), encoding="utf-8")
    assert plan(manifest(), root)["state"] == "unmanaged"
    assert sentinel.read_text(encoding="utf-8") == "must remain unread"
    marker["files"] = {"/absolute": "b" * 64}
    (root / "managed.json").write_text(json.dumps(marker), encoding="utf-8")
    assert plan(manifest(), root)["state"] == "unmanaged"
    assert sentinel.read_text(encoding="utf-8") == "must remain unread"


def test_environment_file_bindings_and_client_rules_are_closed() -> None:
    value = manifest()
    value["environment_files"]["connectors"] = {}
    with pytest.raises(ManifestError, match="missing keys"):
        validate_manifest(value)
    value = manifest()
    value["clients"][0]["rule"]["path_prefix"] = "/wrong"
    with pytest.raises(ManifestError, match="exactly match a gateway API rule"):
        validate_manifest(value)
    value = manifest()
    value["caddy"]["service_name"] = "ignored-name"
    with pytest.raises(ManifestError, match="must equal anvil-connect-caddy"):
        validate_manifest(value)
