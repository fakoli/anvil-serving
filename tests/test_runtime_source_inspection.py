import json
import subprocess

import pytest

from anvil_serving import runtime_source_inspection as subject


@pytest.fixture
def scenario(tmp_path, monkeypatch, capsys):
    plan = {"schema": "anvil-serving.runtime-source-inspection/v1", "profile": "vllm-accounting",
            "model": "example/model", "revision": "revision-1", "image_id": "sha256:" + "b" * 64,
            "recipe_digest": "c" * 64, "container_id": "a" * 64,
            "started_at": "2026-01-02T03:04:05.123456789Z"}
    path = tmp_path / "plan.json"
    recipe = {"model": plan["model"], "download": {"revision": plan["revision"]}}
    monkeypatch.setattr(subject.serve_recipes, "load_registry", lambda _: {"recipe": [recipe]})
    monkeypatch.setattr(subject.serve_recipes, "recipe_digest", lambda _: "c" * 64)
    monkeypatch.setattr(subject, "_recipe_container_identity", lambda *a, **k: {
        "container_id": plan["container_id"], "running": True, "image_id": plan["image_id"], "revision": plan["revision"]})
    capture = {"files": {name: {"bytes": 7, "sha256": "d" * 64} for name in subject.FILES}, "package_version": "1"}
    rows = [{"Id": plan["container_id"], "Image": plan["image_id"],
             "State": {"Running": True, "StartedAt": plan["started_at"]}}]
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        assert 0 < kwargs["timeout"] <= 45 and kwargs["max_output_bytes"] == 2 * 1024 * 1024
        return subprocess.CompletedProcess(argv, 0, json.dumps(capture if argv[1] == "exec" else rows).encode(), b"")
    def execute():
        path.write_text(json.dumps(plan))
        monkeypatch.setattr(subject, "_run_bounded_process", runner)
        # Default parameters are bound on definition: exercise actual CLI handler with the injected runner.
        original = subject.inspect_runtime
        monkeypatch.setattr(subject, "inspect_runtime", lambda r, c, p: original(r, c, p, runner=runner))
        rc = subject.main(["--registry", "registry", "--container", "example", "--plan", str(path)])
        return rc, json.loads(capsys.readouterr().out)
    return plan, rows, capture, calls, execute


def test_cli_captures_fixed_manifest_without_inference(scenario):
    plan, rows, capture, calls, execute = scenario
    rc, result = execute()
    assert rc == 0 and result["inference_requests"] == 0
    assert result["identity"] == plan and set(result["capture"]["files"]) == set(subject.FILES)
    assert [x[1] for x in calls] == ["inspect", "exec", "inspect"]
    assert calls[1][-1] == subject.CAPTURE and plan["container_id"] in calls[1]
    assert "import vllm" not in subject.CAPTURE and "torch" not in subject.CAPTURE


@pytest.mark.parametrize("field,value", [("profile", "other"), ("container_id", "bad"),
    ("started_at", "bad"), ("recipe_digest", "0" * 64), ("model", "other")])
def test_bad_plan_or_recipe_has_no_exec(scenario, field, value):
    plan, _, _, calls, execute = scenario
    plan[field] = value
    rc, _ = execute()
    assert rc == 1 and not any(c[1] == "exec" for c in calls)


@pytest.mark.parametrize("change", ["id", "start", "image", "stopped"])
def test_wrong_generation_is_refused_before_capture(scenario, change):
    _, rows, _, calls, execute = scenario
    row = rows[0]
    if change == "id": row["Id"] = "e" * 64
    if change == "start": row["State"]["StartedAt"] = "2026-01-02T03:04:06Z"
    if change == "image": row["Image"] = "sha256:" + "e" * 64
    if change == "stopped": row["State"]["Running"] = False
    rc, _ = execute()
    assert rc == 1 and not any(c[1] == "exec" for c in calls)


@pytest.mark.parametrize("change", ["missing", "extra", "oversize", "hash"])
def test_malformed_capture_fails(scenario, change):
    _, _, capture, _, execute = scenario
    if change == "missing": capture["files"].pop(subject.FILES[0])
    if change == "extra": capture["files"]["../outside"] = {"bytes": 1, "sha256": "a" * 64}
    if change == "oversize": capture["files"][subject.FILES[0]]["bytes"] = 2 * 1024 * 1024 + 1
    if change == "hash": capture["files"][subject.FILES[0]]["sha256"] = "wrong"
    assert execute()[0] == 1


