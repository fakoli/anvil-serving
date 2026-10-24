from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import types

import pytest

from anvil_serving.benchmarking import aider_adapter as adapter


def test_callback_uses_original_globals_without_global_subprocess_patch(tmp_path):
    calls = []
    namespace = {"subprocess": subprocess}
    exec("def callback(command):\n return subprocess.run(command, timeout=180)\n", namespace)
    original = namespace["callback"]
    replacement = adapter.contained_test_callback(original, lambda command, **kw: calls.append((command, kw)))
    replacement(["pytest"])
    assert calls == [(["pytest"], {"timeout": 180})]
    assert original.__globals__["subprocess"] is subprocess
    assert replacement.__code__ is original.__code__
    assert replacement.__globals__["subprocess"].TimeoutExpired is subprocess.TimeoutExpired


def test_loader_rejects_changed_source_before_imports(tmp_path):
    (tmp_path / "benchmark").mkdir()
    (tmp_path / "benchmark/benchmark.py").write_text("raise AssertionError('must never import')")
    with pytest.raises(RuntimeError, match="hash"):
        adapter.load_test_callback(tmp_path)


def test_loader_executes_selected_source_functions_only(tmp_path, monkeypatch):
    source = b'''raise AssertionError("imports must not execute")
def run_unit_tests(original, testdir, history, files):
    return cleanup_test_output(subprocess.run(["pytest"], cwd=testdir, timeout=180).stdout, testdir)
def cleanup_test_output(output, testdir):
    return output.replace(str(testdir), testdir.name)
'''
    (tmp_path / "benchmark").mkdir()
    (tmp_path / "benchmark/benchmark.py").write_bytes(source)
    monkeypatch.setitem(adapter.SOURCE_HASHES, "benchmark/benchmark.py", hashlib.sha256(source).hexdigest())
    original = adapter.load_test_callback(tmp_path)
    contained = adapter.contained_test_callback(original, lambda *args, **kw: subprocess.CompletedProcess(args, 1, str(tmp_path)))
    assert contained(None, tmp_path, None, []) == tmp_path.name


@pytest.mark.parametrize("task", ["/python/exercises/practice/a", "../python/exercises/practice/a",
    "python/exercises/practice/../a", "python\\exercises\\practice\\a", "python/exercises/practice/a/extra"])
def test_task_id_rejects_path_escape(task):
    with pytest.raises(ValueError):
        adapter.validate_task_id(task)


def make_task(root, task="python/exercises/practice/example"):
    target = root / task
    (target / ".meta").mkdir(parents=True)
    (target / ".docs").mkdir()
    (target / ".meta/config.json").write_text(json.dumps({"files": {"solution": ["main.py"], "test": ["test_main.py"]}}))
    (target / ".docs/instructions.md").write_text("Implement the function.")
    (target / "main.py").write_text("pass")
    (target / "test_main.py").write_text("assert False")
    return task


def test_task_snapshot_rejects_symlinks_and_config_escape(tmp_path):
    task = make_task(tmp_path)
    assert len(adapter.task_snapshot(tmp_path, task)) == 4
    file = tmp_path / task / "main.py"
    file.unlink()
    file.symlink_to(tmp_path / task / "test_main.py")
    with pytest.raises(ValueError, match="symlink"):
        adapter.task_snapshot(tmp_path, task)
    file.unlink()
    file.write_text("pass")
    (tmp_path / task / ".meta/config.json").write_text(json.dumps({"files": {"solution": ["../../escape"], "test": ["test_main.py"]}}))
    with pytest.raises(ValueError, match="unsafe"):
        adapter.task_snapshot(tmp_path, task)


