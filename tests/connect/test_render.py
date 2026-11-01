from __future__ import annotations

import copy
import json
import platform
import sys
from pathlib import Path

import pytest

from anvil_serving.connect import manage as connect_manage
from anvil_serving.connect.config import ManifestError, validate_manifest
from anvil_serving.connect.render import MAX_OWNED_FILES, plan, render, stage


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


def scaled_manifest(*, connector_count: int, client_count: int) -> dict:
    """Build a schema-valid isolated deployment at the ownership boundary."""
    assert 1 <= connector_count <= 64
    assert 0 <= client_count < connector_count
    value = isolated_manifest()
    browser = value["connectors"][0]["resources"][0]
    api = value["connectors"][0]["resources"][1]
    browser_rule = value["gateway"]["gateway"]["resources"][0]["rule"]
    api_rule = value["gateway"]["gateway"]["resources"][1]["rule"]
    connector_template = value["connectors"][0]
    client_template = value["clients"][0]
    value["connectors"] = []
    value["clients"] = []
    value["gateway"]["gateway"]["resources"] = []
    for collection in ("connectors", "clients"):
        value["service_identities"][collection] = {}
        value["service_limits"][collection] = {}
        value["environment_files"][collection] = {}
    for index in range(connector_count):
        name = f"resource{index}"
        resource = copy.deepcopy(browser if index == 0 else api)
        rule = resource["envelope"]["rule"]
        rule["id"] = name
        rule["host"] = f"{name}.example.test"
        if index:
            resource["envelope"]["token_env"] = f"ANVIL_CONNECT_RESOURCE_{index}_TOKEN"
        resource["envelope"]["listen"] = f"127.0.0.1:{18000 + index}"
        resource["reverse_address"] = f"127.0.0.1:{17000 + index}"
        connector = copy.deepcopy(connector_template)
        connector["id"] = name
        connector["resources"] = [resource]
        connector["state_directory"] = f"/var/lib/anvil-connect/connectors/{name}"
        value["connectors"].append(connector)
        gateway_resource = {
            "connector": name,
            "tunnel_address": resource["reverse_address"],
            "rule": copy.deepcopy(browser_rule if index == 0 else api_rule),
        }
        gateway_resource["rule"]["id"] = name
        gateway_resource["rule"]["host"] = rule["host"]
        value["gateway"]["gateway"]["resources"].append(gateway_resource)
        identity = {"uid": 1300 + index, "gid": 1300 + index}
        value["service_identities"]["connectors"][name] = identity
        value["service_limits"]["connectors"][name] = copy.deepcopy(value["service_limits"]["gateway"])
        value["environment_files"]["connectors"][name] = f"/etc/anvil-connect/secrets/connectors/{name}.env"
    for index in range(1, client_count + 1):
        name = f"resource{index}"
        client = copy.deepcopy(client_template)
        client["listen"] = f"127.0.0.1:{18100 + index}"
        client["local_key_env"] = f"ANVIL_CONNECT_RESOURCE_{index}_LOCAL_KEY"
        client["remote_key_env"] = f"ANVIL_CONNECT_RESOURCE_{index}_REMOTE_KEY"
        client["rule"] = copy.deepcopy(value["gateway"]["gateway"]["resources"][index]["rule"])
        value["clients"].append(client)
        identity = {"uid": 1400 + index, "gid": 1400 + index}
        value["service_identities"]["clients"][name] = identity
        value["service_limits"]["clients"][name] = copy.deepcopy(value["service_limits"]["gateway"])
        value["environment_files"]["clients"][name] = f"/etc/anvil-connect/secrets/clients/{name}.env"
    return value


def test_literal_credentials_and_unbound_bindings_are_rejected() -> None:
    value = manifest()
    value["authelia"]["client_secret"] = "do-not-inline"
    with pytest.raises(ManifestError, match="literal credentials"):
        validate_manifest(value)
    value = manifest()
    value["gateway"]["gateway"]["resources"][0]["tunnel_address"] = "127.0.0.1:17101"
    with pytest.raises(ManifestError, match="fixed connector binding"):
        validate_manifest(value)


