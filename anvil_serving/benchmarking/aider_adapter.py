"""Hash-guarded Aider bridge; imported dependencies live only in its prepared venv."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import types
from urllib.parse import urlsplit

from .swe_agent_adapter import BudgetStopped, EventRecorder, SuiteBudget
from .aider_containment import (
    AIDER_REVISION as AIDER_REVISION,
    POLYGLOT_REVISION as POLYGLOT_REVISION,
    SOURCE_HASHES as SOURCE_HASHES,
    LANGUAGES as LANGUAGES,
    validate_task_id as validate_task_id,
    task_snapshot as task_snapshot,
    load_test_callback as load_test_callback,
    contained_test_callback as contained_test_callback,
)

_COMPILER_DIAGNOSTIC_PATTERNS = {
    "rust": {
        "rust_could_not_compile": r"(?m)^error: could not compile `[^`\r\n]+`",
        "rust_syntax_error": r"(?m)^error: (?:expected |unexpected |mismatched closing delimiter|unclosed delimiter|unterminated )",
        "rust_linker_error": r"(?m)^error: linking with [^\r\n]+ failed:",
    },
    "go": {"go_build_failed": r"(?m)^FAIL[ \t]+[^\r\n]+[ \t]+\[build failed\][ \t]*\r?$"},
}


def verify_source(root, *, runner=subprocess.run):
    root = Path(root).resolve(strict=True)
    for args, expected in ((["rev-parse", "HEAD"], AIDER_REVISION),
                           (["status", "--porcelain", "--untracked-files=all"], "")):
        result = runner(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
        observed = result.stdout.strip()
        if args[0] == "status":
            observed = "\n".join(line for line in observed.splitlines() if line != "?? .anvil-harness-lock.json")
        if result.returncode or observed != expected:
            raise RuntimeError("Aider source checkout identity changed")
    for relative, expected in SOURCE_HASHES.items():
        path = root / relative
        if (path.is_symlink() or not path.resolve(strict=True).is_relative_to(root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise RuntimeError("pinned Aider source hash changed")
    return root


def verify_task_tree(task):
    """Generated code must not redirect the next host-side restore or edit."""
    task = Path(task)
    for path in [task, *task.rglob("*")]:
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)) or (stat.S_ISREG(mode) and path.stat().st_nlink != 1):
            raise BudgetStopped("unsafe generated Aider task filesystem")


def guard_solution_path(coder, path, results_root):
    candidate = Path(coder.abs_root_path(path))
    if (candidate.is_symlink() or not candidate.resolve().is_relative_to(results_root)
            or str(candidate) not in coder.abs_fnames):
        raise BudgetStopped("Aider access outside admitted solution files")
    verify_task_tree(candidate.parent)


@contextmanager
def cpp_header_lint(plan, upstream):
    """Select C++ only for admitted C++ solution headers, retaining pinned lint logic."""
    results = Path(plan["paths"]["results"]).resolve()
    headers = {}
    for task_id in plan["selection"]["instance_ids"]:
        if not validate_task_id(task_id).startswith("cpp/"):
            continue
        config_path = Path(plan["paths"]["polyglot_root"]) / task_id / ".meta/config.json"
        raw = config_path.read_bytes()
        files = plan["selection"]["files"][task_id]
        if hashlib.sha256(raw).hexdigest() != files[".meta/config.json"]:
            raise BudgetStopped("Aider solution metadata changed")
        for name in json.loads(raw)["files"]["solution"]:
            if name in files and Path(name).suffix == ".h":
                headers[results / task_id / name] = results / task_id

    # Reuse the hash-guarded parser, error traversal and exception handling unchanged.
    namespace = dict(upstream.basic_lint.__globals__, filename_to_lang=lambda _fname: "cpp")
    cpp_lint = types.FunctionType(upstream.basic_lint.__code__, namespace,
                                 upstream.basic_lint.__name__, upstream.basic_lint.__defaults__,
                                 upstream.basic_lint.__closure__)
    original = upstream.Linter.lint

    def lint(self, fname, cmd=None):
        candidate = Path(os.path.abspath(fname))
        if candidate not in headers or cmd or self.all_lint_cmd or "c" in self.languages:
            return original(self, fname, cmd)
        if candidate.resolve() != candidate:
            raise BudgetStopped("unsafe generated Aider header path")
        verify_task_tree(headers[candidate])
        languages = self.languages
        self.languages = dict(languages)
        try:
            self.set_linter("c", lambda _fname, rel_fname, code: cpp_lint(rel_fname, code))
            return original(self, fname, cmd)
        finally:
            self.languages = languages

    upstream.Linter.lint = lint
    try:
        yield
    finally:
        upstream.Linter.lint = original


@contextmanager
def disable_dotenv():
    import dotenv  # Importing the library does not search or read environment files.
    old_load, old_find = dotenv.load_dotenv, dotenv.find_dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    dotenv.find_dotenv = lambda *args, **kwargs: ""
    try:
        yield
    finally:
        dotenv.load_dotenv, dotenv.find_dotenv = old_load, old_find


def response_diagnostics(response):
    """Observe bounded protocol metadata without retaining generated text."""
    result = {"finish_reason": None, "output_truncated": None,
              "usage": dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"))}
    try:
        if not 200 <= response.status_code < 300 or len(response.content) > 8 * 1024 * 1024:
            return result
        body = json.loads(response.content)
        choices = body.get("choices")
        if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
            reason = choices[0].get("finish_reason")
            if reason in ("stop", "length", "tool_calls", "content_filter", "function_call"):
                result.update(finish_reason=reason, output_truncated=reason == "length")
        usage = body.get("usage")
        if isinstance(usage, dict):
            details = usage.get("completion_tokens_details")
            values = dict(usage)
            reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
            if type(reasoning) is int and 0 <= reasoning <= 10**12:
                values["reasoning_tokens"] = reasoning
            for name in result["usage"]:
                value = values.get(name)
                if type(value) is int and 0 <= value <= 10**12:
                    result["usage"][name] = value
    except (AttributeError, TypeError, ValueError, RecursionError):
        pass  # Missing/malformed metadata must not change transport or SDK retries.
    return result


def install_transport_guard(client, model_class, *, endpoint, controls, budget, recorder):
    """Count actual synchronous HTTP attempts, including provider and SDK retries."""
    original_send, original_completion = client.send, model_class.send_completion
    active = None
    retry_context = None
    target = urlsplit(endpoint["base_url"] + "/chat/completions")

    def completion(self, *args, **kwargs):
        nonlocal active, retry_context
        if self.name != "openai/" + endpoint["model"] or active is not None:
            raise BudgetStopped("unexpected Aider model or nested request")
        active = retry_context or {"sdk_transport_attempt_ordinal": 0,
                  "logical_request_id": recorder.next_logical_request_id()}
        try:
            return original_completion(self, *args, **kwargs)
        finally:
            active = None

    def send(self, request, *args, **kwargs):
        nonlocal retry_context
        if args or active is None:
            raise BudgetStopped("unsupported Aider transport outside model request")
        observed = urlsplit(str(request.url))
        body = json.loads(request.content)
        if (observed != target or body.get("model") != endpoint["model"]
                or body.get("max_tokens") != budget.configured["max_completion_tokens"]):
            raise BudgetStopped("unexpected Aider endpoint, alias, or token cap")
        expected = request_body_controls(controls)
        if any(body.get(key) != value for key, value in expected.items()):
            raise BudgetStopped("Aider request controls were not preserved")
        budget.before_send(request, active, self, kwargs)
        active["sdk_transport_attempt_ordinal"] += 1
        recorder.record("aider_transport_started", logical_request_id=active["logical_request_id"],
                        attempt=active["sdk_transport_attempt_ordinal"], model_call=budget.model_calls)
        retry_context = active
        try:
            response = original_send(self, request, **kwargs)
            if 200 <= response.status_code < 300:
                retry_context = None
            recorder.record("aider_transport_response", logical_request_id=active["logical_request_id"],
                            attempt=active["sdk_transport_attempt_ordinal"],
                            http_status=response.status_code, **response_diagnostics(response))
            return response
        finally:
            budget.check()

    client.send, model_class.send_completion = send, completion

    def restore():
        client.send, model_class.send_completion = original_send, original_completion
    return restore


def request_body_controls(controls):
    result = {}
    if controls.get("reasoning_effort") is not None:
        result["reasoning_effort"] = controls["reasoning_effort"]
    if controls.get("chat_template_kwargs") is not None:
        result["chat_template_kwargs"] = controls["chat_template_kwargs"]
    for name, setting in controls.get("sampling", {}).items():
        if setting["sent"]:
            result[name] = setting["effective_request"]
    return result


def run_contained_callback(command, *, plan, budget, **kwargs):
    from .aider_containment import run_contained_test
    if kwargs.get("timeout") != 180:
        raise RuntimeError("upstream Aider test timeout changed")
    budget.check()
    recorder = getattr(budget, "recorder", None)
    task = Path(kwargs["cwd"]).relative_to(plan["run_path"])
    if recorder is not None:
        recorder.record("aider_contained_command", command=list(command), task=str(task), timeout_s=180)
    completed, timed_out = None, None
    try:
        completed = run_contained_test(["python3", "/aider/anvil-entrypoint.py", *command], task_dir=kwargs["cwd"],
            run_path=plan["run_path"], ownership_id=plan["ownership_id"],
            run_id=plan["run_id"], image_id=plan["aider_environment"]["image_id"],
            timeout=min(180, budget.check()), container_binary=plan["container_executable"])
        timed_out = False
        return completed
    except subprocess.TimeoutExpired:
        timed_out = True
        raise
    finally:
        verify_task_tree(kwargs["cwd"])
        if recorder is not None:
            compiler_codes, compiler_signals, truncated = set(), set(), None
            language = task.parts[-4] if len(task.parts) >= 4 and task.parts[-3:-1] == ("exercises", "practice") else None
            if completed is not None and language in _COMPILER_DIAGNOSTIC_PATTERNS:
                truncated = False
                for output in (completed.stdout, completed.stderr):
                    if isinstance(output, (str, bytes)):
                        truncated |= len(output) > 65536
                        text = output[:65536]
                        if isinstance(text, bytes):
                            text = text.decode("utf-8", "replace")
                        if language == "rust":
                            compiler_codes.update(re.findall(r"(?m)^error\[(E[0-9]{4})\]:", text))
                        compiler_signals.update(label for label, pattern in _COMPILER_DIAGNOSTIC_PATTERNS[language].items()
                                                if re.search(pattern, text))
                if compiler_codes:
                    compiler_signals.add("rust_error_code")
            # Diagnostic signatures are observations, not a replacement test oracle.
            recorder.record("aider_contained_command_finished", task=str(task),
                returncode=completed.returncode if completed is not None else None, timed_out=timed_out,
                rust_compiler_error_codes=sorted(compiler_codes)[:32] or None,
                compilation_failure=True if compiler_signals and completed.returncode != 0 else None,
                compilation_failure_signals=sorted(compiler_signals) or None,
                compiler_diagnostics_truncated=truncated if truncated is None else truncated or len(compiler_codes) > 32)


def run_contained_lint(command, *, plan, budget, **kwargs):
    if command[:3] != [sys.executable, "-m", "flake8"]:
        raise BudgetStopped("unexpected Aider lint command")
    completed = run_contained_callback(["python3", "-I", *command[1:]],
        plan=plan, budget=budget, cwd=kwargs["cwd"], timeout=180)
    return subprocess.CompletedProcess(command, completed.returncode,
        completed.stdout or "", completed.stderr or "")


def run_adapter(plan):
    from .artifacts import atomic_write_json

    root = verify_source(plan["paths"]["aider_root"])
    sys.path[:0] = [str(root / "benchmark"), str(root)]
    recorder = EventRecorder(plan["paths"]["request_events"])
    recorder.record("aider_adapter_ready", adapter_sha256=plan["adapter_sha256"], source_hashes=SOURCE_HASHES)
    budget = SuiteBudget(plan["budgets"], recorder)
    outcome, restore = "failed", None
    try:
        with disable_dotenv():
            namespace = runpy.run_path(str(root / "benchmark/benchmark.py"), run_name="anvil_aider_benchmark")
            import httpx
            from aider import linter as upstream_linter, models
            from aider.coders.base_coder import Coder
            from aider.linter import Linter
            from aider.coders.wholefile_coder import WholeFileCoder

            restore = install_transport_guard(httpx.Client, models.Model, endpoint=plan["endpoint"],
                                              controls=plan["request_controls"], budget=budget, recorder=recorder)
            real_model = models.Model
            # Unknown local aliases must not trigger mutable remote metadata downloads.
            models.model_info_manager._update_cache = lambda: None

            def explicit_model(name, **kwargs):
                if name != "openai/" + plan["endpoint"]["model"]:
                    raise BudgetStopped("Aider model substitution rejected")
                model = real_model(name, weak_model=False, editor_model=False,
                                   verbose=kwargs.get("verbose", False))
                model.weak_model = model.editor_model = model
                model.extra_params = {
                    "api_base": plan["endpoint"]["base_url"], "custom_llm_provider": "openai",
                    "max_tokens": plan["budgets"]["max_completion_tokens"],
                    "extra_body": request_body_controls(plan["request_controls"]),
                }
                return model

            # The benchmark uses this module reference only for Model/register metadata.
            model_proxy = types.SimpleNamespace(**vars(models))
            model_proxy.Model = explicit_model
            globals_ = namespace["run_test_real"].__globals__
            globals_["models"] = model_proxy
            original_allowed = Coder.allowed_to_edit
            results_root = Path(plan["paths"]["results"]).resolve()

            def allowed(self, path):
                guard_solution_path(self, path, results_root)
                return original_allowed(self, path)
            Coder.allowed_to_edit = allowed
            original_diff = WholeFileCoder.do_live_diff
            def guarded_diff(self, full_path, new_lines, final):
                guard_solution_path(self, full_path, results_root)
                return original_diff(self, full_path, new_lines, final)
            WholeFileCoder.do_live_diff = guarded_diff
            original_lint = Linter.flake8_lint

            Linter.flake8_lint = contained_test_callback(original_lint,
                partial(run_contained_lint, plan=plan, budget=budget))

            replacement = contained_test_callback(namespace["run_unit_tests"],
                partial(run_contained_callback, plan=plan, budget=budget))
            def guarded_callback(original, taskdir, history, files):
                verify_task_tree(taskdir)
                return replacement(original, taskdir, history, files)
            globals_["run_unit_tests"] = guarded_callback
            assert namespace["run_test_real"].__globals__["run_unit_tests"] is guarded_callback
            os.environ["AIDER_DOCKER"] = "managed-contained-tests"
            # Namespace functions share the runpy globals, not its returned copy.
            globals_["BENCHMARK_DNAME"] = Path(plan["paths"]["aider"])
            args = [plan["paths"]["results"], "--model", "openai/" + plan["endpoint"]["model"],
                    "--edit-format", "whole", "--tries", "2", "--threads", "1",
                    "--exercises-dir", "original"]
            try:
                with cpp_header_lint(plan, upstream_linter):
                    namespace["app"](args=args, standalone_mode=False)
                budget.check()
                outcome = "completed"
            finally:
                Coder.allowed_to_edit = original_allowed
                Linter.flake8_lint = original_lint
                WholeFileCoder.do_live_diff = original_diff
    except BudgetStopped:
        outcome = "budget_exhausted" if budget.reason else "boundary_rejected"
        raise
    finally:
        if restore:
            restore()
        recorder.close(outcome=outcome)
        atomic_write_json(plan["paths"]["adapter_result"], {
            "schema": "anvil-serving.aider-adapter-result/v1", "outcome": outcome,
            "plan_sha256": plan["plan_sha256"], "adapter_sha256": plan["adapter_sha256"],
            "budget": budget.snapshot(),
            "events_complete": not recorder.write_failed and not recorder.omitted_events,
        })


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    args = parser.parse_args(argv)
    plan = json.loads(Path(args.plan).read_text())
    from .aider_prepare import verify_aider_plan as verify_plan
    verify_plan(plan)
    run_adapter(plan)


if __name__ == "__main__":
    main()
