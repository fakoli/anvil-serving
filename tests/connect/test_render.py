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
from anvil_serving.connect.render import plan, plan_for_inspection, render, render_for_inspection, stage


ROOT = Path(__file__).parents[2]
_LINUX_AMD64 = sys.platform == "linux" and platform.machine().lower() in {"x86_64", "amd64"}
_NATIVE = pytest.mark.skipif(not _LINUX_AMD64, reason="native Connect filesystem contract requires Linux amd64")


def manifest() -> dict:
    return json.loads((ROOT / "connect/examples/deployment.json").read_text())


def isolated_manifest() -> dict:
    value = manifest()
    del value["service_user"]
    value["service_identities"] = {
        "gateway": {"uid": 1201, "gid": 1201},
        "edge": {"uid": 1202, "gid": 1202},
        "idp": {"uid": 1203, "gid": 1203},
        "connectors": {"dashboard": {"uid": 1204, "gid": 1204}},
        "clients": {"dashboard-api": {"uid": 1205, "gid": 1205}},
        "ingress": {"group_id": 1290, "directory": "/run/anvil-connect/ingress"},
    }
    value["service_limits"] = {
        "gateway": {"memory_max_bytes": 805306368, "tasks_max": 128},
        "edge": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "idp": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "connectors": {"dashboard": {"memory_max_bytes": 402653184, "tasks_max": 64}},
        "clients": {"dashboard-api": {"memory_max_bytes": 268435456, "tasks_max": 32}},
    }
    value["caddy"]["state_directory"] = "/var/lib/anvil-connect/caddy"
    return value


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
    first, second = render(isolated_manifest()), render(copy.deepcopy(isolated_manifest()))
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


def test_legacy_generation_is_inspection_only() -> None:
    legacy = manifest()
    inspected = render_for_inspection(legacy)
    assert inspected == render_for_inspection(copy.deepcopy(legacy))
    assert "User=anvil-connect" in inspected["files"]["systemd/anvil-connect-gateway.service"]
    with pytest.raises(ManifestError, match="isolated service identities"):
        render(legacy)


def test_legacy_inspection_plan_remains_read_only(tmp_path: Path) -> None:
    legacy = manifest()
    assert plan_for_inspection(legacy, tmp_path / "missing")["state"] == "absent"
    with pytest.raises(ManifestError, match="isolated service identities"):
        plan(legacy, tmp_path / "missing")


def test_caddy_routes_keep_h2c_and_bound_websocket_path() -> None:
    document = json.loads(render(isolated_manifest())["files"]["caddy.json"])
    routes = document["apps"]["http"]["servers"]["anvil_connect"]["routes"]
    rendered = json.dumps(routes)
    assert "wstunnel" not in rendered
    tunnel = next(route for route in routes if route["match"][0].get("path") == ["/acv1/events"])
    assert tunnel["match"][0]["method"] == ["GET"]
    assert tunnel["handle"][1]["transport"]["versions"] == ["1.1"]
    upgrade_routes = [route for route in routes if "header_regexp" in route.get("match", [{}])[0]]
    assert len(upgrade_routes) == 1 + len(isolated_manifest()["gateway"]["gateway"]["resources"])
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
    assert ordinary["handle"][1]["upstreams"][0]["dial"] == "unix/" + isolated_manifest()["service_identities"]["ingress"]["directory"] + "/ingress.sock"
    reserved = next(route for route in routes if route.get("match", [{}])[0].get("path") == ["/_anvil-connect/login", "/_anvil-connect/callback", "/_anvil-connect/logout"])
    assert reserved["handle"][1]["transport"]["versions"] == ["h2c"]
    assert routes[-1] == {"handle": [{"handler": "static_response", "status_code": 404}]}
    assert document["apps"]["http"]["servers"]["anvil_connect"]["automatic_https"] == {"disable_certificates": True}


def origin_proxy_manifest() -> dict:
    value = isolated_manifest()
    browser = value["gateway"]["gateway"]["resources"][0]["rule"]
    envelope = value["connectors"][0]["resources"][0]["envelope"]
    browser["path_prefix"] = envelope["rule"]["path_prefix"] = "/"
    envelope["origin_url"] = "http://127.0.0.1:18768"
    value["caddy"]["origin_proxy"] = {
        "listen": "127.0.0.1:18768",
        "routes": [
            {"path_prefix": "/", "origin_url": "http://127.0.0.1:8768", "preserve_identity": False},
            {"path_prefix": "/grafana", "origin_url": "http://127.0.0.1:3000", "preserve_identity": False},
        ],
    }
    return value


