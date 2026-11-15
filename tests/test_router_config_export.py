import hashlib
import json
import base64
from types import SimpleNamespace

import pytest

from anvil_serving import router_config_export as export
from anvil_serving.operator_output import UsageError


CONFIG = '''[server]
auth_env = "ANVIL_ROUTER_TOKEN"
[router]
[[router.tiers]]
id = "secondary"
base_url = "http://127.0.0.1:30001/v1"
model = "candidate"
context_limit = 131072
dialect = "openai"
privacy = "local"
tool_support = true
auth_env = "ANVIL_UPSTREAM_TOKEN"
[router.model_routes]
"llm.secondary" = "secondary"
'''


@pytest.fixture(autouse=True)
def snapshot_root(tmp_path, monkeypatch):
    root = tmp_path / "router-snapshots"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(export, "SNAPSHOT_ROOT", root)


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "router.toml"
    path.write_bytes(CONFIG.encode())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    state = {"id": "a" * 64, "running": True, "project": "anvil-serving", "service": "router", "mounts": [{
        "Type": "bind", "Source": str(path), "Destination": export.INSTALLED_PATH, "RW": False,
    }]}
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        output = json.dumps(state) if argv[1] == "inspect" else digest + "\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    return path, digest, state, calls, run


def test_exact_installed_export_does_not_enumerate_credentials(setup, monkeypatch):
    path, digest, _, calls, run = setup
    (path.parent / ".env").write_text("ANVIL_ROUTER_TOKEN=private-value")
    monkeypatch.setattr(export.operator_config, "inventory", lambda *a, **kw: pytest.fail("whole-home scan"))
    result = export.export_installed_config("anvil-router", digest, _run=run)
    assert result["content"] == CONFIG
    assert result["sha256"] == digest
    assert "private-value" not in json.dumps(result)
    assert all(row[1] in {"inspect", "exec"} for row in calls)
    assert calls[1][3] == "a" * 64


def test_rejects_selected_symlink_but_not_unselected_link(setup):
    path, digest, _, _, run = setup
    target = path.parent / "other.toml"
    try:
        (path.parent / ".env").symlink_to(path.parent / "missing-secret")
    except OSError:
        pytest.skip("symlink privilege unavailable")
    assert export.export_installed_config("anvil-router", digest, _run=run)["sha256"] == digest
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(UsageError):
        export.export_installed_config("anvil-router", digest, _run=run)


def test_budget_exception_does_not_cover_other_fields_or_scopes():
    for data in [{"thinking_token_budget": 49152}, {"router": {"tiers": [{"params": {"extra_body": {"thinking_token_budget": 49152}}}]}}, {"router": {"tiers": [{"params": {"api_token": 49152}}]}}]:
        with pytest.raises(export.operator_config.ConfigExportError):
            export.operator_config._assert_no_secret_literals(export._secret_projection(data), path="router.toml")


def _runtime_snapshot_runner(state, raw):
    digest = hashlib.sha256(raw).hexdigest()

    def run(argv, **kwargs):
        if argv[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout=json.dumps(state).encode(), stderr=b"")
        if export._CAPTURE_CODE in argv:
            return SimpleNamespace(returncode=0, stdout=base64.b64encode(raw) + b"\n", stderr=b"")
        if export._VALIDATE_INSTALLED_CODE in argv:
            return SimpleNamespace(returncode=0, stdout=(digest + "\n").encode(), stderr=b"")
        pytest.fail("unexpected command")
    return digest, run


def test_runtime_snapshot_refuses_literal_secret_and_existing_output(tmp_path):
    raw = (CONFIG + '\n[secrets]\napi_key = "private-value"\n').encode()
    state = {"id": "a" * 64, "running": True, "project": "anvil-serving", "service": "router", "mounts": [{
        "Type": "bind", "Source": "/root/unreadable/router.toml", "Destination": export.INSTALLED_PATH, "RW": False,
    }]}
    digest, run = _runtime_snapshot_runner(state, raw)
    protected = export.SNAPSHOT_ROOT
    output = protected / "snapshot.toml"
    with pytest.raises(UsageError) as caught:
        export.export_runtime_snapshot("anvil-router", digest, str(output), _run=run)
    assert caught.value.details == {"stage": "secret_validation"}
    assert not output.exists()
    clean = CONFIG.encode(); digest, run = _runtime_snapshot_runner(state, clean)
    output.write_bytes(b"existing")
    with pytest.raises(UsageError) as caught:
        export.export_runtime_snapshot("anvil-router", digest, str(output), _run=run)
    assert caught.value.details == {"stage": "output"}
    assert output.read_bytes() == b"existing"