def test_restart_after_capture_refused(scenario, monkeypatch, capsys, tmp_path):
    plan, rows, capture, calls, _ = scenario
    path = tmp_path / "p.json"
    path.write_text(json.dumps(plan))
    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "exec":
            rows[0]["State"]["StartedAt"] = "2026-01-02T03:04:06Z"
            value = capture
        else: value = rows
        return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode(), b"")
    with pytest.raises(ValueError, match="generation"):
        subject.inspect_runtime("registry", "example", path, runner=runner)


def test_fixed_capture_reads_regular_files_and_rejects_symlink(tmp_path):
    import os
    import sys
    root = tmp_path / "vllm"
    root.mkdir()
    (root / "__init__.py").write_text("")
    for name in subject.FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source")
    metadata = tmp_path / "vllm-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: vllm\nVersion: 1.0\n")
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(tmp_path)}
    def run():
        return subprocess.run([sys.executable, "-B", "-c", subject.CAPTURE], env=env,
                              capture_output=True, text=True, timeout=10)
    ok = run()
    assert ok.returncode == 0 and set(json.loads(ok.stdout)["files"]) == set(subject.FILES)
    path = root / subject.FILES[0]
    path.unlink()
    path.symlink_to(root / subject.FILES[1])
    assert run().returncode != 0
    path.unlink()
    assert run().returncode != 0


def test_command_manifest_has_read_only_inspector():
    from anvil_serving.commands import render_manifest
    manifest = json.loads(render_manifest())
    row = next(item for item in manifest["commands"] if item["path"] == "eval runtime-source-inspect")
    assert row["mutation_class"] == "read"


def test_cli_dispatch_to_handler_rejects_invalid_plan_before_process(tmp_path, monkeypatch, capsys):
    from anvil_serving import cli
    path = tmp_path / "plan.json"
    path.write_text('{"profile":"wrong"}')
    def forbidden(*args, **kwargs):
        pytest.fail("invalid plan reached process runner")
    original = subject.inspect_runtime
    monkeypatch.setattr(subject, "inspect_runtime", lambda r, c, p: original(r, c, p, runner=forbidden))
    result = cli.main(["eval", "runtime-source-inspect", "--registry", "unused", "--container", "example", "--plan", str(path), "--transport", "local"])
    assert result != 0
    assert "ValueError" in capsys.readouterr().out


def test_cli_dispatch_runs_bound_capture(scenario, tmp_path, monkeypatch, capsys):
    from anvil_serving import cli
    plan, rows, capture, calls, _ = scenario
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(capture if argv[1] == "exec" else rows).encode(), b"")
    original = subject.inspect_runtime
    monkeypatch.setattr(subject, "inspect_runtime", lambda r, c, p: original(r, c, p, runner=runner))
    assert cli.main(["eval", "runtime-source-inspect", "--registry", "unused", "--container", "example", "--plan", str(path), "--transport", "local"]) == 0
    assert '"inference_requests": 0' in capsys.readouterr().out
    assert [call[1] for call in calls] == ["inspect", "exec", "inspect"]


def test_cli_real_owner_identity_chain_blocks_wrong_label(tmp_path, monkeypatch, capsys):
    from anvil_serving import cli, models
    plan = {"schema": "anvil-serving.runtime-source-inspection/v1", "profile": "vllm-accounting",
            "model": "example/model", "revision": "rev", "image_id": "sha256:" + "b" * 64,
            "recipe_digest": "c" * 64, "container_id": "a" * 64, "started_at": "2026-01-02T03:04:05Z"}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    recipe = {"model": plan["model"], "download": {"revision": "rev"}, "serve": {}}
    monkeypatch.setattr(subject.serve_recipes, "load_registry", lambda _: {"recipe": [recipe]})
    monkeypatch.setattr(subject.serve_recipes, "recipe_digest", lambda _: "c" * 64)
    monkeypatch.setattr(subject.serve_recipes.recipe_memory, "observation", lambda *a, **k: {})
    monkeypatch.setattr(subject.serve_recipes.recipe_memory, "containment_error", lambda *a, **k: None)
    labels = {models._RECIPE_MANAGED_LABEL: "models-recipes", models._RECIPE_MODEL_LABEL: "wrong/model",
              models._RECIPE_REVISION_LABEL: "rev", subject.serve_recipes.RECIPE_DIGEST_LABEL: "c" * 64}
    row = {"Id": plan["container_id"], "Image": plan["image_id"], "Config": {"Labels": labels},
           "State": {"Running": True, "StartedAt": plan["started_at"]}}
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps([row]).encode(), b"")
    original = subject.inspect_runtime
    monkeypatch.setattr(subject, "inspect_runtime", lambda r, c, p: original(r, c, p, runner=runner))
    assert cli.main(["eval", "runtime-source-inspect", "--registry", "x", "--container", "example", "--plan", str(path), "--transport", "local"]) != 0
    assert [c[1] for c in calls] == ["inspect"]