def test_caddy_origin_proxy_routes_longest_prefix_first_and_strips_identity_by_default() -> None:
    value = origin_proxy_manifest()
    before = render(isolated_manifest())
    rendered = render(value)
    document = json.loads(rendered["files"]["caddy.json"])
    proxy = document["apps"]["http"]["servers"]["anvil_connect_origin_proxy"]
    assert proxy["listen"] == ["127.0.0.1:18768"]
    assert [route["match"][0]["path"] for route in proxy["routes"]] == [["/grafana", "/grafana/*"], ["/", "/*"]]
    assert [route["handle"][0]["upstreams"][0]["dial"] for route in proxy["routes"]] == ["127.0.0.1:3000", "127.0.0.1:8768"]
    assert all(route["handle"] == [route["handle"][0]] for route in proxy["routes"])
    assert all(route["handle"][0]["headers"]["request"]["delete"] == ["X-Anvil-Connect-Identity"] for route in proxy["routes"])
    assert rendered["generation"] != before["generation"]
    assert {name for name in rendered["files"] if rendered["files"][name] != before["files"][name]} == {
        "caddy.json", "connectors/dashboard.json", "managed.json",
    }


def test_caddy_origin_proxy_is_closed_bound_and_loopback_only() -> None:
    base = origin_proxy_manifest()
    assert validate_manifest(base)["caddy"]["origin_proxy"]["routes"][0]["path_prefix"] == "/grafana"
    invalid = [
        (lambda value: value["caddy"]["origin_proxy"]["routes"].pop(0), "fallback"),
        (lambda value: value["caddy"]["origin_proxy"]["routes"].append({"path_prefix": "/grafana", "origin_url": "http://127.0.0.1:3001", "preserve_identity": False}), "duplicate"),
        (lambda value: value["caddy"]["origin_proxy"]["routes"].__setitem__(0, {"path_prefix": "/grafana/", "origin_url": "http://127.0.0.1:3000", "preserve_identity": False}), "canonical path prefix"),
        (lambda value: value["caddy"]["origin_proxy"].__setitem__("listen", "127.0.0.1:443"), "native listener"),
        (lambda value: value["caddy"]["origin_proxy"]["routes"].__setitem__(0, {"path_prefix": "/grafana", "origin_url": "http://127.0.0.1:18768", "preserve_identity": False}), "origin proxy listener"),
        (lambda value: value["caddy"]["origin_proxy"]["routes"].__setitem__(1, {"path_prefix": "/grafana", "origin_url": "http://127.0.0.1:17080", "preserve_identity": False}), "managed native listener"),
        (lambda value: value["caddy"]["origin_proxy"]["routes"][0].__setitem__("preserve_identity", True), "only the signed-identity root"),
    ]
    for mutate, message in invalid:
        value = copy.deepcopy(base)
        mutate(value)
        with pytest.raises(ManifestError, match=message):
            validate_manifest(value)
    value = copy.deepcopy(base)
    value["caddy"]["origin_proxy"]["extra"] = True
    with pytest.raises(ManifestError, match="unknown keys"):
        validate_manifest(value)
    value = copy.deepcopy(base)
    value["connectors"][0]["resources"][0]["envelope"]["origin_url"] = "http://127.0.0.1:8768"
    with pytest.raises(ManifestError, match="root browser resource"):
        validate_manifest(value)


def test_origin_proxy_limits_signed_identity_to_the_native_root_receiver() -> None:
    value = origin_proxy_manifest()
    resource = value["gateway"]["gateway"]["resources"][0]
    resource["rule"]["native_auth"] = "signed-identity"
    resource.update(identity_key_env="ANVIL_DASHBOARD_IDENTITY_KEY", identity_key_id="dashboard-v1")
    value["connectors"][0]["resources"][0]["envelope"]["rule"]["native_auth"] = "signed-identity"
    value["environment_files"]["gateway_identity"] = "/etc/anvil-connect/identity/gateway.env"
    value["caddy"]["origin_proxy"]["routes"][0]["preserve_identity"] = True
    routes = json.loads(render(value)["files"]["caddy.json"])["apps"]["http"]["servers"]["anvil_connect_origin_proxy"]["routes"]
    assert routes[0]["handle"][0]["headers"]["request"]["delete"] == ["X-Anvil-Connect-Identity"]
    assert "headers" not in routes[1]["handle"][0]
    value["caddy"]["origin_proxy"]["routes"][1]["preserve_identity"] = True
    with pytest.raises(ManifestError, match="only the signed-identity root"):
        validate_manifest(value)