def test_runtime_snapshot_refuses_capture_or_custody_drift(tmp_path):
    raw = CONFIG.encode()
    state = {"id": "a" * 64, "running": True, "project": "anvil-serving", "service": "router", "mounts": [{
        "Type": "bind", "Source": "/root/unreadable/router.toml", "Destination": export.INSTALLED_PATH, "RW": False,
    }]}
    digest, base = _runtime_snapshot_runner(state, raw)
    calls = 0
    def drift(argv, **kwargs):
        nonlocal calls
        result = base(argv, **kwargs)
        if export._CAPTURE_CODE in argv:
            calls += 1
            if calls == 2:
                state["id"] = "b" * 64
        return result
    protected = export.SNAPSHOT_ROOT
    with pytest.raises(UsageError) as caught:
        export.export_runtime_snapshot("anvil-router", digest, str(protected / "snapshot.toml"), _run=drift)
    assert caught.value.details == {"stage": "installed_identity"}


@pytest.mark.parametrize("content", [
    CONFIG + '\n[ignored]\nmanifest="Bearer private-value"\n',
    CONFIG + '\n[ignored]\nmanifest="basic private-value"\n',
    CONFIG.replace('[server]', '[server]\napi_keys_path="Bearer private-value"'),
    CONFIG.replace('[server]', '[server]\nauthorization_policy_path="https://user:password@example.test/policy"'),
    CONFIG + '\n[ignored]\nauthorization_policy_path="private-value"\n',
])
def test_snapshot_path_projection_cannot_hide_literal_credentials(content):
    import tomllib
    from anvil_serving.operator_config import ConfigExportError

    with pytest.raises((ConfigExportError, ValueError)):
        projected = export._snapshot_secret_projection(tomllib.loads(content))
        export.operator_config._assert_no_secret_literals(projected, path="router.toml")


def test_snapshot_preserves_validated_webui_ids_without_resolving_signer(monkeypatch):
    raw = (CONFIG.replace('[server]', '[server]\nauthorization_policy_path="policy.json"') + '''
[[server.webui_identity]]
credential_id = "test-ui-service"
credential_kind = "configured_scope"
instance = "test-ui"
signer_env = "TEST_UI_SIGNER"
''').encode()
    state = {"id": "a" * 64, "running": True, "project": "anvil-serving", "service": "router", "mounts": [{
        "Type": "bind", "Source": "/root/unreadable/router.toml", "Destination": export.INSTALLED_PATH, "RW": False,
    }]}
    from anvil_serving.router import identity
    monkeypatch.setattr(identity, "read_private_auth_file", lambda *a, **kw: pytest.fail("signer read"))
    digest, run = _runtime_snapshot_runner(state, raw)
    output = export.SNAPSHOT_ROOT / "snapshot.toml"
    result = export.export_runtime_snapshot("anvil-router", digest, str(output), _run=run)
    assert output.read_bytes() == raw and result["dependency_complete"] is False


@pytest.mark.parametrize("section,fields", [
    ("ignored", 'credential_id="private-value"'),
    ("[server.webui_identity]", 'credential_id="Bearer private-value"\ncredential_kind="device_key"\ninstance="test-ui"\nsigner_env="TEST_UI_SIGNER"'),
    ("[server.webui_identity]", 'credential_id="test-ui"\ncredential_kind="Bearer private-value"\ninstance="test-ui"\nsigner_env="TEST_UI_SIGNER"'),
])
def test_snapshot_identity_projection_is_schema_scoped(section, fields):
    import tomllib
    from anvil_serving.operator_config import ConfigExportError
    with pytest.raises((ValueError, ConfigExportError)):
        projected = export._snapshot_secret_projection(tomllib.loads(CONFIG + f"\n[{section}]\n{fields}\n"))
        export.operator_config._assert_no_secret_literals(projected, path="router.toml")
