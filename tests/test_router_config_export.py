import hashlib
import json
import subprocess
import sys
from pathlib import Path
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


@pytest.mark.parametrize("change", ["writable", "volume", "duplicate", "stopped", "relative", "wrong_name", "foreign", "unlabeled", "wrong_service", "bad_mounts"])
def test_rejects_unowned_mount(setup, change):
    _, digest, state, _, run = setup
    m = state["mounts"][0]
    if change == "writable": m["RW"] = True
    if change == "volume": m["Type"] = "volume"
    if change == "duplicate": state["mounts"].append(dict(m))
    if change == "stopped": state["running"] = False
    if change == "relative": m["Source"] = "router.toml"
    if change == "wrong_name": m["Source"] = str(Path(m["Source"]).with_name("secrets.toml"))
    if change == "foreign": state["project"] = "other"
    if change == "unlabeled": state.pop("project")
    if change == "wrong_service": state["service"] = "other"
    if change == "bad_mounts": state["mounts"] = [None]
    with pytest.raises(UsageError):
        export.export_installed_config("anvil-router", digest, _run=run)


@pytest.mark.parametrize("content", [
    CONFIG + '\n[secrets]\napi_key = "private-value"\n',
    CONFIG + '\n# Authorization: Bearer private-value\n',
    CONFIG + '\n[extra]\nrouter_config = "other.toml"\n',
    CONFIG.replace('[server]', '[server]\nauthorization_policy_path = "policy.json"'),
    "invalid = [",
    'server = "bad"\n',
    "x" * (export.MAX_BYTES + 1),
], ids=["secret-field", "secret-comment", "dependency", "authorization-policy", "malformed", "server-shape", "oversized"])
def test_rejects_unsafe_or_incomplete_content(setup, content):
    path, _, _, _, run = setup
    path.write_text(content)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(UsageError) as caught:
        export.export_installed_config("anvil-router", digest, _run=run)
    assert "private-value" not in str(caught.value)


def test_rejects_stale_expected_and_mounted_bytes(setup):
    path, digest, _, _, run = setup
    with pytest.raises(UsageError):
        export.export_installed_config("anvil-router", "b" * 64, _run=run)
    path.write_text(CONFIG + "\n# changed\n")
    with pytest.raises(UsageError):
        export.export_installed_config("anvil-router", hashlib.sha256(path.read_bytes()).hexdigest(), _run=run)


def test_rejects_mount_replacement_during_read(setup):
    _, digest, state, _, run = setup
    def changing(argv, **kwargs):
        result = run(argv, **kwargs)
        if argv[1] == "exec": state["id"] = "c" * 64
        return result
    with pytest.raises(UsageError):
        export.export_installed_config("anvil-router", digest, _run=changing)


def test_validates_captured_bytes_not_source_path(setup, monkeypatch):
    _, digest, _, _, run = setup
    def validate(argv, **kwargs):
        if export._VALIDATE_CODE in argv:
            assert kwargs["input"] == CONFIG
            assert argv[3] == "a" * 64
        return run(argv, **kwargs)
    assert export.export_installed_config("anvil-router", digest, _run=validate)["content"] == CONFIG


def test_runtime_validator_refusal_never_exports_bytes(setup):
    _, digest, _, _, run = setup
    def reject(argv, **kwargs):
        if export._VALIDATE_CODE in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="sensitive parser detail")
        return run(argv, **kwargs)
    with pytest.raises(UsageError) as caught:
        export.export_installed_config("anvil-router", digest, _run=reject)
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize("raw,valid", [(CONFIG, True), ('server = "bad"\n' + CONFIG.split("[router]", 1)[1], False), (CONFIG.replace("context_limit = 131072", 'context_limit = "bad"'), False)], ids=["valid", "bad-server", "bad-router"])
def test_runtime_validator_checks_exact_stdin(raw, valid):
    result = subprocess.run([sys.executable, "-c", export._VALIDATE_CODE], input=raw.encode(), capture_output=True)
    assert (result.returncode == 0) is valid
    assert result.stdout.strip() == (hashlib.sha256(raw.encode()).hexdigest().encode() if valid else b"")


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


@pytest.mark.parametrize("container,digest", [("--all", "a" * 64), ("router", "bad"), ("../router", "a" * 64)])
def test_rejects_argument_injection(container, digest):
    with pytest.raises(UsageError):
        export.export_installed_config(container, digest, _run=lambda *a, **kw: pytest.fail("docker called"))


def test_dispatch_returns_typed_refusal_without_traceback():
    result = export.dispatch(["--expected-sha256", "bad"])
    assert result.data is None
    assert result.error.code == "router_config_export_refused"
    assert result.error.details == {"stage": "ownership"}


@pytest.mark.parametrize("budget,accepted", [(49152, True), (0, True), (1048576, True), ("private-value", False), (True, False), (-1, False), (1048577, False)])
def test_narrow_numeric_reasoning_budget_exception(budget, accepted):
    data = {"router": {"tiers": [{"extra_body": {"thinking_token_budget": budget}}]}}
    projected = export._secret_projection(data)
    if accepted:
        export.operator_config._assert_no_secret_literals(projected, path="router.toml")
        assert data["router"]["tiers"][0]["extra_body"]["thinking_token_budget"] == budget
    else:
        with pytest.raises(export.operator_config.ConfigExportError):
            export.operator_config._assert_no_secret_literals(projected, path="router.toml")


def test_budget_exception_does_not_cover_other_fields_or_scopes():
    for data in [{"thinking_token_budget": 49152}, {"router": {"tiers": [{"params": {"extra_body": {"thinking_token_budget": 49152}}}]}}, {"router": {"tiers": [{"params": {"api_token": 49152}}]}}]:
        with pytest.raises(export.operator_config.ConfigExportError):
            export.operator_config._assert_no_secret_literals(export._secret_projection(data), path="router.toml")