def test_browser_external_redirects_are_closed_and_bound_to_the_connector() -> None:
    redirect = "https://auth.example.test/api/oidc/authorization"

    def browser_rules(value: dict) -> tuple[dict, dict]:
        gateway_rule = next(resource["rule"] for resource in value["gateway"]["gateway"]["resources"]
                            if resource["rule"]["access"] == "browser")
        connector_rule = next(resource["envelope"]["rule"] for resource in value["connectors"][0]["resources"]
                              if resource["envelope"]["rule"]["id"] == gateway_rule["id"])
        return gateway_rule, connector_rule

    value = manifest()
    gateway_rule, connector_rule = browser_rules(value)
    gateway_rule["external_redirects"] = connector_rule["external_redirects"] = [redirect]
    normalized = validate_manifest(value)
    assert normalized["gateway"]["gateway"]["resources"][0]["rule"]["external_redirects"] == [redirect]

    value = manifest()
    gateway_rule, connector_rule = browser_rules(value)
    gateway_rule["external_redirects"] = [redirect]
    with pytest.raises(ManifestError, match="fixed connector binding"):
        validate_manifest(value)
    connector_rule["external_redirects"] = [redirect]
    assert validate_manifest(value)

    value = manifest()
    gateway_rule, connector_rule = browser_rules(value)
    gateway_rule["external_redirects"] = connector_rule["external_redirects"] = [redirect]
    gateway_rule["external_redirect"] = [redirect]
    with pytest.raises(ManifestError, match="unknown keys"):
        validate_manifest(value)

    invalid_redirects = (
        "http://auth.example.test/api/oidc/authorization",
        "https://auth.example.test/",
        "https://auth.example.test:443/api/oidc/authorization",
        "https://user@auth.example.test/api/oidc/authorization",
        "https://auth.example.test/api/oidc/authorization?next=/",
        "https://auth.example.test/api/oidc/authorization#fragment",
        "https://auth.example.test/api%2Foidc/authorization",
    )
    for invalid in invalid_redirects:
        value = manifest()
        gateway_rule, connector_rule = browser_rules(value)
        gateway_rule["external_redirects"] = connector_rule["external_redirects"] = [invalid]
        with pytest.raises(ManifestError, match="external redirect"):
            validate_manifest(value)

    value = manifest()
    gateway_rule, connector_rule = browser_rules(value)
    same_host = f"https://{gateway_rule['host']}/api/oidc/authorization"
    gateway_rule["external_redirects"] = connector_rule["external_redirects"] = [same_host]
    with pytest.raises(ManifestError, match="differ from the resource host"):
        validate_manifest(value)

    value = manifest()
    gateway_rule, connector_rule = browser_rules(value)
    gateway_rule["external_redirects"] = connector_rule["external_redirects"] = [redirect, redirect]
    with pytest.raises(ManifestError, match="duplicate external redirects"):
        validate_manifest(value)
    value = manifest()
    gateway_rule, connector_rule = browser_rules(value)
    redirects = [f"https://auth.example.test/api/oidc/authorization-{index}" for index in range(9)]
    gateway_rule["external_redirects"] = connector_rule["external_redirects"] = redirects
    with pytest.raises(ManifestError, match="between 1 and 8"):
        validate_manifest(value)
    value = manifest()
    gateway_rule, connector_rule = browser_rules(value)
    gateway_rule["access"] = connector_rule["access"] = "api"
    gateway_rule["native_auth"] = connector_rule["native_auth"] = "delegate-bearer"
    gateway_rule["external_redirects"] = connector_rule["external_redirects"] = [redirect]
    with pytest.raises(ManifestError, match="requires browser access"):
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
    files = render(isolated_manifest())["files"]
    text = files["authelia/configuration.yml"]
    assert "require_pkce: true" in text
    assert "pkce_challenge_method: S256" in text
    assert "id_token_signed_response_alg: RS256" in text
    assert "- openid" in text
    assert "https://dash.example.test/_anvil-connect/callback" in text
    assert '{{- fileContent "/etc/anvil-connect/secrets/oidc-rs256-private-key" | nindent 10 }}' in text
    assert "identity_validation:" in text
    assert "notifier:" in text
    assert "template_path: '/etc/anvil-connect/rendered/authelia/notification-templates'" in text
    assert "default_policy: two_factor" in text
    html = files["authelia/notification-templates/IdentityVerificationJWT.html"]
    plaintext = files["authelia/notification-templates/IdentityVerificationJWT.txt"]
    assert "Set up or reset your Anvil Connect password" in html
    assert 'href="{{ .LinkURL }}"' in html
    assert "{{ .LinkURL }}" not in html.replace('href="{{ .LinkURL }}"', "")
    assert "new password link" in html
    assert "Registering a passkey and using it to finish sign-in are separate steps" in html
    assert 'href="https://auth.example.test/reset-password/step1"' in html
    assert "open your service home" not in html
    assert "\r\n" in html and "\n" not in html.replace("\r\n", "")
    assert "{{ .LinkURL }}" in plaintext
    assert "\r\n" in plaintext and "\n" not in plaintext.replace("\r\n", "")


