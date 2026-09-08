"""Real transaction file writes with independent fake runtime failure boundaries."""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from anvil_serving import router_manage
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.tools import router as owner


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_router_recovery_restores_bytes_and_original_admissions(tmp_path, monkeypatch, rollback_fails):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    config = tmp_path / "router.toml"
    baseline = b'[router]\n[[router.tiers]]\nid="primary"\nmax_concurrency=1\nmax_output_tokens=4096\nurl="http://127.0.0.1:30000/v1"\n'
    config.write_bytes(baseline)
    runtime = {"installed": baseline, "states": {"primary": "admitting", "intentionally-paused": "quiesced"}, "recreates": 0}
    monkeypatch.setattr(owner, "_run_argv", lambda *_args, **_kwargs: {"stdout": json.dumps([
        {"Destination": "/etc/anvil/config.toml", "Source": str(config), "Type": "bind", "RW": False}])})
    monkeypatch.setattr(router_manage, "installed_fleet_status", lambda **_kwargs: {"config_sha256": hashlib.sha256(runtime["installed"]).hexdigest()})

    def transition(action, **kwargs):
        if action == "status":
            return {"tiers": [{"tier_id": key, "state": value} for key, value in runtime["states"].items()]}
        if action == "readmit":
            assert runtime["installed"] == baseline, "readmission attempted before restoring prior runtime"
            runtime["states"][kwargs["tier_id"]] = "admitting"
        else:
            runtime["states"][kwargs["tier_id"]] = "quiesced"
        return {"applied": True}

    monkeypatch.setattr(router_manage, "transition_request", transition)

    def recreate(*_args, **_kwargs):
        runtime["recreates"] += 1
        if runtime["recreates"] == 1 or rollback_fails:
            return 1
        runtime["installed"] = config.read_bytes()
        return 0

    monkeypatch.setattr(router_manage, "cmd_up", recreate)

    def failed_install(_candidate, *, _install, **_kwargs):
        runtime["states"]["primary"] = "quiesced"
        assert _install(None) == 1
        raise ValueError("candidate startup failed; original admission intent remains saved")

    monkeypatch.setattr(router_manage, "install_config", failed_install)
    with pytest.raises(ToolError) as failed:
        owner.tool_router_configuration({"action": "apply", "config": str(config), "tier": "primary",
            "values": {"max_output_tokens": 2048}, "expected_baseline_sha256": hashlib.sha256(baseline).hexdigest(),
            "compose": str(tmp_path / "compose.json"), "service": "router", "env_file": str(tmp_path / "runtime.env"),
            "confirm": True, "dry_run": False, "human_approved": True})
    assert failed.value.code == "configuration_apply_failed"
    assert failed.value.details["recovery"] == ("failed" if rollback_fails else "restored")
    assert config.read_bytes() == baseline
    assert runtime["states"]["primary"] == ("quiesced" if rollback_fails else "admitting")
    assert runtime["states"]["intentionally-paused"] == "quiesced"
    assert runtime["recreates"] == 2


def test_second_process_admission_client_cannot_race_owner_transaction(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    code = "from anvil_serving.serves import _switch_role_lock\nwith _switch_role_lock('promotion'):\n print('locked',flush=True)\n input()\n"
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, env=dict(os.environ))
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeError, match="already active"):
            router_manage.transition_request("quiesce", tier_id="primary", confirm=True, dry_run=False,
                env={"ANVIL_ROUTER_TOKEN": "fixture-only"}, _open=lambda *_args, **_kwargs: pytest.fail("conflicting owner reached network"))
    finally:
        child.communicate("\n", timeout=5)
        assert child.returncode == 0


def test_prewrite_failure_reports_failed_admission_recovery(tmp_path, monkeypatch):
    """A failed best-effort readmit must not be reported as recovery not needed."""
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    config = tmp_path / "router.toml"
    baseline = b'[router]\n[[router.tiers]]\nid="primary"\nmax_concurrency=1\nmax_output_tokens=4096\nurl="http://127.0.0.1:30000/v1"\n'
    config.write_bytes(baseline)
    states = {"primary": "admitting"}
    monkeypatch.setattr(owner, "_run_argv", lambda *_args, **_kwargs: {"stdout": json.dumps([
        {"Destination": "/etc/anvil/config.toml", "Source": str(config),
         "Type": "bind", "RW": False}])})
    monkeypatch.setattr(router_manage, "installed_fleet_status", lambda **_kwargs: {
        "config_sha256": hashlib.sha256(baseline).hexdigest()
    })

    def transition(action, **kwargs):
        if action == "status":
            return {"tiers": [
                {"tier_id": tier_id, "state": state}
                for tier_id, state in states.items()
            ]}
        if action == "readmit":
            raise ValueError("readmission unavailable")
        states[kwargs["tier_id"]] = "quiesced"
        return {"applied": True}

    monkeypatch.setattr(router_manage, "transition_request", transition)

    def fail_before_write(_candidate, **_kwargs):
        states["primary"] = "quiesced"
        raise ValueError("installer rejected stale baseline")

    monkeypatch.setattr(router_manage, "install_config", fail_before_write)
    with pytest.raises(ToolError) as failed:
        owner.tool_router_configuration({
            "action": "apply", "config": str(config), "tier": "primary",
            "values": {"max_output_tokens": 2048},
            "expected_baseline_sha256": hashlib.sha256(baseline).hexdigest(),
            "compose": str(tmp_path / "compose.json"), "service": "router",
            "env_file": str(tmp_path / "runtime.env"),
            "confirm": True, "dry_run": False, "human_approved": True,
        })
    assert failed.value.code == "configuration_apply_failed"
    assert failed.value.details["recovery"] == "failed"
    assert config.read_bytes() == baseline
    assert states == {"primary": "quiesced"}


def test_successful_recreate_preserves_intentionally_paused_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    config = tmp_path / "router.toml"
    config.write_text('[router]\n[[router.tiers]]\nid="primary"\nmax_concurrency=1\nmax_output_tokens=4096\nurl="http://127.0.0.1:30000/v1"\n')
    runtime = {"installed": config.read_bytes(), "state": "quiesced"}
    monkeypatch.setattr(owner, "_run_argv", lambda *_args, **_kwargs: {"stdout": json.dumps([
        {"Destination": "/etc/anvil/config.toml", "Source": str(config), "Type": "bind", "RW": False}])})
    monkeypatch.setattr(router_manage, "installed_fleet_status", lambda **_kwargs: {"config_sha256": hashlib.sha256(runtime["installed"]).hexdigest()})

    def transition(action, **_kwargs):
        if action != "status":
            runtime["state"] = "quiesced" if action == "quiesce" else "admitting"
        return {"tiers": [{"tier_id": "primary", "state": runtime["state"]}]}

    def recreate(*_args, **_kwargs):
        runtime.update(installed=config.read_bytes(), state="admitting")
        return 0

    def install(_candidate, *, _install, **_kwargs):
        assert _install(None) == 0
        return {"applied": True, "dry_run": False}

    monkeypatch.setattr(router_manage, "transition_request", transition)
    monkeypatch.setattr(router_manage, "cmd_up", recreate)
    monkeypatch.setattr(router_manage, "install_config", install)
    result = owner.tool_router_configuration({"action": "apply", "config": str(config), "tier": "primary",
        "values": {"max_output_tokens": 2048}, "compose": str(tmp_path / "compose.json"),
        "service": "router", "env_file": str(tmp_path / "runtime.env"),
        "confirm": True, "dry_run": False, "human_approved": True})
    assert result["data"]["applied"] is True
    assert runtime["state"] == "quiesced"
    assert b"2048" in runtime["installed"]