def test_dotenv_never_searches_or_loads(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("ambient dotenv lookup")
    dotenv = types.SimpleNamespace(load_dotenv=forbidden, find_dotenv=forbidden)
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    with adapter.disable_dotenv():
        assert dotenv.load_dotenv(override=True) is False
        assert dotenv.find_dotenv() == ""
    assert dotenv.load_dotenv is forbidden


def transport(tmp_path, *, status=200, max_calls=2, max_retries=0):
    recorder = adapter.EventRecorder(tmp_path / "events.jsonl")
    budget = adapter.SuiteBudget({"max_model_calls": max_calls, "max_completion_tokens": 8,
        "deadline_s": 60, "max_retries": max_retries, "min_free_disk_bytes": 0}, recorder)
    sent = []
    class URL:
        path = "/v1/chat/completions"
        def __str__(self):
            return "http://127.0.0.1:8000/v1/chat/completions"
    class Client:
        def send(self, request, **kwargs):
            sent.append(kwargs)
            return types.SimpleNamespace(status_code=status)
    class Model:
        name = "openai/llm.primary"
        def send_completion(self, body=None):
            request = types.SimpleNamespace(method="POST", url=URL(), extensions={},
                content=json.dumps(body or {"model": "llm.primary", "max_tokens": 8}).encode())
            return Client().send(request)
    restore = adapter.install_transport_guard(Client, Model, endpoint={"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        controls={}, budget=budget, recorder=recorder)
    return Model, sent, budget, recorder, restore


def test_actual_http_attempt_cap_and_restore(tmp_path):
    Model, sent, budget, recorder, restore = transport(tmp_path, max_calls=1)
    try:
        Model().send_completion()
        with pytest.raises(adapter.BudgetStopped):
            Model().send_completion()
        assert len(sent) == budget.model_calls == 1
        assert sent[0]["follow_redirects"] is False
    finally:
        restore()
        recorder.close(outcome="failed")


def test_outer_model_retry_is_counted_as_retry(tmp_path):
    Model, sent, budget, recorder, restore = transport(tmp_path, status=503)
    try:
        Model().send_completion()
        with pytest.raises(adapter.BudgetStopped):
            Model().send_completion()
        assert len(sent) == 1
        assert budget.reason == "max_retries"
    finally:
        restore()
        recorder.close(outcome="failed")


def test_alias_substitution_fails_before_send(tmp_path):
    Model, sent, budget, recorder, restore = transport(tmp_path)
    try:
        with pytest.raises(adapter.BudgetStopped, match="alias"):
            Model().send_completion({"model": "other", "max_tokens": 8})
        assert not sent
    finally:
        restore()
        recorder.close(outcome="failed")


def test_generated_tree_cannot_redirect_host_edits_or_test_restore(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("protected")
    adapter.verify_task_tree(task)
    (task / "solution.py").symlink_to(outside)
    with pytest.raises(adapter.BudgetStopped, match="filesystem"):
        adapter.verify_task_tree(task)
    (task / "solution.py").unlink()
    (task / "solution.py").hardlink_to(outside)
    with pytest.raises(adapter.BudgetStopped, match="filesystem"):
        adapter.verify_task_tree(task)
    assert outside.read_text() == "protected"


def test_timeout_cannot_return_an_unsafe_tree_to_host(tmp_path, monkeypatch):
    from anvil_serving.benchmarking import aider_containment
    task = tmp_path / "task"
    task.mkdir()
    plan = {"run_path": str(tmp_path), "ownership_id": "owner", "run_id": "run",
            "aider_environment": {"image_id": "sha256:" + "a" * 64}, "container_executable": "docker"}
    budget = types.SimpleNamespace(check=lambda: 180)
    def timed_out(command, **kwargs):
        (task / "escape").symlink_to(tmp_path)
        raise subprocess.TimeoutExpired(command, 180)
    monkeypatch.setattr(aider_containment, "run_contained_test", timed_out)
    with pytest.raises(adapter.BudgetStopped, match="filesystem"):
        adapter.run_contained_callback(["pytest"], plan=plan, budget=budget, cwd=task, timeout=180)
    (task / "escape").unlink()
    def clean_timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 180)
    monkeypatch.setattr(aider_containment, "run_contained_test", clean_timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        adapter.run_contained_callback(["pytest"], plan=plan, budget=budget, cwd=task, timeout=180)


def test_model_paths_are_guarded_before_preview_reads(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    solution = results / "main.py"
    solution.write_text("pass")
    coder = types.SimpleNamespace(abs_fnames={str(solution)}, abs_root_path=lambda path: str(results / path))
    adapter.guard_solution_path(coder, "main.py", results)
    with pytest.raises(adapter.BudgetStopped, match="admitted"):
        adapter.guard_solution_path(coder, "../outside", results)
    solution.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    results.rmdir()
    results.symlink_to(outside, target_is_directory=True)
    (outside / "main.py").write_text("protected")
    with pytest.raises(adapter.BudgetStopped, match="admitted"):
        adapter.guard_solution_path(coder, "main.py", results)


def test_lint_keeps_upstream_argv_but_executes_only_inside_image(tmp_path, monkeypatch):
    calls = []
    def contained(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, "lint diagnostics", None)
    monkeypatch.setattr(adapter, "run_contained_callback", contained)
    command = [sys.executable, "-m", "flake8", "--select=E9", "--show-source", "--isolated", "main.py"]
    result = adapter.run_contained_lint(command, plan={"image": "pinned"}, budget="bounded", cwd=tmp_path)
    assert calls[0][0] == ["python3", "-I", *command[1:]]
    assert calls[0][1]["timeout"] == 180
    assert result.stdout == "lint diagnostics" and result.stderr == ""
    with pytest.raises(adapter.BudgetStopped, match="lint command"):
        adapter.run_contained_lint(["sh", "-c", "untrusted"], plan={}, budget=None, cwd=tmp_path)