def test_authelia_template_has_explicit_pkce_rs256_and_callbacks() -> None:
    text = render(isolated_manifest())["files"]["authelia/configuration.yml"]
    assert "require_pkce: true" in text
    assert "pkce_challenge_method: S256" in text
    assert "id_token_signed_response_alg: RS256" in text
    assert "- openid" in text
    assert "https://dash.example.test/_anvil-connect/callback" in text
    assert '{{- fileContent "/etc/anvil-connect/secrets/oidc-rs256-private-key" | nindent 10 }}' in text
    assert "identity_validation:" in text
    assert "notifier:" in text
    assert "default_policy: two_factor" in text


def test_authelia_additional_oidc_client_is_fixed_profile_with_protected_secret_ref() -> None:
    value = isolated_manifest()
    value["authelia"]["additional_oidc_clients"] = [{
        "client_id": "existing-integration", "client_name": "Existing Integration",
        "client_secret_file": "/etc/anvil-connect/secrets/existing-integration-oidc-client-secret",
        "redirect_uris": ["https://app.example.test/oidc/callback"],
    }]
    text = render(value)["files"]["authelia/configuration.yml"]
    assert "client_id: 'existing-integration'" in text
    assert "client_name: 'Existing Integration'" in text
    assert '{{- fileContent "/etc/anvil-connect/secrets/existing-integration-oidc-client-secret" | nindent 10 }}' in text
    assert "consent_mode: implicit" in text
    assert "token_endpoint_auth_method: client_secret_basic" in text
    assert "token_endpoint_auth_method: client_secret_post" in text
    assert "          - openid\n          - email\n          - profile" in text
    assert "https://app.example.test/oidc/callback" in text

    for redirect in ("http://app.example.test/callback", "https://app.example.test:443/callback",
                     "https://app.example.test/callback?next=/", "https://APP.example.test/callback"):
        invalid = copy.deepcopy(value)
        invalid["authelia"]["additional_oidc_clients"][0]["redirect_uris"] = [redirect]
        with pytest.raises(ManifestError, match="canonical HTTPS redirect URI"):
            validate_manifest(invalid)
    invalid = copy.deepcopy(value)
    invalid["authelia"]["additional_oidc_clients"][0]["client_id"] = invalid["gateway"]["oidc"]["client_id"]
    with pytest.raises(ManifestError, match="unique including the Connect client"):
        validate_manifest(invalid)
    invalid = copy.deepcopy(value)
    invalid["authelia"]["additional_oidc_clients"][0]["client_secret_file"] = invalid["authelia"]["client_secret_file"]
    with pytest.raises(ManifestError, match="distinct protected client secret files"):
        validate_manifest(invalid)
    invalid = copy.deepcopy(value)
    invalid["authelia"]["additional_oidc_clients"][0]["client_secret_file"] = invalid["authelia"]["state_directory"] + "/client-secret"
    with pytest.raises(ManifestError, match="outside rendered output and Authelia state"):
        validate_manifest(invalid)


@_NATIVE
def test_plan_and_staging_preserve_drift_and_are_idempotent(tmp_path: Path) -> None:
    output = tmp_path / "rendered"
    output.mkdir()
    foreign = output / "keep.txt"
    foreign.write_text("operator owned")
    before = foreign.read_text()
    assert plan(isolated_manifest(), output)["state"] == "unmanaged"
    assert foreign.read_text() == before
    with pytest.raises(ManifestError, match="unmanaged"):
        stage(isolated_manifest(), output)

    output = tmp_path / "clean"
    first_stage = stage(isolated_manifest(), output)
    second_stage = stage(isolated_manifest(), output)
    assert first_stage == second_stage
    assert not output.exists()  # sibling staging never contaminates a deployment root
    assert (Path(first_stage["path"]) / "gateway.json").is_file()

    owned = tmp_path / "owned"
    owned.mkdir()
    rendered = render(isolated_manifest())["files"]
    for name, content in rendered.items():
        target = owned / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    assert plan(isolated_manifest(), owned)["state"] == "current"
    (owned / "gateway.json").write_text("tampered")
    report = plan(isolated_manifest(), owned)
    assert report["state"] == "drift"
    assert "gateway.json" in report["corrupt"]
    assert (owned / "gateway.json").read_text() == "tampered"


