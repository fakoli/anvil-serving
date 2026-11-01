from __future__ import annotations

import json

import pytest

from anvil_serving.benchmarking import aider
from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.profiles import load_profile


def accounted_event_stream(*, terminal_status="complete", pending=0):
    schema = aider.AIDER_REQUEST_ACCOUNTING_SCHEMA
    usage = {"prompt_tokens": None, "completion_tokens": None,
             "total_tokens": None, "reasoning_tokens": None}
    events = [
        {"schema": "event/v1", "event": "aider_adapter_ready", "seq": 1,
         "utc_observed_at": "2026-10-25T00:00:00Z", "request_accounting_schema": schema},
        {"schema": "event/v1", "event": "aider_transport_started", "seq": 2,
         "utc_observed_at": "2026-10-25T00:00:01Z",
         "logical_request_id": "logical-00000001", "attempt": 1},
        {"schema": "event/v1", "event": "aider_transport_response", "seq": 3,
         "utc_observed_at": "2026-10-25T00:00:02Z",
         "logical_request_id": "logical-00000001", "attempt": 1},
        {"schema": "event/v1", "event": "aider_transport_started", "seq": 4,
         "utc_observed_at": "2026-10-25T00:00:03Z",
         "logical_request_id": "logical-00000002", "attempt": 1},
    ]
    errors = 0
    if not pending:
        events.append({"schema": "event/v1", "event": "aider_transport_error", "seq": 5,
            "utc_observed_at": "2026-10-25T00:00:04Z",
            "logical_request_id": "logical-00000002", "attempt": 1,
            "error_type": "transport_error", "http_status": None, "finish_reason": None,
            "output_truncated": None, "usage": usage})
        errors = 1
    accounting = {"schema": schema, "status": terminal_status, "started": 2,
                  "responses": 1, "errors": errors, "pending": pending}
    events.append({"schema": "event/v1", "event": "adapter_finished", "seq": len(events) + 1,
                   "utc_observed_at": "2026-10-25T00:00:05Z", "status": terminal_status,
                   "request_accounting": accounting})
    adapter_result = {"request_accounting": accounting,
                      "events_complete": terminal_status == "complete"}
    return events, adapter_result


def test_retention_rejects_symlink_and_large_selection(tmp_path):
    base = tmp_path / "aider"
    base.mkdir()
    (tmp_path / "outside").write_text("{}")
    (base / "selection.json").symlink_to(tmp_path / "outside")
    assert aider.retain_aider_artifacts(run_path=str(tmp_path))["state"] == "failed"
    (base / "selection.json").unlink()
    (base / "selection.json").write_text(" " * (1024 * 1024 + 1))
    assert aider.retain_aider_artifacts(run_path=str(tmp_path))["state"] == "failed"


