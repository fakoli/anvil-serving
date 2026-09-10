from __future__ import annotations

import importlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
from types import SimpleNamespace
import zipfile

import pytest

from anvil_serving import cli
from anvil_serving.commands import COMMAND_TREE, manifest_data
from anvil_serving.connect.cli import dispatch


ROOT = Path(__file__).parents[2]
LEAVES = {"validate", "render", "up", "down", "status", "doctor", "logs", "init", "identity", "admin", "keygen", "backup", "restore", "migration", "qualify"}


def test_connect_registry_help_has_no_runtime_discovery(capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("help invoked a process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert cli.main(["connect", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert all(leaf in help_text for leaf in LEAVES)
    for leaf in LEAVES:
        assert cli.main(["connect", leaf, "--help"]) == 0
        text = capsys.readouterr().out
        if leaf == "qualify":
            assert "--config" in text and "--lane" in text
            assert "--manifest" not in text
        else:
            assert "--manifest" in text
        assert "--topology" not in text and "--target" not in text
    node = next(node for node in COMMAND_TREE.nodes if node.name == "connect")
    assert node.group == "Control Plane & Fleet"
    assert {child.name for child in node.children} == LEAVES
    assert all(child.execution_policy == "offline" and child.transports == () for child in node.children)


def test_qualification_uses_saved_settings_without_deployment_discovery(monkeypatch, capsys):
    calls = []

    def qualify(config_path, *, lane):
        calls.append((config_path, lane))
        return {"schema": "anvil-connect.qualification/v1", "ok": True, "lane": lane}

    class QualificationError(Exception):
        pass

    monkeypatch.setitem(sys.modules, "anvil_serving.connect.qualification", SimpleNamespace(
        qualify=qualify, QualificationError=QualificationError,
    ))
    manager = importlib.import_module("anvil_serving.connect.manage")
    monkeypatch.setattr(manager, "supported_platform", lambda: pytest.fail("deployment discovery"))
    assert cli.main(["connect", "qualify", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert calls == [(None, "baseline")]
    for args in (
        ["qualify", "--lane", "unknown"],
        ["qualify", "--config", "/one", "--config", "/two"],
        ["qualify", "--manifest", "/deployment.json"],
    ):
        assert dispatch(args).error is not None
    assert len(calls) == 1


@pytest.mark.parametrize("preflight", [False, True])
def test_qualification_failure_is_nonzero_and_does_not_echo_private_errors(monkeypatch, capsys, preflight):
    class QualificationError(Exception):
        code = "connect_qualification_config_invalid"

    def qualify(config_path, *, lane):
        if preflight:
            raise QualificationError("private-sentinel")
        return {"ok": False, "lane": lane, "state": "failed", "error_code": "browser_failed", "artifact_dir": "/qualification-artifacts/run-synthetic"}

    monkeypatch.setitem(sys.modules, "anvil_serving.connect.qualification", SimpleNamespace(
        qualify=qualify, QualificationError=QualificationError,
    ))
    assert cli.main(["connect", "qualify", "--config", "/private/settings.toml", "--json"]) != 0
    output = capsys.readouterr().out
    assert "private-sentinel" not in output and "/private/settings.toml" not in output
    envelope = json.loads(output)
    assert envelope["ok"] is False
    if preflight:
        assert envelope["error"]["code"] == QualificationError.code
        assert envelope["data"] == {
            "schema": "anvil-connect.qualification/v1", "ok": False, "state": "not-run",
            "error_code": QualificationError.code, "stage": "preflight",
            "counts": {"passed": 0, "failed": 0, "skipped": 0, "not_run": 2},
        }
    else:
        assert envelope["data"] == {
            "ok": False, "lane": "baseline", "state": "failed",
            "error_code": "browser_failed", "artifact_dir": "/qualification-artifacts/run-synthetic",
        }
        assert envelope["error"]["code"] == "connect_qualification_failed"


def test_qualification_executed_error_is_not_labeled_not_run(monkeypatch, capsys):
    class QualificationError(Exception):
        code = "staging-failed"
        execution_started = True
        stage = "execution"

    def qualify(config_path, *, lane):
        raise QualificationError("private-sentinel")

    monkeypatch.setitem(sys.modules, "anvil_serving.connect.qualification", SimpleNamespace(
        qualify=qualify, QualificationError=QualificationError,
    ))
    assert cli.main(["connect", "qualify", "--json"]) != 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"] == {
        "schema": "anvil-connect.qualification/v1", "ok": False, "state": "failed",
        "error_code": "staging-failed", "stage": "execution",
        "counts": None,
    }
    assert "private-sentinel" not in json.dumps(envelope)



@pytest.mark.parametrize("args", [
    ["connect", "private-sentinel", "--config", "/private-sentinel"],
    ["connect", "qualify", "--private-sentinel", "/private-sentinel"],
])
def test_connect_json_rejects_unknown_operands_without_echoing_them(capsys, args):
    assert cli.main([*args, "--json"]) != 0
    output = capsys.readouterr().out
    assert "private-sentinel" not in output
    assert json.loads(output)["ok"] is False


@pytest.fixture
def fake_manager(monkeypatch):
    calls = []
    def invoke(action):
        def call(*args, **kwargs):
            calls.append((action, args, kwargs))
            return {"schema": "anvil-connect.management/v1", "action": action, "state": "preview" if not kwargs.get("apply") else "applied"}
        return call
    def target(value):
        if value != "gateway" and not value.startswith(("connector:", "client:")):
            raise ValueError("private operand")
        return value
    value = SimpleNamespace(
        Target=SimpleNamespace(parse=target),
        supported_platform=lambda: True,
        **{name: invoke(name) for name in LEAVES | {"native_init", "up_many"}},
    )
    package = importlib.import_module("anvil_serving.connect")
    monkeypatch.setattr(package, "manage", value, raising=False)
    return calls


@pytest.mark.parametrize("flags,applied", [([], False), (["--confirm"], True), (["--confirm", "--dry-run"], False)])
def test_root_dispatch_preserves_preview_and_conditional_confirmation(fake_manager, capsys, flags, applied):
    assert cli.main(["connect", "up", "--manifest", "/private/deployment.json", "--service", "gateway", "--json", *flags]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert fake_manager == [("up", ("/private/deployment.json",), {"target": "gateway", "apply": applied})]


def test_explicit_coordinated_upgrade_is_one_previewed_operation(fake_manager):
    result = dispatch(["up", "--manifest", "/deployment.json", "--services", "gateway,connector:origin", "--upgrade"])
    assert result.error is None
    assert fake_manager == [("up_many", ("/deployment.json", ("gateway", "connector:origin")), {"upgrade": True, "apply": False})]


def test_coordinated_selection_is_unique_and_exclusive(fake_manager):
    for operands in (["--service", "gateway", "--services", "gateway"], ["--services", "gateway,gateway"], ["--services", "gateway,"]):
        result = dispatch(["up", "--manifest", "/deployment.json", *operands])
        assert result.error is not None
    assert fake_manager == []


def test_unsupported_platform_returns_typed_operator_error_before_manifest_access(monkeypatch):
    manager = importlib.import_module("anvil_serving.connect.manage")
    monkeypatch.setattr(manager, "supported_platform", lambda: False)
    result = dispatch(["validate", "--manifest", "/private/deployment.json"])
    assert result.error is not None
    assert result.error.code == "connect_platform_unsupported"
    assert "deployment.json" not in str(result.error)


def test_cli_rejects_unknown_duplicate_or_credential_like_operands_before_manager(fake_manager):
    for args in (
        ["up", "--manifest", "/a", "--manifest", "/b", "--service", "gateway"],
        ["admin", "--manifest", "/a", "--request", "/b", "--secret", "private-sentinel"],
        ["logs", "--manifest", "/a", "--service", "gateway", "--tail", "201"],
        ["up", "--manifest", "/a"],
    ):
        result = dispatch(args)
        assert result.error is not None
        assert "private-sentinel" not in str(result.error)
    assert fake_manager == []


def test_admin_passes_paths_only_and_preview_does_not_become_issuance(fake_manager):
    result = dispatch(["admin", "--manifest", "/deployment.json", "--request", "/request.json", "--output", "/private/issued.json"])
    assert result.error is None
    assert fake_manager == [("admin", ("/deployment.json",), {"request_path": "/request.json", "output_path": "/private/issued.json", "apply": False})]


@pytest.mark.parametrize("partial,exit_code", [(False, 1), (True, 5)])
def test_execution_failures_are_redacted_and_distinguish_partial_results(fake_manager, monkeypatch, capsys, partial, exit_code):
    package = importlib.import_module("anvil_serving.connect")
    def fail(*args, **kwargs):
        error = RuntimeError("private-subprocess-output")
        error.may_have_executed = partial
        raise error
    monkeypatch.setattr(package.manage, "up", fail)
    assert cli.main(["connect", "up", "--manifest", "/a", "--service", "gateway", "--confirm", "--json"]) == exit_code
    output = capsys.readouterr().out
    assert "private-subprocess-output" not in output
    result = json.loads(output)
    assert result["error"]["details"]["may_have_executed"] is partial


def test_existing_edge_manifest_records_remain_unchanged():
    # Golden canonical digest captured before Connect registration. No Git
    # history or live Tailscale inspection is required by this regression.
    edges = [entry for entry in manifest_data()["commands"] if entry["path"] == "edge" or entry["path"].startswith("edge ")]
    digest = hashlib.sha256(json.dumps(edges, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == "69eb1f26a60755fb425afc69dd53b9a2955599e7a9f192cf2823a5529e67fce6"


def test_packaged_component_pins_match_validated_lab_artifacts():
    assert (ROOT / "anvil_serving/connect/components.lock.json").read_bytes() == (ROOT / "connect/lab/edge-tools.json").read_bytes()


def test_installed_wheel_exposes_connect_without_starting_native_tools(tmp_path):
    # Build/install into isolated directories; no user environment is changed.
    wheels, installed = tmp_path / "wheels", tmp_path / "installed"
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the isolated installed-wheel qualification")
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(wheels), str(ROOT)], check=True, capture_output=True, text=True, timeout=90)
    wheel, = wheels.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert "anvil_serving/connect/components.lock.json" in archive.namelist()
        metadata_name, = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        metadata = archive.read(metadata_name).decode()
        assert not any(line.startswith("Requires-Dist:") and "; extra ==" not in line for line in metadata.splitlines())
    subprocess.run([uv, "pip", "install", "--python", sys.executable, "--no-deps", "--target", str(installed), str(wheel)], check=True, capture_output=True, text=True, timeout=60)
    script = """
import subprocess
def forbidden(*args, **kwargs):
    raise AssertionError('discovery started during installed help')
subprocess.Popen = forbidden
from importlib.resources import files
import sys
from anvil_serving import cli
assert cli.__file__.startswith(sys.argv[1])
assert files('anvil_serving.connect').joinpath('components.lock.json').is_file()
assert cli.main(['connect', '--help']) == 0
"""
    result = subprocess.run([sys.executable, "-c", script, str(installed)], cwd=tmp_path, env=os.environ | {"PYTHONPATH": str(installed)}, check=True, capture_output=True, text=True, timeout=20)
    assert "Anvil Connect" in result.stdout


@pytest.mark.parametrize("kind", ["container", "vm"])
def test_qualification_preparation_is_explicit(monkeypatch, capsys, kind):
    calls = []
    def prepare(config):
        calls.append(config)
        return {"schema": f"anvil-connect.qualification-{kind}/v1", "reused": True}
    monkeypatch.setitem(sys.modules, f"anvil_serving.connect.qualification_{kind}", SimpleNamespace(prepare=prepare))
    assert cli.main(["connect", "qualify", f"--prepare-{kind}", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["reused"] is True
    assert calls == [None]
    assert dispatch(["qualify", f"--prepare-{kind}", "--lane", "baseline"]).error is not None
    assert dispatch(["qualify", "--prepare-container", "--prepare-vm"]).error is not None
    assert calls == [None]


@pytest.mark.parametrize("kind", ["container", "vm"])
def test_qualification_preparation_error_does_not_echo_output(monkeypatch, capsys, kind):
    from anvil_serving.connect.qualification import QualificationError
    def prepare(config):
        raise QualificationError("runner-failed", "private-build-sentinel")
    monkeypatch.setitem(sys.modules, f"anvil_serving.connect.qualification_{kind}", SimpleNamespace(prepare=prepare))
    assert cli.main(["connect", "qualify", f"--prepare-{kind}", "--json"]) != 0
    output = capsys.readouterr().out
    assert "private-build-sentinel" not in output
    assert json.loads(output)["data"]["error_code"] == "runner-failed"


def test_container_baseline_dispatch_uses_saved_configuration(monkeypatch, capsys):
    calls = []
    def qualify(config, *, lane):
        calls.append((config, lane))
        return {"ok":True,"state":"passed","counts":{"passed":2,"failed":0,"skipped":0,"not_run":0}}
    monkeypatch.setitem(sys.modules,"anvil_serving.connect.qualification_container_run",SimpleNamespace(qualify=qualify))
    assert cli.main(["connect","qualify","--lane","container-baseline","--config","/private/settings.toml","--json"]) == 0
    assert calls == [("/private/settings.toml", "container-baseline")]
    assert json.loads(capsys.readouterr().out)["data"]["counts"]["passed"] == 2


def test_revocation_dispatch_uses_saved_configuration_and_ten_case_contract(monkeypatch, capsys):
    calls = []
    def qualify(config, *, lane):
        calls.append((config, lane))
        return {"ok": True, "state": "passed", "counts": {"passed": 10, "failed": 0, "skipped": 0, "not_run": 0}}
    monkeypatch.setitem(sys.modules, "anvil_serving.connect.qualification_container_run", SimpleNamespace(qualify=qualify, _DEVICE_TESTS=("device",), _REVOCATION_TESTS=("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")))
    assert cli.main(["connect", "qualify", "--lane", "revocation", "--config", "/private/settings.toml", "--json"]) == 0
    assert calls == [("/private/settings.toml", "revocation")]
    assert json.loads(capsys.readouterr().out)["data"]["counts"]["passed"] == 10


def test_device_preflight_reports_all_required_scenarios_not_run(tmp_path):
    result = dispatch(["qualify", "--lane", "device", "--config", str(tmp_path / "missing.toml")])
    assert result.error is not None
    assert result.data["state"] == "not-run"
    assert result.data["counts"] == {"passed":0,"failed":0,"skipped":0,"not_run":10}


def test_revocation_preflight_reports_only_its_ten_scenarios_not_run(tmp_path):
    result = dispatch(["qualify", "--lane", "revocation", "--config", str(tmp_path / "missing.toml")])
    assert result.error is not None
    assert result.data["state"] == "not-run"
    assert result.data["counts"] == {"passed":0,"failed":0,"skipped":0,"not_run":10}


@pytest.mark.parametrize("lane", ["device", "revocation"])
def test_container_qualification_refuses_nonlinux_before_os_apis(monkeypatch, tmp_path, lane):
    from anvil_serving.connect import qualification_container_run

    monkeypatch.setattr(qualification_container_run.sys, "platform", "win32")
    result = dispatch(["qualify", "--lane", lane, "--config", str(tmp_path / "missing.toml")])
    assert result.error is not None
    assert result.error.code == "runner-unavailable"
    assert result.data["state"] == "not-run"
    assert result.data["counts"] == {"passed": 0, "failed": 0, "skipped": 0, "not_run": 10}