def test_systemd_units_have_real_argv_and_reject_unsafe_paths() -> None:
    files = render(isolated_manifest())["files"]
    caddy = json.loads(files["caddy.json"])
    assert "${CONFIG_ROOT}" not in "\n".join(files.values())
    assert "--adapter json" not in files["systemd/anvil-connect-caddy.service"]
    assert "--config.experimental.filters template" in files["systemd/anvil-connect-authelia.service"]
    assert "ExecStart=/opt/anvil-connect/components/caddy/2.11.3/caddy" in files["systemd/anvil-connect-caddy.service"]
    assert "ExecStart=/opt/anvil-connect/components/authelia/4.39.20/authelia" in files["systemd/anvil-connect-authelia.service"]
    assert "EnvironmentFile=/etc/anvil-connect/secrets/gateway.env" in files["systemd/anvil-connect-gateway.service"]
    assert "EnvironmentFile=/etc/anvil-connect/secrets/connectors/dashboard.env" in files["systemd/anvil-connect-connector-dashboard.service"]
    assert "EnvironmentFile=/etc/anvil-connect/secrets/clients/dashboard-api.env" in files["systemd/anvil-connect-client-dashboard-api.service"]
    assert caddy["apps"]["http"]["grace_period"] == "15s"
    assert "TimeoutStopSec=20" in files["systemd/anvil-connect-caddy.service"]
    value = isolated_manifest()
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
        plan(isolated_manifest(), root_link)

    output = tmp_path / "output"
    output.mkdir()
    (output / "managed.json").symlink_to(target / "marker")
    with pytest.raises(ManifestError, match="invalid ownership marker"):
        plan(isolated_manifest(), output)

    root = tmp_path / "clean"
    staging_link = tmp_path / ".clean.anvil-connect-staging"
    staging_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ManifestError, match="staging root"):
        stage(isolated_manifest(), root)


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
    files = render(isolated_manifest())["files"]
    paths = [("gateway", "gateway.json")]
    paths.extend(("connector", "connectors/" + item["id"] + ".json") for item in isolated_manifest()["connectors"])
    paths.extend(("client", "clients/" + item["rule"]["id"] + ".json") for item in isolated_manifest()["clients"])
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
    assert plan(isolated_manifest(), root)["state"] == "unmanaged"
    assert sentinel.read_text(encoding="utf-8") == "must remain unread"
    marker["files"] = {"/absolute": "b" * 64}
    (root / "managed.json").write_text(json.dumps(marker), encoding="utf-8")
    assert plan(isolated_manifest(), root)["state"] == "unmanaged"
    assert sentinel.read_text(encoding="utf-8") == "must remain unread"


def test_environment_file_bindings_and_client_rules_are_closed() -> None:
    value = isolated_manifest()
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


def test_loopback_edge_keeps_tls_without_implicit_public_listeners() -> None:
    value = isolated_manifest()
    value['caddy']['listen'] = '127.0.0.1:19443'
    value['caddy']['tls'] = {'mode': 'provided', 'certificate_file': '/etc/connect/tls.pem', 'key_file': '/etc/connect/tls.key'}
    generated = render(value)
    edge = json.loads(generated['files']['caddy.json'])['apps']['http']['servers']['anvil_connect']
    assert edge['listen'] == ['127.0.0.1:19443']
    assert edge['tls_connection_policies'] == [{}]
    assert edge['automatic_https'] == {'disable_certificates': True, 'disable_redirects': True}
    assert 'CAP_NET_BIND_SERVICE' not in generated['files']['systemd/anvil-connect-caddy.service']
    for bad in ('0.0.0.0:19443', 'localhost:19443', ':19443', value['authelia']['listen'], value['gateway']['tunnel_listen']):
        value['caddy']['listen'] = bad
        with pytest.raises(ManifestError):
            validate_manifest(value)
    value['caddy']['listen'] = '127.0.0.1:19443'
    value['caddy']['tls'] = {'mode': 'acme', 'certificate_file': '', 'key_file': ''}
    with pytest.raises(ManifestError, match='provided TLS'):
        validate_manifest(value)


def local_tunnel_manifest(value: dict | None = None) -> dict:
    value = isolated_manifest() if value is None else copy.deepcopy(value)
    value["gateway"]["local_tunnel"] = {
        "listen": "127.0.0.1:18443", "server_name": "local-tls.example.test",
        "http_host": "local-http.example.test", "certificate_file": "/etc/connect-local/leaf.pem",
        "private_key_file": "/etc/connect-local/leaf.key", "trust_file": "/etc/connect-local/root.pem",
    }
    value["connectors"][0]["local_tunnel"] = {
        "address": "127.0.0.1:18443", "server_name": "local-tls.example.test",
        "http_host": "local-http.example.test", "trust_file": "/etc/connector-local/root.pem",
    }
    return value


