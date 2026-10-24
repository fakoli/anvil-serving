"""Hash-guarded Aider bridge; imported dependencies live only in its prepared venv."""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from functools import partial
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import stat
import subprocess
import sys
import types
from urllib.parse import urlsplit

from .swe_agent_adapter import BudgetStopped, EventRecorder, SuiteBudget

AIDER_REVISION = "5dc9490bb35f9729ef2c95d00a19ccd30c26339c"
POLYGLOT_REVISION = "7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f"
SOURCE_HASHES = {
    "benchmark/benchmark.py": "ba350b7b3ebcc9da6f588c0724dade552791b3c5edd9f16641eadb5931de9c30",
    "benchmark/npm-test.sh": "4ad9c732f05da5cad4608545454b574c40a97aefb45ead784a31f398b5dad687",
    "benchmark/cpp-test.sh": "51184a5f17c90e5cfeec37bb7f7cb37119b9fe7c295621f36bdcef5094713eca",
    "aider/linter.py": "b798f0599d5dbc8cd1b628359134a3ce5eaed4b45fc347b5f6ac0d21e9f68c08",
    "aider/models.py": "37784de662eeafacf17cbc4eb656246aadee20477b8ad43768caf7d373ab6e0d",
    "aider/coders/base_coder.py": "04a7b38c4f1d2b1684ac958f40bcd81a28b00139eae88afed59d2c970bef1ee1",
    "aider/coders/wholefile_coder.py": "b8fa0d35e4a26b5c2b978a223e3c2762bb557a3ee15dad8e7a91ac242f6dd52f",
}
LANGUAGES = frozenset({"cpp", "go", "java", "javascript", "python", "rust"})


def validate_task_id(value):
    if (not isinstance(value, str) or len(value) > 256 or "\\" in value
            or not re.fullmatch(r"[a-z][a-z0-9-]*/exercises/practice/[a-z0-9][a-z0-9_-]*", value)
            or PurePosixPath(value).parts[0] not in LANGUAGES):
        raise ValueError("invalid source-relative Aider task ID")
    return value


def task_snapshot(root, task_id):
    root = Path(root).resolve(strict=True)
    task = root / validate_task_id(task_id)
    if not task.resolve(strict=True).is_relative_to(root):
        raise ValueError("Aider task escaped its source")
    for parent in [task, *task.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Aider task traverses a symlink")
    files = {}
    for path in sorted(task.rglob("*")):
        if path.is_symlink():
            raise ValueError("Aider task contains a symlink")
        if path.is_file():
            files[path.relative_to(task).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    config = json.loads((task / ".meta/config.json").read_text())
    for names in config.get("files", {}).values():
        if not isinstance(names, list):
            raise ValueError("invalid Aider task config")
        for name in names:
            if (not isinstance(name, str) or "\\" in name or PurePosixPath(name).is_absolute()
                    or ".." in PurePosixPath(name).parts or name not in files):
                raise ValueError("unsafe or missing Aider task config file")
    if not config.get("files", {}).get("test") or not config.get("files", {}).get("solution"):
        raise ValueError("Aider task lacks tests or solution")
    if ".docs/instructions.md" not in files:
        raise ValueError("Aider task lacks instructions")
    return files


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


def load_test_callback(root):
    """Load actual pinned oracle code without importing Aider or ambient dotenv."""
    path = Path(root) / "benchmark/benchmark.py"
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_HASHES["benchmark/benchmark.py"]:
        raise RuntimeError("pinned Aider test callback hash changed")
    tree = ast.parse(source, filename=str(path))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"run_unit_tests", "cleanup_test_output"}]
    if len(functions) != 2:
        raise RuntimeError("pinned Aider test callback missing")
    namespace = dict(Path=Path, os=os, shutil=shutil, re=re, subprocess=subprocess, dump=lambda *_: None)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["run_unit_tests"]


def contained_test_callback(callback, run_test):
    """Replace only this function's subprocess reference, never global subprocess."""
    namespace = dict(callback.__globals__)
    namespace["subprocess"] = types.SimpleNamespace(
        run=run_test, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
        TimeoutExpired=subprocess.TimeoutExpired,
    )
    return types.FunctionType(callback.__code__, namespace, callback.__name__,
                              callback.__defaults__, callback.__closure__)


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
def disable_dotenv():
    import dotenv  # Importing the library does not search or read environment files.
    old_load, old_find = dotenv.load_dotenv, dotenv.find_dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    dotenv.find_dotenv = lambda *args, **kwargs: ""
    try:
        yield
    finally:
        dotenv.load_dotenv, dotenv.find_dotenv = old_load, old_find


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
                            http_status=response.status_code)
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
    if getattr(budget, "recorder", None) is not None:
        budget.recorder.record("aider_contained_command", command=list(command),
            task=str(Path(kwargs["cwd"]).relative_to(plan["run_path"])), timeout_s=180)
    try:
        return run_contained_test(["python3", "/aider/anvil-entrypoint.py", *command], task_dir=kwargs["cwd"],
            run_path=plan["run_path"], ownership_id=plan["ownership_id"],
            run_id=plan["run_id"], image_id=plan["aider_environment"]["image_id"],
            timeout=min(180, budget.check()), container_binary=plan["container_executable"])
    finally:
        verify_task_tree(kwargs["cwd"])


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
            from aider import models
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
    from .aider import verify_plan
    verify_plan(plan)
    run_adapter(plan)


if __name__ == "__main__":
    main()
