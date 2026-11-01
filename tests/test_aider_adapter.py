from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import types

import pytest

from anvil_serving.benchmarking import aider_adapter as adapter


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


def offline_model_client(*, status=200, response_content=b"{}", send_effect=None):
    sent = []
    class URL:
        path = "/v1/chat/completions"
        def __str__(self):
            return "http://127.0.0.1:8000/v1/chat/completions"
    class Client:
        def send(self, request, **kwargs):
            sent.append(kwargs)
            if send_effect is not None:
                return send_effect()
            return types.SimpleNamespace(status_code=status, content=response_content)
    class Model:
        name = "openai/llm.primary"
        def __init__(self, name="openai/llm.primary", **kwargs):
            self.name = name
        def send_completion(self, body=None):
            request = types.SimpleNamespace(method="POST", url=URL(), extensions={},
                content=json.dumps(body or {"model": "llm.primary", "max_tokens": 8}).encode())
            return Client().send(request)
    return Model, Client, sent


def transport(tmp_path, *, status=200, max_calls=2, max_retries=0, response_content=b"{}", send_effect=None):
    recorder = adapter.EventRecorder(tmp_path / "events.jsonl")
    budget = adapter.SuiteBudget({"max_model_calls": max_calls, "max_completion_tokens": 8,
        "deadline_s": 60, "max_retries": max_retries, "min_free_disk_bytes": 0}, recorder)
    Model, Client, sent = offline_model_client(status=status, response_content=response_content, send_effect=send_effect)
    restore = adapter.install_transport_guard(Client, Model, endpoint={"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        controls={}, budget=budget, recorder=recorder)
    return Model, sent, budget, recorder, restore


def test_alias_substitution_fails_before_send(tmp_path):
    Model, sent, budget, recorder, restore = transport(tmp_path)
    try:
        with pytest.raises(adapter.BudgetStopped, match="alias"):
            Model().send_completion({"model": "other", "max_tokens": 8})
        assert not sent
    finally:
        restore()
        recorder.close(outcome="failed")


@pytest.mark.parametrize("reason", ["length", "stop", "private-response-text"])
def test_response_diagnostics_allowlist_preserves_response_and_usage(tmp_path, reason):
    content = json.dumps({"choices": [{"finish_reason": reason, "message": {"content": "private-response-text"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 8, "total_tokens": 19,
                  "completion_tokens_details": {"reasoning_tokens": 0, "private": "private-response-text"}},
        "private": "private-response-text"}).encode()
    Model, sent, budget, recorder, restore = transport(tmp_path, response_content=content)
    try:
        response = Model().send_completion()
        assert response.content == content and len(sent) == budget.model_calls == 1
    finally:
        restore()
        recorder.close(outcome="completed")
    text = (tmp_path / "events.jsonl").read_text()
    event = next(row for row in map(json.loads, text.splitlines()) if row["event"] == "aider_transport_response")
    assert "private-response-text" not in text
    assert event["finish_reason"] == (reason if reason != "private-response-text" else None)
    assert event["output_truncated"] == (reason == "length" if reason != "private-response-text" else None)
    assert event["usage"] == {"prompt_tokens": 11, "completion_tokens": 8, "total_tokens": 19, "reasoning_tokens": 0}
    assert event["attempt"] == 1


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


@pytest.mark.parametrize("mode,language,output,signal", [
    ("compiler", "rust", "error[E0594]: private-output\nerror[E0594]: duplicate\n", "rust_error_code"),
    ("compiler", "rust", "error: could not compile `private-output` due to previous error\n", "rust_could_not_compile"),
    ("compiler", "rust", "error: expected expression, found `private-output`\n", "rust_syntax_error"),
    ("compiler", "rust", "error: linking with `private-output` failed: exit status: 1\n", "rust_linker_error"),
    ("compiler", "go", "# private-output\nFAIL\tprivate-output [build failed]\n", "go_build_failed"),
    ("test_failure", "go", "--- FAIL: TestExample\nFAIL\tprivate-output\n", None),
    ("test_failure", "rust", "assertion failed: private-output", None),
    ("timeout", "rust", "private-output", None),
    ("infrastructure", "rust", "private-output", None),
])
def test_contained_diagnostics_preserve_result_and_exception_without_output(tmp_path, monkeypatch, mode, language, output, signal):
    from anvil_serving.benchmarking import aider_containment
    task = tmp_path / f"results/{language}/exercises/practice/example"
    task.mkdir(parents=True)
    recorder = adapter.EventRecorder(tmp_path / "events.jsonl")
    budget = types.SimpleNamespace(check=lambda: 180, recorder=recorder)
    plan = {"run_path": str(tmp_path), "ownership_id": "owner", "run_id": "run",
            "aider_environment": {"image_id": "sha256:" + "a" * 64}, "container_executable": "docker"}
    completed = subprocess.CompletedProcess(["cargo", "test"], 1, output, None)
    error = subprocess.TimeoutExpired(["cargo", "test"], 180, output="private-output") if mode == "timeout" else RuntimeError("private-output")
    def contained(*args, **kwargs):
        if mode in {"timeout", "infrastructure"}:
            raise error
        return completed
    monkeypatch.setattr(aider_containment, "run_contained_test", contained)
    try:
        if mode in {"timeout", "infrastructure"}:
            with pytest.raises(type(error)) as caught:
                adapter.run_contained_callback(["cargo", "test"], plan=plan, budget=budget, cwd=task, timeout=180)
            assert caught.value is error
        else:
            assert adapter.run_contained_callback(["cargo", "test"], plan=plan, budget=budget, cwd=task, timeout=180) is completed
    finally:
        recorder.close(outcome="completed")
    text = (tmp_path / "events.jsonl").read_text()
    event = next(row for row in map(json.loads, text.splitlines()) if row["event"] == "aider_contained_command_finished")
    assert "private-output" not in text
    assert event["returncode"] == (1 if mode in {"compiler", "test_failure"} else None)
    assert event["timed_out"] == (None if mode == "infrastructure" else mode == "timeout")
    assert event["rust_compiler_error_codes"] == (["E0594"] if signal == "rust_error_code" else None)
    assert event["compilation_failure"] is (True if signal else None)
    assert event["compilation_failure_signals"] == ([signal] if signal else None)
    assert event["compiler_diagnostics_truncated"] is (False if mode in {"compiler", "test_failure"} else None)


def cpp_lint_fixture(tmp_path):
    """Small upstream API double; actual pinned parser is also checked offline."""
    from pathlib import Path
    source, results = tmp_path / "source", tmp_path / "results"
    task = "cpp/exercises/practice/example"
    original = source / task / ".meta/config.json"
    original.parent.mkdir(parents=True)
    original.write_text(json.dumps({"files": {"solution": ["main.h", "mixed.c"]}}))
    names = [task + "/main.h", task + "/mixed.c", task + "/test.h",
             "cpp/exercises/practice/unselected/main.h", "python/exercises/practice/example/main.h"]
    for name in names:
        file = results / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("source")
    plan = {"paths": {"results": str(results), "polyglot_root": str(source)},
            "selection": {"instance_ids": [task], "files": {task: {
                ".meta/config.json": hashlib.sha256(original.read_bytes()).hexdigest(),
                "main.h": "bound-source", "mixed.c": "bound-source"}}}}
    namespace = {"filename_to_lang": lambda name: "c"}
    exec("def basic_lint(fname, code):\n return (filename_to_lang(fname), fname, code)\n", namespace)
    basic = namespace["basic_lint"]
    class Linter:
        def __init__(self):
            self.languages = {}
            self.all_lint_cmd = None
        def set_linter(self, language, command):
            self.languages[language] = command
        def lint(self, fname, cmd=None):
            command = cmd or self.all_lint_cmd or self.languages.get("c")
            if callable(command):
                return command(fname, str(fname), Path(fname).read_text())
            return command or basic(str(fname), Path(fname).read_text())
    return plan, types.SimpleNamespace(Linter=Linter, basic_lint=basic), [results / name for name in names]


def test_cpp_header_lint_scopes_original_solution_paths_and_restores(tmp_path):
    plan, upstream, paths = cpp_lint_fixture(tmp_path)
    linter = upstream.Linter()
    original, mapping = upstream.Linter.lint, linter.languages
    with adapter.cpp_header_lint(plan, upstream):
        assert linter.lint(paths[0]) == ("cpp", str(paths[0]), "source")
        assert linter.languages is mapping
        for other in paths[1:]:
            assert linter.lint(other) == ("c", str(other), "source")
        assert upstream.basic_lint(str(paths[0]), "source")[0] == "c"
    assert upstream.Linter.lint is original
    assert linter.lint(paths[0])[0] == "c"


def test_simultaneous_admission_is_atomic_and_response_keeps_its_owner(tmp_path):
    entered, release, start = threading.Event(), threading.Event(), threading.Barrier(3)
    failures = []
    rejected = threading.Event()
    def delayed():
        entered.set()
        assert release.wait(5)
        return types.SimpleNamespace(status_code=200, content=b"{}")
    Model, sent, _budget, recorder, guard = transport(tmp_path, send_effect=delayed)
    def call():
        start.wait()
        try:
            Model().send_completion()
        except BaseException as exc:
            failures.append(exc)
            rejected.set()
    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        start.wait()
        assert entered.wait(5) and rejected.wait(5)
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
        guard()
        recorder.close(outcome="failed", request_accounting=guard.accounting())
    assert len(sent) == 1
    assert len(failures) == 1 and isinstance(failures[0], adapter.BudgetStopped)
    rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
    request = next(row for row in rows if row["event"] == "aider_transport_started")
    response = next(row for row in rows if row["event"] == "aider_transport_response")
    assert (request["logical_request_id"], request["attempt"]) == (response["logical_request_id"], response["attempt"])


def test_retry_context_does_not_leak_between_threads(tmp_path):
    Model, sent, _budget, recorder, guard = transport(tmp_path, status=503, max_calls=3, max_retries=1)
    failures = []
    def separate_request():
        try:
            Model().send_completion()
        except BaseException as exc:
            failures.append(exc)
    try:
        Model().send_completion()
        thread = threading.Thread(target=separate_request)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        Model().send_completion()  # Main thread retains its own failed-call retry.
    finally:
        guard()
        recorder.close(outcome="failed", request_accounting=guard.accounting())
    assert not failures and len(sent) == 3
    rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
    starts = [row for row in rows if row["event"] == "aider_transport_started"]
    assert [row["attempt"] for row in starts] == [1, 1, 2]
    assert starts[0]["logical_request_id"] == starts[2]["logical_request_id"] != starts[1]["logical_request_id"]


# Verbatim Coder methods from Aider 5dc9490bb35f9729ef2c95d00a19ccd30c26339c,
# aider/coders/base_coder.py:1002-1034 (full file is verified by SOURCE_HASHES).
_PINNED_SUMMARY_METHODS = '    def summarize_start(self):\n        if not self.summarizer.too_big(self.done_messages):\n            return\n\n        self.summarize_end()\n\n        if self.verbose:\n            self.io.tool_output("Starting to summarize chat history.")\n\n        self.summarizer_thread = threading.Thread(target=self.summarize_worker)\n        self.summarizer_thread.start()\n\n    def summarize_worker(self):\n        self.summarizing_messages = list(self.done_messages)\n        try:\n            self.summarized_done_messages = self.summarizer.summarize(self.summarizing_messages)\n        except ValueError as err:\n            self.io.tool_warning(err.args[0])\n\n        if self.verbose:\n            self.io.tool_output("Finished summarizing chat history.")\n\n    def summarize_end(self):\n        if self.summarizer_thread is None:\n            return\n\n        self.summarizer_thread.join()\n        self.summarizer_thread = None\n\n        if self.summarizing_messages == self.done_messages:\n            self.done_messages = self.summarized_done_messages\n        self.summarizing_messages = None\n        self.summarized_done_messages = []\n\n'


@pytest.mark.parametrize("final_response", ["success", "error", "deadline", "budget_stop"])
def test_run_adapter_joins_pinned_final_summary_before_closing(tmp_path, monkeypatch, final_response):
    from contextlib import nullcontext
    namespace = {"threading": threading}
    exec("class Coder:\n" + _PINNED_SUMMARY_METHODS, namespace)
    Coder = namespace["Coder"]
    Coder.allowed_to_edit = lambda *args: None
    original_start = Coder.summarize_start
    original_worker = Coder.summarize_worker
    original_end = Coder.summarize_end
    entered, release = threading.Event(), threading.Event()
    summary_started = threading.Event()
    failures, coders, captured_guards = [], [], []
    now = [100.0]
    if final_response == "deadline":
        monkeypatch.setattr(adapter.time, "monotonic", lambda: now[0])
    def send_effect():
        if len(sent) == 3:
            entered.set()
            assert release.wait(5)
            if final_response == "error":
                raise ValueError("private failure text")
        return types.SimpleNamespace(status_code=200, content=b'{"choices":[{"finish_reason":"stop"}]}')
    Model, Client, sent = offline_model_client(send_effect=send_effect)
    original_send, original_completion = Client.send, Model.send_completion
    models = types.SimpleNamespace(Model=Model, model_info_manager=types.SimpleNamespace(_update_cache=lambda: None))
    Linter = type("Linter", (), {"flake8_lint": lambda *args: None})
    WholeFileCoder = type("WholeFileCoder", (), {"do_live_diff": lambda *args: None})
    modules = {"httpx": types.SimpleNamespace(Client=Client),
               "aider": types.SimpleNamespace(linter=types.SimpleNamespace(), models=models),
               "aider.coders.base_coder": types.SimpleNamespace(Coder=Coder),
               "aider.linter": types.SimpleNamespace(Linter=Linter),
               "aider.coders.wholefile_coder": types.SimpleNamespace(WholeFileCoder=WholeFileCoder)}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    benchmark = {}
    exec("def run_test_real(): pass\ndef run_unit_tests(*args): pass", benchmark)
    def app(**kwargs):
        model = benchmark["models"].Model("openai/llm.primary")
        model.send_completion()
        model.send_completion()
        coder = Coder()
        coder.summarizer_thread = None
        coder.verbose = False
        coder.done_messages = ["history"]
        coder.summarizing_messages = None
        coder.summarized_done_messages = []
        coder.io = types.SimpleNamespace(tool_warning=lambda _text: None)
        def summarize(_messages):
            summary_started.set()
            model.send_completion()
            return ["summary"]
        coder.summarizer = types.SimpleNamespace(too_big=lambda _messages: True, summarize=summarize)
        coders.append(coder)
        coder.summarize_start()
        expected_start = summary_started if final_response == "budget_stop" else entered
        assert expected_start.wait(5)
        # Like pinned benchmark.py, return after grading without summarize_end().
        if final_response == "deadline":
            now[0] += 61
    benchmark["app"] = app
    install = adapter.install_transport_guard
    def capture_guard(*args, **kwargs):
        guard = install(*args, **kwargs)
        captured_guards.append(guard)
        return guard
    monkeypatch.setattr(adapter, "install_transport_guard", capture_guard)
    monkeypatch.setattr(adapter, "verify_source", lambda _root: tmp_path)
    monkeypatch.setattr(adapter, "disable_dotenv", nullcontext)
    monkeypatch.setattr(adapter, "cpp_header_lint", lambda *_args: nullcontext())
    monkeypatch.setattr(adapter, "contained_test_callback", lambda original, replacement: original)
    monkeypatch.setattr(adapter.runpy, "run_path", lambda *args, **kwargs: benchmark)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("AIDER_DOCKER", "fixture-original")
    plan = {"paths": {"aider_root": str(tmp_path), "request_events": str(tmp_path / "events.jsonl"),
                      "adapter_result": str(tmp_path / "result.json"), "results": str(tmp_path / "results"),
                      "aider": str(tmp_path)},
            "budgets": {"max_model_calls": 2 if final_response == "budget_stop" else 12,
                        "max_completion_tokens": 8,
                        "deadline_s": 60, "max_retries": 0, "min_free_disk_bytes": 0},
            "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
            "request_controls": {}, "plan_sha256": "a" * 64, "adapter_sha256": "b" * 64}
    # Release only after shutdown has entered the bounded *actual* summary join.
    observed_join = threading.Event()
    def watch_join():
        expected_start = summary_started if final_response == "budget_stop" else entered
        assert expected_start.wait(5)
        thread = coders[0].summarizer_thread
        original_join = thread.join
        def join(timeout=None):
            observed_join.set()
            if final_response != "deadline":
                release.set()
            return original_join(timeout=timeout)
        thread.join = join
    # Install the join observer synchronously as app returns, avoiding scheduling races.
    actual_app = benchmark["app"]
    benchmark["app"] = lambda **kwargs: (actual_app(**kwargs), watch_join())
    monkeypatch.setattr(threading, "excepthook", lambda args: failures.append(args.exc_value))
    try:
        if final_response in ("deadline", "budget_stop"):
            with pytest.raises(adapter.BudgetStopped):
                adapter.run_adapter(plan)
        else:
            adapter.run_adapter(plan)
        assert observed_join.is_set()
        result = json.loads((tmp_path / "result.json").read_text())
        rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
        assert rows[0]["request_accounting_schema"] == adapter.REQUEST_ACCOUNTING_SCHEMA
        assert rows[-1]["request_accounting"] == result["request_accounting"]
        accounting = result["request_accounting"]
        assert accounting["started"] == (2 if final_response == "budget_stop" else 3)
        if final_response == "deadline":
            assert result["outcome"] == "budget_exhausted" and result["events_complete"] is False
            assert rows[-1]["status"] == accounting["status"] == "incomplete"
            assert accounting["responses"] == 2 and accounting["pending"] == 1
            assert Client.send is not original_send  # Still fences late retries.
            release.set()
            coders[0].summarizer_thread.join(5)
            with pytest.raises(adapter.BudgetStopped):
                Model().send_completion()
            assert len(sent) == 3
            assert (tmp_path / "events.jsonl").read_text().splitlines() == [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]
        elif final_response == "budget_stop":
            assert result["outcome"] == "budget_exhausted" and result["events_complete"] is True
            assert result["budget"]["reason"] == "max_model_calls"
            assert accounting == {"schema": adapter.REQUEST_ACCOUNTING_SCHEMA,
                                 "status": "complete", "started": 2, "responses": 2,
                                 "errors": 0, "pending": 0}
            assert len(sent) == 2
            assert coders[0].summarizer_thread is None
            assert coders[0].done_messages == ["history"]
            assert Client.send is original_send and Model.send_completion is original_completion
            assert Coder.summarize_start is original_start
            assert Coder.summarize_worker is original_worker
            assert Coder.summarize_end is original_end
            assert not failures
        else:
            assert result["outcome"] == "completed" and result["events_complete"] is True
            assert accounting["pending"] == 0 and accounting["status"] == "complete"
            assert accounting["responses"] == (3 if final_response == "success" else 2)
            assert accounting["errors"] == (1 if final_response == "error" else 0)
            assert coders[0].summarizer_thread is None
            assert Client.send is original_send and Model.send_completion is original_completion
            assert Coder.summarize_start is original_start
            assert Coder.summarize_worker is original_worker
            assert Coder.summarize_end is original_end
            assert not failures
        assert "private failure text" not in (tmp_path / "events.jsonl").read_text()
    finally:
        release.set()
        for coder in coders:
            if coder.summarizer_thread is not None:
                coder.summarizer_thread.join(5)
        # Only local synthetic classes: undo deadline fencing for fixture cleanup.
        Client.send, Model.send_completion = original_send, original_completion