def prepared_plan_inputs(tmp_path, monkeypatch):
    import hashlib
    from anvil_serving.benchmarking import aider_prepare as prep
    # Reuse preparation's fixture shape: the same public validator gates this plan.
    from tests.test_aider_prepare import assets, environment, selection, python_environment
    cache = tmp_path / "cache"
    manifest_assets = assets()
    for asset in manifest_assets.values():
        asset["dirty"] = False
    aider_root = cache / manifest_assets["aider"]["cache_key"]
    polyglot = cache / manifest_assets["polyglot-benchmark"]["cache_key"]
    rows = selection(polyglot)
    for relative in aider.SOURCE_HASHES:
        path = aider_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pinned-source-fixture")
        monkeypatch.setitem(aider.SOURCE_HASHES, relative, hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(prep, "SOURCE_HASHES", {**aider.SOURCE_HASHES, "benchmark/Dockerfile": "d" * 64})
    receipt = environment(rows)
    python_receipt = python_environment(cache)
    python = cache / python_receipt["cache_key"] / "bin/python"
    profile = load_profile("pilot-v2")
    assets_manifest = {"schema": aider.HARNESS_ASSETS_SCHEMA, "suite": "aider",
        "profile_sha256": profile["content_sha256"], "assets": manifest_assets,
        "aider_environment": receipt, "python_environment": python_receipt}
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(aider, "resolve_container_binary", lambda: "/usr/bin/docker")
    return profile, assets_manifest, dict(
        endpoint={"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        run_root=str(run_root), cache_root=str(cache), ownership_id="owner", run_id="run",
        request_controls={"thinking_mode": "disabled"}), python, rows, receipt


@pytest.mark.parametrize("mode,expected_state,expected_failure", [
    ("legacy_gap", "completed", None),
    ("accounted_complete", "completed", None),
    ("accounted_incomplete", "incomplete", "incomplete_aider_observations"),
])
def test_run_parser_versions_request_accounting_without_changing_native_grade(
        tmp_path, monkeypatch, mode, expected_state, expected_failure):
    import subprocess
    from pathlib import Path

    profile, manifest, kwargs, _, rows, _ = prepared_plan_inputs(tmp_path, monkeypatch)
    task = rows[0]["task_id"]
    kwargs["instance_ids"] = [task]
    plan = aider.build_aider_run_plan(profile, manifest, **kwargs)

    def runner(command, _cwd, _timeout, _env):
        results = Path(plan["paths"]["results"]) / task
        results.mkdir(parents=True)
        (results / ".aider.results.json").write_text(json.dumps({
            "model": "openai/" + plan["endpoint"]["model"], "edit_format": "whole",
            "tests_outcomes": [True]}))
        ready = {"schema": "event/v1", "event": "aider_adapter_ready", "seq": 1,
                 "utc_observed_at": "2026-10-25T00:00:00Z",
                 "adapter_sha256": plan["adapter_sha256"], "source_hashes": aider.SOURCE_HASHES}
        if mode != "legacy_gap":
            ready["request_accounting_schema"] = aider.AIDER_REQUEST_ACCOUNTING_SCHEMA
        events = [ready,
            {"schema": "event/v1", "event": "aider_transport_started", "seq": 2,
             "utc_observed_at": "2026-10-25T00:00:01Z",
             "logical_request_id": "logical-00000001", "attempt": 1},
            {"schema": "event/v1", "event": "aider_transport_response", "seq": 3,
             "utc_observed_at": "2026-10-25T00:00:02Z",
             "logical_request_id": "logical-00000001", "attempt": 1},
            {"schema": "event/v1", "event": "aider_transport_started", "seq": 4,
             "utc_observed_at": "2026-10-25T00:00:03Z",
             "logical_request_id": "logical-00000002", "attempt": 1}]
        if mode == "accounted_complete":
            events.append({"schema": "event/v1", "event": "aider_transport_error", "seq": 5,
                "utc_observed_at": "2026-10-25T00:00:04Z",
                "logical_request_id": "logical-00000002", "attempt": 1,
                "error_type": "transport_error", "http_status": None, "finish_reason": None,
                "output_truncated": None, "usage": dict.fromkeys(aider._AIDER_USAGE_FIELDS)})
        status = "incomplete" if mode == "accounted_incomplete" else "complete"
        accounting = None if mode == "legacy_gap" else {
            "schema": aider.AIDER_REQUEST_ACCOUNTING_SCHEMA, "status": status, "started": 2,
            "responses": 1, "errors": int(mode == "accounted_complete"),
            "pending": int(mode == "accounted_incomplete")}
        raw = [(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
               for event in events]
        budget = {"configured": plan["budgets"], "model_calls": 2, "elapsed_s": 1.0,
                  "exhausted": False, "reason": None}
        event_terminal = {"schema": "event/v1", "event": "adapter_finished",
                          "seq": len(events) + 1, "utc_observed_at": "2026-10-25T00:00:05Z",
                          "status": status, "outcome": status, "event_count": len(events),
                          "byte_count": sum(map(len, raw)), "omitted_events": 0,
                          "omitted_bytes": 0, "budget": budget}
        adapter_result = {"schema": "anvil-serving.aider-adapter-result/v1",
                          "outcome": "completed" if status == "complete" else "incomplete",
                          "plan_sha256": plan["plan_sha256"],
                          "adapter_sha256": plan["adapter_sha256"], "budget": budget,
                          "events_complete": status == "complete"}
        if accounting is not None:
            event_terminal["request_accounting"] = accounting
            adapter_result["request_accounting"] = accounting
        raw.append((json.dumps(event_terminal, sort_keys=True, separators=(",", ":")) + "\n").encode())
        Path(plan["paths"]["request_events"]).write_bytes(b"".join(raw))
        Path(plan["paths"]["adapter_result"]).write_text(json.dumps(adapter_result))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    result = aider.run_aider_benchmark(plan, command_runner=runner)
    assert result["state"] == expected_state
    assert result["instances"][0]["resolved"] is True
    assert result["summary"] == {"selected": 1, "graded": 1, "resolved": 1, "resolve_rate": 1.0}
    assert (result["failure"] or {}).get("code") == expected_failure
    if mode == "accounted_incomplete":
        assert result["quality"] == {"status": "unknown"}
        assert result["request_accounting"]["pending"] == 1


@pytest.mark.parametrize("changed", ["interpreter", "product_root", "bootstrap"])
def test_prepared_python_command_cannot_substitute_interpreter(tmp_path, monkeypatch, changed):
    profile, manifest, kwargs, *_ = prepared_plan_inputs(tmp_path, monkeypatch)
    plan = aider.build_aider_run_plan(profile, manifest, **kwargs)
    position = {"interpreter": 0, "bootstrap": plan["command"].index("-c") + 1,
                "product_root": plan["command"].index("-c") + 2}[changed]
    plan["command"][position] = "/untrusted/substitute"
    plan["plan_sha256"] = aider._digest({k:v for k,v in plan.items() if k != "plan_sha256"})
    with pytest.raises(BenchmarkJobError, match="prepared interpreter"):
        aider.run_aider_benchmark(plan, command_runner=lambda *_: pytest.fail("substitute Python executed"))


@pytest.mark.parametrize("owner, count, expected", [
    ("aider", 8 * 1024**2, "completed"),
    ("aider", 8 * 1024**2 + 1, "swe_process_output_overflow"),
    ("swe", 65537, "swe_process_output_overflow"),
    ("performance", 65537, "swe_process_output_overflow"),
    ("aider", 0, "timeout"),
])
def test_actual_process_capture_caps_deadline_and_owned_group_cleanup(tmp_path, owner, count, expected):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import textwrap
    if os.name != "posix":
        pytest.skip("owned process groups require POSIX")
    script = textwrap.dedent("""\
        import json, os, subprocess, sys
        sys.path.insert(0, sys.argv[1])
        from anvil_serving.benchmarking import aider, swe, performance
        from anvil_serving.benchmarking.jobs import BenchmarkJobError
        from anvil_serving.transports import _owned_group_members
        owner, count = sys.argv[2], int(sys.argv[3])
        runner = (swe._budget_runner if owner == 'swe' else
                  (aider.run_aider_benchmark if owner == 'aider' else
                   performance.run_performance_benchmark).__kwdefaults__['command_runner'])
        child = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)']); "
        child += ("time.sleep(5)" if not count else
                  f"sys.stdout.buffer.write(b'a'*{count // 2}); sys.stdout.flush(); sys.stderr.buffer.write(b'b'*{count - count // 2}); sys.stderr.flush()")
        try:
            result = runner([sys.executable, '-I', '-B', '-c', child], os.getcwd(),
                            0.2 if not count else 5, {'PATH': os.defpath})
            observed = {'outcome': 'completed', 'bytes': len(result.stdout) + len(result.stderr),
                        'returncode': result.returncode}
        except BenchmarkJobError as exc:
            observed = {'outcome': exc.code}
        except subprocess.TimeoutExpired:
            observed = {'outcome': 'timeout'}
        observed['remaining_peers'] = _owned_group_members(os.getpid(), timeout=1)
        print(json.dumps(observed))
    """)
    process = subprocess.run([sys.executable, "-I", "-B", "-c", script,
                              str(Path(aider.__file__).resolve().parents[2]), owner, str(count)],
                             cwd=tmp_path, env={"PATH": os.defpath}, start_new_session=True,
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stderr
    observed = json.loads(process.stdout)
    assert observed["outcome"] == expected
    assert observed["remaining_peers"] == []
    if expected == "completed":
        assert observed["returncode"] == 0 and observed["bytes"] == count