def test_authelia_smtp_notifier_uses_a_protected_secret_reference() -> None:
    value = isolated_manifest()
    value["authelia"]["smtp"] = {
        "address": "submission://smtp.resend.com:587",
        "username": "resend",
        "password_file": "/etc/anvil-connect/secrets/resend-smtp-key",
        "sender": "Anvil Connect <auth@auth.example.test>",
    }
    text = render(value)["files"]["authelia/configuration.yml"]
    assert "  smtp:\n    address: 'submission://smtp.resend.com:587'" in text
    assert "{{- fileContent \"/etc/anvil-connect/secrets/resend-smtp-key\" | nindent 6 }}" in text
    assert "    identifier: 'auth.example.test'" in text
    assert "    subject: '[Anvil Connect] {title}'" in text
    assert "filesystem:" not in text


def test_authelia_filesystem_notifier_remains_the_default() -> None:
    text = render(isolated_manifest())["files"]["authelia/configuration.yml"]
    assert "  filesystem:\n    filename: '/var/lib/anvil-connect/authelia/notifications.txt'" in text
    assert "  smtp:" not in text


def test_authelia_smtp_rejects_unsafe_or_unmanaged_declarations() -> None:
    base = isolated_manifest()
    valid = {
        "address": "submissions://smtp.resend.com:465",
        "username": "resend",
        "password_file": "/etc/anvil-connect/secrets/resend-smtp-key",
        "sender": "auth@auth.example.test",
    }
    invalid = [
        ({**valid, "address": "smtp://smtp.resend.com:25"}, "submission:// or submissions://"),
        ({**valid, "address": "submission://resend@smtp.resend.com:587"}, "lower-case DNS host"),
        ({**valid, "username": "{{ .Secret }}"}, "simple SMTP username"),
        ({**valid, "sender": "auth@auth.example.test\nBcc: victim@example.test"}, "unsafe control"),
        ({**valid, "sender": "{{ .Secret }} <auth@auth.example.test>"}, "simple mailbox"),
        ({**valid, "password_file": base["config_root"] + "/resend-key"}, "outside rendered output"),
        ({**valid, "password_file": base["authelia"]["state_directory"] + "/resend-key"}, "outside rendered output"),
        ({**valid, "password": "do-not-inline"}, "literal credentials"),
    ]
    for smtp, message in invalid:
        value = copy.deepcopy(base)
        value["authelia"]["smtp"] = smtp
        with pytest.raises(ManifestError, match=message):
            validate_manifest(value)
    value = copy.deepcopy(base)
    value["authelia"]["smtp"] = {**valid, "password_file": value["authelia"]["client_secret_file"]}
    with pytest.raises(ManifestError, match="distinct protected secret files"):
        validate_manifest(value)