def test_local_tunnel_omission_preserves_pre_slice_bytes() -> None:
    import hashlib

    # Captured from the supported isolated fixture before Slice 1 changed either reader.
    expected = {
        "authelia/configuration.yml": "6fd41c28c45c359715c3376df165fc2b74175df7433ccea42ec1e940ec766f43",
        "caddy.json": "c3810b3ba1096c911f9983d5999f8d8436d1fee169ae262a99704b74c383bec8",
        "clients/dashboard-api.json": "797df756cc7391a94bf230a3a5e8ba84545c5bc7a8a31e099218f5834f15a672",
        "connectors/dashboard.json": "2d6b1299fe15ea09c10a2438c035a15413ab78df31b39c379c0d25bede593716",
        "gateway.json": "9eba949a2b1679b1f7cfb8907116635b851da9f32fc48a6c62a4b0b37034c56b",
        "systemd/anvil-connect-authelia.service": "c5beb1942112e17a3d872e6ce2bb6ee15195f6f0bf3d6eac5f5be6cb6a5383ab",
        "systemd/anvil-connect-caddy.service": "9ac3db1502ec08f5e7205aa6a5156a933c4e1dc96d1ecfc2ecb5e4b9202069e5",
        "systemd/anvil-connect-client-dashboard-api.service": "213a3fd28ca1ce71e1288ddb249021763a4a08149b05b6eea2ec447d6dfbdacb",
        "systemd/anvil-connect-connector-dashboard.service": "c7d6da3ae0e75e203ae3dd6f3f7bce042d895e00351019aceb92f36d693b4f13",
        "systemd/anvil-connect-gateway.service": "d41891ef4907ca24a554827f6de463c2b5714269ef946bbeb0f437f44c381214",
        "managed.json": "40b11470c384731d629a59edc4b1f71507c876d13e7ff5922abab0f02c93ce0f",
    }
    value = isolated_manifest()
    normalized = connect_config.canonical_manifest(value)
    generation = "e9b86d9f9ad8acb44d686138a714f43ad610bb0be372fa80e268a4882d817b81"
    assert hashlib.sha256(normalized).hexdigest() == generation
    assert b"local_tunnel" not in normalized
    result = render(value)
    assert result["generation"] == generation
    assert {name: hashlib.sha256(content.encode()).hexdigest() for name, content in result["files"].items()} == expected
    assert result["ownership"]["files"] == {name: digest for name, digest in expected.items() if name != "managed.json"}
    assert render(validate_manifest(value)) == result


def test_local_tunnel_exact_projection_and_staged_listener() -> None:
    original = render(isolated_manifest())
    value = local_tunnel_manifest()
    result = render(value)
    assert json.loads(result["files"]["gateway.json"])["local_tunnel"] == value["gateway"]["local_tunnel"]
    connector = json.loads(result["files"]["connectors/dashboard.json"])
    assert connector["local_tunnel"] == value["connectors"][0]["local_tunnel"]
    assert connector["http_proxy_url"] == value["connectors"][0]["http_proxy_url"]
    assert {name for name in result["files"] if result["files"][name] != original["files"][name]} == {
        "gateway.json", "connectors/dashboard.json", "managed.json", "systemd/anvil-connect-connector-dashboard.service",
    }
    unit = result["files"]["systemd/anvil-connect-connector-dashboard.service"]
    assert "After=network-online.target anvil-connect-gateway.service" in unit
    assert "Wants=network-online.target anvil-connect-gateway.service" in unit
    assert all(directive not in unit for directive in ("Requires=", "BindsTo=", "PartOf="))
    assert value["gateway"]["local_tunnel"]["private_key_file"] not in result["files"]["connectors/dashboard.json"]
    assert result["generation"] != original["generation"]
    assert validate_manifest(validate_manifest(value)) == validate_manifest(value)
    del value["connectors"][0]["local_tunnel"]
    staged = render(value)
    assert staged["files"]["connectors/dashboard.json"] == original["files"]["connectors/dashboard.json"]
    del value["gateway"]["local_tunnel"]
    assert render(value) == original


