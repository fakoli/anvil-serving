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
LEAVES = {"validate", "render", "up", "down", "status", "doctor", "logs", "init", "identity", "admin", "keygen"}


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
        assert "--manifest" in text
        assert "--topology" not in text and "--target" not in text
    node = next(node for node in COMMAND_TREE.nodes if node.name == "connect")
    assert node.group == "Control Plane & Fleet"
    assert {child.name for child in node.children} == LEAVES
    assert all(child.execution_policy == "offline" and child.transports == () for child in node.children)


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
    value = SimpleNamespace(Target=SimpleNamespace(parse=target), **{name: invoke(name) for name in LEAVES | {"native_init"}})
    package = importlib.import_module("anvil_serving.connect")
    monkeypatch.setattr(package, "manage", value, raising=False)
    return calls


@pytest.mark.parametrize("flags,applied", [([], False), (["--confirm"], True), (["--confirm", "--dry-run"], False)])
def test_root_dispatch_preserves_preview_and_conditional_confirmation(fake_manager, capsys, flags, applied):
    assert cli.main(["connect", "up", "--manifest", "/private/deployment.json", "--service", "gateway", "--json", *flags]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert fake_manager == [("up", ("/private/deployment.json",), {"target": "gateway", "apply": applied})]


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