@pytest.mark.parametrize("theme", ["light", "dark", "grey", "oled", "auto"])
def test_authelia_branding_is_optional_and_preserves_authentication_contract(theme: str) -> None:
    value = isolated_manifest()
    value["authelia"].update({
        "theme": theme,
        "asset_path": value["authelia"]["state_directory"] + "/assets/v1",
        "webauthn": {
            "enable_passkey_login": False,
            "experimental_enable_passkey_uv_two_factors": False,
            "discoverability": "required",
            "user_verification": "required",
        },
    })
    text = render(value)["files"]["authelia/configuration.yml"]
    assert f"theme: '{theme}'" in text
    assert "  asset_path: '/var/lib/anvil-connect/authelia/assets/v1'" in text
    assert "default_policy: two_factor" in text
    assert "enable_passkey_login: false" in text
    assert "experimental_enable_passkey_uv_two_factors: false" in text
    assert "require_pkce: true" in text
    assert "id_token_signed_response_alg: RS256" in text


@pytest.mark.parametrize("asset_path", [
    "/var/lib/anvil-connect/authelia",
    "/var/lib/anvil-connect/authelia/authelia.sqlite3",
    "/var/lib/anvil-connect/authelia/assets/v1/locale",
    "/etc/anvil-connect/secrets",
])
def test_authelia_branding_rejects_paths_outside_the_asset_contract(asset_path: str) -> None:
    value = manifest()
    value["authelia"]["asset_path"] = asset_path
    with pytest.raises(ManifestError, match="Authelia assets directory"):
        validate_manifest(value)


def test_authelia_branding_accepts_the_unversioned_asset_directory() -> None:
    value = manifest()
    value["authelia"]["asset_path"] = value["authelia"]["state_directory"] + "/assets"
    assert validate_manifest(value)["authelia"]["asset_path"] == value["authelia"]["asset_path"]


@pytest.mark.parametrize("theme", ["blue", "LIGHT", "", True])
def test_authelia_branding_rejects_unknown_themes(theme: object) -> None:
    value = manifest()
    value["authelia"]["theme"] = theme
    with pytest.raises(ManifestError):
        validate_manifest(value)


def test_authelia_landing_resource_uses_same_host_trampoline_and_preserves_oidc() -> None:
    baseline = render(isolated_manifest())
    value = isolated_manifest()
    value["gateway"]["gateway"]["resources"][0]["rule"]["path_prefix"] = "/console"
    value["connectors"][0]["resources"][0]["envelope"]["rule"]["path_prefix"] = "/console"
    pre_landing = render(copy.deepcopy(value))
    value["authelia"]["landing_resource"] = "dashboard"

    rendered = render(value)
    authelia = rendered["files"]["authelia/configuration.yml"]
    invitation = rendered["files"]["authelia/notification-templates/IdentityVerificationJWT.html"]
    assert "domain: 'auth.example.test'" in authelia
    assert "authelia_url: 'https://auth.example.test'" in authelia
    assert "default_redirection_url: 'https://auth.example.test/_anvil-connect/home'" in authelia
    assert "https://dash.example.test/_anvil-connect/callback" in authelia
    assert "default_policy: two_factor" in authelia
    assert 'href="https://auth.example.test/_anvil-connect/home"' in invitation
    baseline_authelia = baseline["files"]["authelia/configuration.yml"]
    assert "default_redirection_url:" not in baseline_authelia
    assert authelia.replace("      default_redirection_url: 'https://auth.example.test/_anvil-connect/home'\n", "") == baseline_authelia
    assert json.loads(rendered["files"]["gateway.json"]) == json.loads(pre_landing["files"]["gateway.json"])

    routes = json.loads(rendered["files"]["caddy.json"])["apps"]["http"]["servers"]["anvil_connect"]["routes"]
    trampoline, auth_proxy = routes[:2]
    assert trampoline["match"] == [{"host": ["auth.example.test"], "method": ["GET", "HEAD"], "path": ["/_anvil-connect/home"]}]
    assert trampoline["handle"][1] == {"handler": "static_response", "status_code": 302, "headers": {
        "Location": ["https://dash.example.test/console/_anvil-connect/home"], "Cache-Control": ["no-store"],
    }}
    assert auth_proxy["match"] == [{"host": ["auth.example.test"]}]