def _invalid_local_objects(local: dict) -> list[tuple[str, str]]:
    # One raw corpus exercises both Python and the actual Go readers, including
    # duplicate keys that cannot be represented by a Python dict.
    cases = [(raw, "") for raw in ('null', '{}', '[]', '""', 'false', '1')]
    raw = json.dumps(local)
    for key in local:
        for value in (None, "", False, 1):
            cases.append((json.dumps({**local, key: value}), ""))
        cases.append((json.dumps({k: v for k, v in local.items() if k != key}), ""))
        cases.append((raw.replace('"' + key + '":', '"' + key.upper() + '":'), ""))
        cases.append((raw.replace('"' + key + '":', '"' + key + '": "duplicate", "' + key + '":'), ""))
    for key in ("proxy", "http_proxy_url", "enabled", "path", "unknown"):
        cases.append((json.dumps({**local, key: "forbidden"}), ""))
    address = "listen" if "listen" in local else "address"
    for value in ("localhost:443", "0.0.0.0:443", "127.0.0.2:443", "[::1]:443", "127.1:443", "127.0.0.1:0",
                  "127.0.0.1:65536", "127.0.0.1:0443", "127.0.0.1:+443", "https://127.0.0.1:443",
                  "user@127.0.0.1:443", "127.0.0.1:443?q", "127.0.0.1:443#f"):
        cases.append((json.dumps({**local, address: value}), "127.0.0.1 TCP address"))
    for key in ("server_name", "http_host"):
        for value in ("LOCAL.example.test", "localhost", "127.0.0.1", "bad..example.test", "*.example.test",
                      "local.example.test.", "local.example.test:443", "https://local.example.test", "-bad.example.test"):
            cases.append((json.dumps({**local, key: value}), "lower-case DNS host"))
    for key in (key for key in local if key.endswith("_file")):
        for value in ("relative.pem", "/", "//etc/root.pem", "/etc/../root.pem", "/etc/./root.pem", "/etc//root.pem", "/etc/root.pem/"):
            cases.append((json.dumps({**local, key: value}), "clean absolute path"))
    return cases


