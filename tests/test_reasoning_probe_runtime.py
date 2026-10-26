"""The runtime inspector exposes only its closed diagnostic projection."""
import json
import shlex
import subprocess
import time

import pytest

from anvil_serving import reasoning_probe_runtime as probe
from anvil_serving import reasoning_probe


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
@pytest.mark.parametrize("patch_state", [None, "valid", "bad_labels", "bad_source", "bad_helper",
    "missing_entrypoint", "wrong_entrypoint", "wrong_command", "wrong_flags",
    "recipe_entrypoint", "recipe_trace", "trace_missing", "trace_zero", "trace_duplicate"])
def test_inspection_binds_owner_and_uses_fixed_program(tmp_path, monkeypatch, drift, patch_state):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"runtime": {"model_revision": probe.REVISION,
        "image_digest": "sha256:" + "b" * 64, "template_sha256": "c" * 64}}))
    if patch_state:
        value = json.loads(plan.read_text())
        value["runtime_patch"] = {"base_image_digest": reasoning_probe._RUNTIME["image_digest"],
            "base_source_sha256": reasoning_probe._PATCH_BASE_SOURCE,
            "upstream_commit": reasoning_probe._PATCH_COMMIT, "source_sha256": reasoning_probe._PATCH_SOURCE}
        plan.write_text(json.dumps(value))
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
    if patch_state:
        recipe["serve"].update(entrypoint=list(probe._PATCH_ENTRYPOINT),
            flags=[shlex.join(row["Config"]["Cmd"])], env=["ANVIL_SAMPLING_TRACE=1"])
        row["Config"]["Entrypoint"] = ["python3"]
        row["Config"]["Cmd"] = probe._PATCH_ENTRYPOINT[1:] + [probe.MODEL_PATH] + row["Config"]["Cmd"]
        row["Config"]["Env"].append("ANVIL_SAMPLING_TRACE=1")
        row["Config"]["Labels"] = {
            "io.anvil-serving.patch.base-image": value["runtime_patch"]["base_image_digest"],
            "io.anvil-serving.patch.upstream": value["runtime_patch"]["upstream_commit"],
            "io.anvil-serving.patch.source-sha256": "wrong" if patch_state == "bad_labels" else reasoning_probe._PATCH_SOURCE}
        if patch_state == "missing_entrypoint":
            del row["Config"]["Entrypoint"]
        elif patch_state == "wrong_entrypoint":
            row["Config"]["Entrypoint"] = ["/usr/local/bin/lil-entrypoint"]
        elif patch_state == "wrong_command":
            row["Config"]["Cmd"] = row["Config"]["Cmd"][1:]
        elif patch_state == "wrong_flags":
            row["Config"]["Cmd"].append("--enforce-eager")
        elif patch_state == "recipe_entrypoint":
            recipe["serve"]["entrypoint"][1] = "/opt/bypass.py"
        elif patch_state == "recipe_trace":
            recipe["serve"]["env"] = []
        elif patch_state == "trace_missing":
            row["Config"]["Env"].pop()
        elif patch_state == "trace_zero":
            row["Config"]["Env"][-1] = "ANVIL_SAMPLING_TRACE=0"
        elif patch_state == "trace_duplicate":
            row["Config"]["Env"].append("ANVIL_SAMPLING_TRACE=1")
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
            result["files"]["vllm/v1/sample/ops/topk_topp_triton.py"] = {
                "sha256": "wrong" if patch_state == "bad_source" else reasoning_probe._PATCH_SOURCE}
            result["files"].update({"patch/" + name: {"sha256": digest}
                                    for name, digest in probe._PATCH_FILES.items()})
            if patch_state == "bad_helper":
                result["files"]["patch/launch.py"]["sha256"] = "wrong"
        else:
            if drift and len(calls) > 1:
                row["State"]["StartedAt"] = "changed"
            result = [row]
        return subprocess.CompletedProcess(argv, 0, json.dumps(result).encode(), b"")
    if patch_state not in {None, "valid"}:
        with pytest.raises(ValueError, match="mismatch|ambiguous"):
            probe.inspect_runtime(tmp_path / "registry", "candidate", plan, runner=runner)
    elif drift:
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


