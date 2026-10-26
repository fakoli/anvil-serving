"""The runtime inspector exposes only its closed diagnostic projection."""
import json
import subprocess

import pytest

from anvil_serving import reasoning_probe_runtime as probe


def test_launch_projection_excludes_unselected_secrets():
    row = {"Id": "a" * 64, "Image": "sha256:" + "b" * 64,
           "Config": {"Cmd": ["--api-key", "never-print-this", "--reasoning-parser", "glm45"],
                      "Env": ["UNRELATED_SECRET=never-print-this", "VLLM_USE_V2_MODEL_RUNNER=1"]},
           "Mounts": [{"Source": "/private/operator/path"}], "State": {"Running": True}}
    result = probe._launch_projection(row)
    assert "never-print-this" not in json.dumps(result)
    assert "/private/operator/path" not in json.dumps(result)
    assert result["flags"] == {"--reasoning-parser": "glm45"}
    assert result["selected_environment"] == {"VLLM_USE_V2_MODEL_RUNNER": "1"}
    row["Config"]["Cmd"].extend(["--reasoning-parser=other"])
    with pytest.raises(ValueError):
        probe._launch_projection(row)


@pytest.mark.parametrize("drift", [False, True])
def test_inspection_binds_owner_and_uses_fixed_program(tmp_path, monkeypatch, drift):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"runtime": {"model_revision": probe.REVISION,
        "image_digest": "sha256:" + "b" * 64, "template_sha256": "c" * 64}}))
    recipe = {"download": {"revision": probe.REVISION}, "serve": {"model_path": probe.MODEL_PATH, "port": 30000}}
    monkeypatch.setattr(probe.serve_recipes, "load_registry", lambda path: {})
    monkeypatch.setattr(probe.serve_recipes, "find_recipe", lambda *args: recipe)
    identity = {"container_id": "a" * 64, "running": True, "image_id": "sha256:" + "b" * 64}
    checks = []
    def identify(*args, **kwargs):
        checks.append(kwargs["require_recipe_digest"])
        return dict(identity)
    monkeypatch.setattr(probe, "_recipe_container_identity", identify)
    monkeypatch.setattr(probe, "resource_snapshot", lambda port, **kwargs: {"port": port})
    row = {"Id": "a" * 64, "Image": identity["image_id"], "State": {"Running": True},
           "Config": {"Cmd": ["--revision", probe.REVISION, "--reasoning-parser", "glm45",
                              "--chat-template", probe.TEMPLATE, "--speculative-config", json.dumps({"method":"mtp", "draft_sample_method":"probabilistic", "rejection_sample_method":"standard", "moe_backend":"b12x", "attention_backend":"B12X", "num_speculative_tokens":3})],
                      "Env": ["VLLM_USE_V2_MODEL_RUNNER=1", "VLLM_USE_FASTOKENS=1"]}}
    row["HostConfig"] = {"PortBindings": {"30000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "30000"}]}}
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        assert 0 < kwargs["timeout"] <= 45
        if argv[1] == "exec":
            assert argv[2:10] == ["--env", "CUDA_VISIBLE_DEVICES=", "--env", "HF_HUB_OFFLINE=1",
                                  "--env", "TRANSFORMERS_OFFLINE=1", "--env", "PYTHONDONTWRITEBYTECODE=1"]
            assert argv[10] == identity["container_id"]
            assert argv[11:15] == ["python3", "-B", "-c", probe.CAPTURE_PROGRAM]
            assert json.loads(argv[15])["model_path"] == probe.MODEL_PATH
            compile(probe.CAPTURE_PROGRAM, "fixed-runtime-inspector", "exec")
            result = {"files": {"runtime-template": {"sha256": "c" * 64}}}
        else:
            if drift and len(calls) > 1:
                row["State"]["StartedAt"] = "changed"
            result = [row]
        return subprocess.CompletedProcess(argv, 0, json.dumps(result).encode(), b"")
    if drift:
        with pytest.raises(ValueError, match="changed"):
            probe.inspect_runtime(tmp_path / "registry", "candidate", plan, runner=runner)
    else:
        result = probe.inspect_runtime(tmp_path / "registry", "candidate", plan, runner=runner)
        assert result["inference_requests"] == 0
        assert checks == [True, True]
        assert len(calls) == 3


@pytest.mark.parametrize("port", [True, 0, 65536, "30000@198.51.100.7:80"])
def test_resource_snapshot_refuses_invalid_port_before_reads(port):
    with pytest.raises(ValueError, match="port"):
        probe.resource_snapshot(port)


def test_inspection_plan_is_bounded_and_does_not_follow_links(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="size"):
        probe.inspect_runtime(tmp_path / "absent", "candidate", plan)
    link = tmp_path / "linked.json"
    link.symlink_to(plan)
    with pytest.raises(OSError):
        probe.inspect_runtime(tmp_path / "absent", "candidate", link)