@pytest.mark.skipif(sys.platform == "win32", reason="the Go runtime reader contract compiles POSIX-only")
def test_local_tunnel_python_go_reader_parity(tmp_path: Path) -> None:
    selected_go = os.environ.get("ANVIL_CONNECT_GO") or shutil.which("go")
    if not selected_go:
        pytest.skip("Anvil Connect Go toolchain is unavailable")
    value = local_tunnel_manifest()
    normalized = validate_manifest(value)
    cases = []
    # Exercise real renderer output through the unchanged Go readers for both
    # versioned rotation and each declared CF recovery stage.
    for phase in ("local", "rotate", "public-retain-listener", "public"):
        candidate = local_tunnel_manifest()
        if phase == "rotate":
            for declaration in (candidate["gateway"], candidate["connectors"][0]):
                for key in declaration["local_tunnel"]:
                    if key.endswith("_file"):
                        declaration["local_tunnel"][key] += ".v2"
        if phase.startswith("public"):
            del candidate["connectors"][0]["local_tunnel"]
        if phase == "public":
            del candidate["gateway"]["local_tunnel"]
        files = render(candidate)["files"]
        cases.extend([
            {"mode": "gateway", "raw": files["gateway.json"], "valid": True},
            {"mode": "connector", "raw": files["connectors/dashboard.json"], "gateway": files["gateway.json"], "valid": True},
        ])
    for mode, role in (("gateway", normalized["gateway"]), ("connector", normalized["connectors"][0])):
        raw_role = json.dumps(role)
        cases.append({"mode": mode, "raw": raw_role, "valid": True})
        local = role["local_tunnel"]
        needle = json.dumps(local)
        for raw_local, reason in _invalid_local_objects(local):
            malformed = json.dumps(normalized).replace(needle, raw_local, 1)
            with pytest.raises(ManifestError, match=reason or None):
                parse_manifest_text(malformed)
            cases.append({"mode": mode, "raw": raw_role.replace(needle, raw_local, 1), "reason": reason})
        # Closed decoding rejects ambiguity of the optional field itself too.
        for spelling in ('"Local_Tunnel":', '"local_tunnel": ' + needle + ', "local_tunnel":'):
            marker = '"local_tunnel": ' + needle
            malformed = json.dumps(normalized).replace(marker, spelling + ' ' + needle, 1)
            with pytest.raises(ManifestError):
                parse_manifest_text(malformed)
            cases.append({"mode": mode, "raw": raw_role.replace(marker, spelling + ' ' + needle, 1)})
        for port in (1, 65535):
            candidate = local_tunnel_manifest()
            candidate["gateway"]["local_tunnel"]["listen"] = f"127.0.0.1:{port}"
            candidate["connectors"][0]["local_tunnel"]["address"] = f"127.0.0.1:{port}"
            rendered = render(candidate)["files"]
            cases.append({"mode": mode, "raw": rendered["gateway.json" if mode == "gateway" else "connectors/dashboard.json"], "valid": True})
        omitted = copy.deepcopy(role)
        del omitted["local_tunnel"]
        cases.append({"mode": mode, "raw": json.dumps(omitted), "valid": True})
    # Joint validation uses the explicit Go pairing seam; role JSON alone has
    # no gateway, IdP, edge, client, or rendered-root deployment context.
    for mutation in ("staged", "missing", "address", "server_name", "http_host", "public_trust", "gateway_trust", "leaf_trust"):
        candidate = local_tunnel_manifest()
        endpoint = candidate["connectors"][0]["local_tunnel"]
        reason = "must match gateway local_tunnel"
        if mutation == "staged":
            del candidate["connectors"][0]["local_tunnel"]
        elif mutation == "missing":
            del candidate["gateway"]["local_tunnel"]
        elif mutation in {"address", "server_name", "http_host"}:
            endpoint[mutation] = "127.0.0.1:18444" if mutation == "address" else "other.example.test"
        else:
            reason = "must not reuse an existing trust reference"
            if mutation == "public_trust":
                endpoint["trust_file"] = candidate["connectors"][0]["public_trust_file"]
            elif mutation == "gateway_trust":
                candidate["gateway"]["local_tunnel"]["trust_file"] = candidate["connectors"][0]["public_trust_file"]
            else:
                endpoint["trust_file"] = candidate["gateway"]["local_tunnel"]["certificate_file"]
        valid = mutation == "staged"
        if valid:
            validate_manifest(candidate)
        else:
            with pytest.raises(ManifestError, match=reason):
                validate_manifest(candidate)
        cases.append({"mode": "connector", "raw": json.dumps(candidate["connectors"][0]),
                      "gateway": json.dumps(candidate["gateway"]), "reason": reason, "valid": valid})
    corpus = tmp_path / "local-tunnel-readers.json"
    corpus.write_text(json.dumps(cases))
    checked = subprocess.run(
        [selected_go, "-C", str(ROOT / "connect"), "test", "./internal/runtime", "-run", "^TestLocalTunnelReaderParity$", "-count=1"],
        env={**os.environ, "ANVIL_CONNECT_SCHEMA_CASES": str(corpus)},
        capture_output=True, text=True, timeout=60,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("key", ["server_name", "http_host"])
def test_local_tunnel_identity_collisions(key: str) -> None:
    base = local_tunnel_manifest()
    for host in (base["gateway"]["control_host"], base["gateway"]["tunnel_host"], base["authelia"]["host"],
                 *(r["rule"]["host"] for r in base["gateway"]["gateway"]["resources"]),
                 "admin.anvil-connect.internal", "gateway.anvil-connect.internal", "tunnel.anvil-connect.internal", "tunnel-gate.anvil-connect.internal",
                 "dashboard.connector.anvil-connect.internal"):
        value = copy.deepcopy(base)
        value["gateway"]["local_tunnel"][key] = value["connectors"][0]["local_tunnel"][key] = host
        with pytest.raises(ManifestError, match="existing service identity"):
            validate_manifest(value)


def test_local_tunnel_listener_and_trust_collisions() -> None:
    base = local_tunnel_manifest()
    addresses = [base["gateway"]["gateway"]["listen"], base["gateway"]["tunnel_listen"], base["authelia"]["listen"],
                 base["clients"][0]["listen"], "127.0.0.1:443"]
    for resource in base["connectors"][0]["resources"]:
        addresses.extend((resource["reverse_address"], resource["envelope"]["listen"], resource["envelope"]["origin_url"].removeprefix("http://").removesuffix("/")))
    for address in addresses:
        value = copy.deepcopy(base)
        value["gateway"]["local_tunnel"]["listen"] = value["connectors"][0]["local_tunnel"]["address"] = address
        with pytest.raises(ManifestError, match="existing listener"):
            validate_manifest(value)
    for mode in ("gateway", "connector"):
        for file in (base["connectors"][0]["public_trust_file"], base["gateway"]["state_directory"] + "/tunnel-roots.pem",
                     base["connectors"][0]["state_directory"] + "/inner.pem", base["config_root"] + "/roots.pem", base["config_root"]):
            value = copy.deepcopy(base)
            role = value["gateway"] if mode == "gateway" else value["connectors"][0]
            role["local_tunnel"]["trust_file"] = file
            with pytest.raises(ManifestError, match="trust reference|outside runtime state|outside rendered output"):
                validate_manifest(value)
    for key in ("certificate_file", "private_key_file"):
        value = copy.deepcopy(base)
        value["connectors"][0]["local_tunnel"]["trust_file"] = value["gateway"]["local_tunnel"][key]
        with pytest.raises(ManifestError, match="trust reference"):
            validate_manifest(value)
    value = copy.deepcopy(base)
    value["gateway"]["local_tunnel"]["private_key_file"] = value["gateway"]["local_tunnel"]["certificate_file"]
    with pytest.raises(ManifestError, match="file references must be distinct"):
        validate_manifest(value)
    value = copy.deepcopy(base)
    value["caddy"]["listen"] = base["gateway"]["local_tunnel"]["listen"]
    value["caddy"]["tls"] = {"mode": "provided", "certificate_file": "/etc/edge/leaf.pem", "key_file": "/etc/edge/leaf.key"}
    with pytest.raises(ManifestError, match="existing listener"):
        validate_manifest(value)
    value["caddy"].pop("listen")
    value["gateway"]["local_tunnel"]["trust_file"] = "/etc/edge/leaf.pem"
    with pytest.raises(ManifestError, match="trust reference"):
        validate_manifest(value)


def test_local_material_stays_outside_other_role_state_and_secrets() -> None:
    base = local_tunnel_manifest()
    references = [base["authelia"]["state_directory"] + "/root.pem", base["caddy"]["state_directory"] + "/root.pem",
                  base["service_identities"]["ingress"]["directory"] + "/root.pem",
                  base["authelia"]["oidc_rsa_private_key_file"], base["environment_files"]["gateway"],
                  *base["environment_files"]["connectors"].values(), *base["environment_files"]["clients"].values()]
    for reference in references:
        value = copy.deepcopy(base)
        value["connectors"][0]["local_tunnel"]["trust_file"] = reference
        with pytest.raises(ManifestError, match="outside runtime state|existing trust reference"):
            validate_manifest(value)


def test_versioned_local_rotation_changes_only_reviewed_role_references() -> None:
    value = local_tunnel_manifest()
    previous = render(value)
    value["gateway"]["local_tunnel"]["certificate_file"] += ".v2"
    value["gateway"]["local_tunnel"]["private_key_file"] += ".v2"
    rotated = render(value)
    assert rotated["generation"] != previous["generation"]
    assert {name for name in previous["files"] if previous["files"][name] != rotated["files"][name]} == {"gateway.json", "managed.json"}
    assert json.loads(previous["files"]["gateway.json"])["local_tunnel"]["private_key_file"].endswith(".key")


def test_local_boot_preflight_waits_for_declared_tls_entry():
    data = local_tunnel_manifest(isolated_manifest())
    files = render(data)["files"]
    unit = files["systemd/anvil-connect-connector-dashboard.service"]
    assert "ExecStartPre=" + data["binary"] + " preflight --mode connector --config " in unit
    assert " --input " + data["config_root"] + "/gateway.json --socket " + data["connectors"][0]["local_tunnel"]["address"] in unit
    assert not any(name + "=" in unit for name in ("Requires", "BindsTo", "PartOf"))
    assert "ExecStartPre=" not in files["systemd/anvil-connect-gateway.service"]


@pytest.mark.skipif(sys.platform == "win32", reason="native readiness is Linux owned")
def test_native_entry_status_passes_closed_python_decoder(tmp_path):
    from anvil_serving.connect.manage import _closed_gateway_status
    go = os.environ.get("ANVIL_CONNECT_GO") or shutil.which("go")
    if not go: pytest.skip("Go unavailable")
    output = tmp_path / "native-status.json"
    checked = subprocess.run([go, "-C", str(ROOT / "connect"), "test", "./internal/runtime", "-run", "^TestLocalEntryStatusContract$", "-count=1"],
                             env={**os.environ, "ANVIL_CONNECT_STATUS_OUTPUT": str(output)}, capture_output=True, text=True, timeout=60)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    statuses = [_closed_gateway_status(json.dumps(value).encode()) for value in json.loads(output.read_text())]
    assert [value["entries"][1]["listening"] for value in statuses] == [True, False]
    assert all(len(value["entries"][1]["resources"]) == 2 for value in statuses)