def _proc_fixture(tmp_path):
    root = tmp_path / "proc"
    (root / "pressure").mkdir(parents=True)
    (root / "pressure/memory").write_text("some avg10=0.00\n")
    (root / "meminfo").write_text("MemTotal: 1024 kB\nMemAvailable: 512 kB\nShmem: 16 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    (root / "vmstat").write_text("pswpin 0\npswpout 0\noom_kill 0\n")
    process = root / "42"
    process.mkdir()
    (process / "status").write_text("Name: worker\nPPid: 1\nVmRSS: 64 kB\n")
    (process / "smaps_rollup").write_text("Pss: 32 kB\n")
    (process / "stat").write_text("42 (worker) " + " ".join(["0"] * 19 + ["100"]))
    (process / "cmdline").write_text("private-argument")
    (process / "environ").write_text("private-environment")
    return root


def test_host_memory_attribution_omits_arguments_and_environment(tmp_path):
    result = probe._collect_host_memory_snapshot(_proc_fixture(tmp_path))
    assert result["host_memory"]["MemAvailable_bytes"] == 512 * 1024
    assert result["largest_processes"] == [{"pid": 42, "parent_pid": 1, "name": "worker",
                                           "rss_bytes": 65536, "pss_bytes": 32768}]
    assert "private-" not in json.dumps(result)
    assert result["process_inventory_incomplete"] is False


@pytest.mark.parametrize("damage", ["missing", "oversized", "malformed"])
def test_host_memory_mandatory_reads_fail_closed(tmp_path, damage):
    root = _proc_fixture(tmp_path)
    path = root / "meminfo"
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("x" * (probe._PROC_READ_BYTES + 1) if damage == "oversized" else "MemTotal: invalid kB\n")
    with pytest.raises((OSError, ValueError)):
        probe._collect_host_memory_snapshot(root)


def test_host_memory_process_cap_is_deterministic_and_explicit(tmp_path, monkeypatch):
    import shutil
    root = _proc_fixture(tmp_path)
    shutil.copytree(root / "42", root / "9")
    monkeypatch.setattr(probe, "_PROCESS_LIMIT", 1)
    result = probe._collect_host_memory_snapshot(root)
    assert [row["pid"] for row in result["largest_processes"]] == [9]
    assert result["process_inventory_truncated"] and result["process_inventory_incomplete"]


def test_host_memory_discards_pss_after_pid_reuse(tmp_path, monkeypatch):
    from pathlib import Path
    import io
    root = _proc_fixture(tmp_path)
    original = Path.open
    reads = 0
    def changing_stat(path, *args, **kwargs):
        nonlocal reads
        if path == root / "42/stat":
            reads += 1
            return io.BytesIO(("42 (worker) " + " ".join(["0"] * 19 + [str(reads)])).encode())
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", changing_stat)
    assert probe._collect_host_memory_snapshot(root)["largest_processes"][0]["pss_bytes"] is None


def test_host_memory_subprocess_has_fixed_program_deadline_and_output_cap():
    def runner(argv, **kwargs):
        assert argv == [probe.sys.executable, "-c", probe._HOST_SNAPSHOT_PROGRAM]
        assert 0 < kwargs["timeout"] <= 1
        assert kwargs["max_output_bytes"] == 256 * 1024
        return subprocess.CompletedProcess(argv, 0, b'{"host_memory": {}}', b"")
    assert probe.host_memory_snapshot(runner=runner, deadline=time.monotonic() + 1) == {"host_memory": {}}
    with pytest.raises(TimeoutError):
        probe.host_memory_snapshot(runner=runner, deadline=time.monotonic() - 1)


def test_host_memory_blocked_child_is_killed_by_deadline(monkeypatch):
    monkeypatch.setattr(probe, "_HOST_SNAPSHOT_PROGRAM", "import time; time.sleep(60)")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        probe.host_memory_snapshot(deadline=started + 0.1)
    assert time.monotonic() - started < 2


def test_resource_deadline_failure_is_sanitized_by_cli(monkeypatch, capsys):
    def failed(*args, **kwargs):
        raise probe.RequestControlError("private-diagnostic-detail")
    monkeypatch.setattr(probe, "inspect_runtime", failed)
    assert probe.main(["--registry", "unused", "--container", "candidate", "--plan", "unused"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "RequestControlError"}