@pytest.mark.parametrize("landing_resource", ["missing", "dashboard-api"])
def test_authelia_landing_resource_must_be_a_declared_get_browser_resource(landing_resource: str) -> None:
    value = manifest()
    value["authelia"]["landing_resource"] = landing_resource
    with pytest.raises(ManifestError, match="declared GET browser resource"):
        validate_manifest(value)

    value = manifest()
    value["authelia"]["landing_resource"] = "dashboard"
    value["gateway"]["gateway"]["resources"][0]["rule"]["methods"] = ["POST"]
    value["connectors"][0]["resources"][0]["envelope"]["rule"]["methods"] = ["POST"]
    with pytest.raises(ManifestError, match="declared GET browser resource"):
        validate_manifest(value)


def test_authelia_additional_oidc_client_is_fixed_profile_with_protected_secret_ref() -> None:
    value = isolated_manifest()
    value["authelia"]["additional_oidc_clients"] = [{
        "client_id": "existing-integration", "client_name": "Existing Integration",
        "client_secret_file": "/etc/anvil-connect/secrets/existing-integration-oidc-client-secret",
        "redirect_uris": ["https://app.example.test/oidc/callback"],
        "groups_scope": True,
    }]
    text = render(value)["files"]["authelia/configuration.yml"]
    assert "client_id: 'existing-integration'" in text
    assert "client_name: 'Existing Integration'" in text
    assert '{{- fileContent "/etc/anvil-connect/secrets/existing-integration-oidc-client-secret" | nindent 10 }}' in text
    assert "consent_mode: implicit" in text
    assert "token_endpoint_auth_method: client_secret_basic" in text
    assert "token_endpoint_auth_method: client_secret_post" in text
    assert "          - openid\n          - email\n          - profile\n          - groups" in text
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
    with pytest.raises(ManifestError, match="distinct protected secret files"):
        validate_manifest(invalid)
    invalid = copy.deepcopy(value)
    invalid["authelia"]["additional_oidc_clients"][0]["client_secret_file"] = invalid["authelia"]["state_directory"] + "/client-secret"
    with pytest.raises(ManifestError, match="outside rendered output and Authelia state"):
        validate_manifest(invalid)
    invalid = copy.deepcopy(value)
    invalid["authelia"]["additional_oidc_clients"][0]["groups_scope"] = "true"
    with pytest.raises(ManifestError, match="must be a boolean"):
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
        target.write_bytes(content.encode("utf-8"))
    assert plan(isolated_manifest(), owned)["state"] == "current"
    (owned / "gateway.json").write_text("tampered")
    report = plan(isolated_manifest(), owned)
    assert report["state"] == "drift"
    assert "gateway.json" in report["corrupt"]
    assert (owned / "gateway.json").read_text() == "tampered"


@_NATIVE
@pytest.mark.parametrize(
    ("connector_count", "client_count", "expected_files"),
    [(61, 0, 130), (64, 63, 262)],
    ids=["former-128-file-cap", "schema-maximum"],
)
def test_large_generation_passes_activation_ownership_gate(
    tmp_path: Path, connector_count: int, client_count: int, expected_files: int,
) -> None:
    """Every schema-valid ownership boundary stages and passes activation checks."""
    value = scaled_manifest(connector_count=connector_count, client_count=client_count)
    rendered = render(value)
    assert len(rendered["ownership"]["files"]) == expected_files
    assert len(rendered["ownership"]["files"]) <= MAX_OWNED_FILES
    staged = stage(value, tmp_path / "rendered")
    root = Path(staged["path"])
    assert plan(value, root)["state"] == "current"
    # _activate performs this same ownership check before it publishes a stage.
    assert connect_manage._verify_owned_tree(root, strict=False) == (
        rendered["generation"], rendered["ownership"]["files"],
    )
    assert stage(value, tmp_path / "rendered") == staged


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
