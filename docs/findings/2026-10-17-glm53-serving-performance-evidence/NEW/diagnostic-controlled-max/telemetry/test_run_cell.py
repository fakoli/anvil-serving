#!/usr/bin/env python3
"""Offline orchestration fixture for the retained campaign runner."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("run_cell_fixture", HERE / "run_cell.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
REAL_LOAD_PLAN = RUNNER.load_plan
NATIVE = {
    "schema": "anvil-serving.benchmark/v1",
    "model": "fixture-controlled-model", "engine": "fixture-engine", "gpu": "fixture-gpu",
    "measurement_protocol": "capacity-v4-reasoning", "unique_prefix_namespace": "capacity-v1:seed-request-index",
    "base_url": "http://127.0.0.1:31841/v1", "context_tokens": 10240, "concurrency": 1,
    "requests": 1, "context_seed": 483001, "max_context_tokens": 327680, "max_tokens": 16384,
    "prompt_cache_mode": "unique", "request_canaries": True, "response_words": 128,
    "controlled_output_policy": "strict", "workload_identity": {"workload_kind": "synthetic-repeated-code"},
    "serve_flags": {"reasoning_effort": "max"},
    "sampling": {"temperature": {"sent": True, "effective_request": 0.6}, "top_p": {"sent": True, "effective_request": 0.95}},
    "completed": 1, "failed": 0, "responses_received": 1,
    "request_timings": [{"prompt_tokens": 10240, "output_tokens": 128}], "failures": []
}


class Response:
    def __init__(self, text): self.text = text
    def read(self): return self.text.encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Process:
    def __init__(self, artifact, native, polls=22):
        self.artifact, self.native, self.polls = artifact, native, polls
        self.calls = self.returncode = 0
        self.terminated = self.killed = False

    def finish(self, code=0):
        if self.returncode is None:
            self.returncode = code
            self.artifact.parent.mkdir(parents=True, exist_ok=True)
            self.artifact.write_text(json.dumps(self.native))

    def poll(self):
        self.calls += 1
        if self.returncode is None and self.calls >= self.polls:
            self.finish()
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.finish(-15)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.finish(-9)


class RunnerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plan = {"absolute_request_cutoff": "2099-01-01T00:00:00+00:00", "models": {"baseline": {
            "model": NATIVE["model"], "repository": "brandonmusic/GLM-5.3-Flash-tr3-4bpw",
            "revision": "a5fee929cf4888b1824323e33e8a19b60129e025", "port":  31841,
            "container": "fixture-container", "registry": "fixture-registry"}}, "matrix": []}
        self.row = {"id": "diagnostic-baseline-controlled-ctx10240-c1-n1", "arm": "baseline", "planned_context_tokens": 10240,
            "concurrency": 1, "requests": 1, "seed": 483001, "diagnostic_request_index": 0, "outer_timeout_seconds": 10,
            "argv": ["fixture-python", "-m", "fixture", "eval", "benchmark", "capacity", "--base-url", "http://127.0.0.1:31841/v1",
                "--model", NATIVE["model"], "--engine", NATIVE["engine"], "--requests", "1", "--concurrency", "1",
                "--gpu", NATIVE["gpu"], "--ctx-tokens", "10240", "--seed", "483001", "--max-model-len", "327680",
                "--max-tokens", "16384", "--prompt-cache-mode", "unique",
                "--request-canaries", "--controlled-output-policy", "strict", "--response-words", "128", "--temperature", "0.6",
                "--top-p", "0.95", "--reasoning-effort", "max", "--output", str(self.root / "native/baseline/diagnostic-baseline-controlled-ctx10240-c1-n1.json"), "--confirm"]}
        self.plan["matrix"] = [self.row]
        (self.root / "plans").mkdir()
        (self.root / "plans/run-plan.json").write_text(json.dumps(self.plan))
        self.identity = self.root / "identity.json"
        self.identity.write_text(json.dumps({"container": "fixture-container", "container_id": "fixture-id", "model": self.plan["models"]["baseline"]["repository"], "revision": self.plan["models"]["baseline"]["revision"], "image": "fixture-image"}))
        self.native = copy.deepcopy(NATIVE)
        self.probes = {"gpu": 0, "host": 0, "cgroup": 0}
        self.process = None
        self.patches = [mock.patch.object(RUNNER, "ROOT", self.root), mock.patch.object(RUNNER, "load_plan", return_value=self.plan)]
        for patch in self.patches: patch.start()

    def tearDown(self):
        for patch in reversed(self.patches): patch.stop()
        self.tmp.cleanup()

    def metrics(self):
        model = self.plan["models"]["baseline"]["model"]
        done = self.process is not None and self.process.returncode is not None
        native = getattr(self, "active_native", self.native)
        timings = list(native["request_timings"])
        timings += [failure["response_timing"] for failure in native["failures"]
                    if isinstance(failure.get("response_timing"), dict)]
        prompt = sum(item["prompt_tokens"] for item in timings) if done else 0
        output = sum(item["output_tokens"] for item in timings) if done else 0
        received = native["responses_received"] if done else 0
        state = 0 if done or self.process is None else 1
        labels = f'engine="0",model_name="{model}"'
        return "\n".join((
            "process_start_time_seconds 1", f"vllm:num_requests_running{{{labels}}} {state}",
            f"vllm:num_requests_waiting{{{labels}}} 0", f"vllm:kv_cache_usage_perc{{{labels}}} 0.1",
            f"vllm:prompt_tokens_total{{{labels}}} {prompt}", f"vllm:generation_tokens_total{{{labels}}} {output}",
            f"vllm:request_success_total{{{labels}}} {received}"))

    def exercise(self, polls=22, native=None):
        native = self.native if native is None else native
        self.active_native = native
        artifact = Path(self.row["argv"][self.row["argv"].index("--output") + 1])
        def popen(*args, **kwargs):
            self.process = Process(artifact, native, polls)
            self.process.returncode = None
            return self.process
        def gpu(): self.probes["gpu"] += 1; return {"sample": self.probes["gpu"]}
        def host(): self.probes["host"] += 1; return {"MemTotal": {"value": 1, "unit": "kB"}}
        def cgroup(*args):
            self.probes["cgroup"] += 1
            return {"host_memory": {"events": {"oom": 0, "oom_kill": 0, "oom_group_kill": 0}}}
        with mock.patch.object(RUNNER.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(RUNNER.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(self.metrics())), \
             mock.patch.object(RUNNER.C, "gpu_sample", side_effect=gpu), \
             mock.patch.object(RUNNER.C, "host_memory", side_effect=host), \
             mock.patch.object(RUNNER.C, "status_sample", side_effect=cgroup):
            return RUNNER.run_cell(self.row, self.plan, self.identity)

    def summary(self):
        return json.loads((self.root / "telemetry/baseline/diagnostic-baseline-controlled-ctx10240-c1-n1/cell-summary.json").read_text())

    def test_orchestrates_inflight_probes_counters_and_native_fixture(self):
        self.assertEqual(self.exercise(), 0)
        summary = self.summary()
        self.assertTrue(summary["coverage_passed"])
        self.assertGreaterEqual(summary["sampling_window"]["engine"]["in_window_samples"], 1)
        self.assertGreaterEqual(summary["sampling_window"]["slow"]["in_window_samples"], 1)
        self.assertEqual(summary["counter_reconciliation"]["status"], "exact")
        self.assertEqual(set(self.probes.values()), {self.probes["gpu"]})
        self.assertGreaterEqual(self.probes["gpu"], 3)  # before, in-flight, after
        engine = (self.root / "telemetry/baseline/diagnostic-baseline-controlled-ctx10240-c1-n1/engine-metrics.jsonl").read_text()
        self.assertIn('"running": 1.0', engine)
        self.assertFalse(self.process.terminated)

    def test_collector_fault_terminates_only_owned_cli_and_fails_closed(self):
        calls = 0
        def bad_gpu():
            nonlocal calls
            calls += 1
            if calls == 2: raise ValueError("fixture gpu failure")
            return {"sample": calls}
        artifact = Path(self.row["argv"][self.row["argv"].index("--output") + 1])
        def popen(*args, **kwargs):
            self.process = Process(artifact, self.native, 999)
            self.process.returncode = None
            return self.process
        with mock.patch.object(RUNNER.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(RUNNER.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(self.metrics())), \
             mock.patch.object(RUNNER.C, "gpu_sample", side_effect=bad_gpu), \
             mock.patch.object(RUNNER.C, "host_memory", return_value={}), \
             mock.patch.object(RUNNER.C, "status_sample", return_value={"host_memory": {"events": {"oom": 0, "oom_kill": 0, "oom_group_kill": 0}}}):
            self.assertEqual(RUNNER.run_cell(self.row, self.plan, self.identity), 2)
        self.assertTrue(self.process.terminated)
        self.assertEqual(self.summary()["aborted"], "collector failure")

    def test_timeout_terminates_owned_cli_and_drains(self):
        self.row["outer_timeout_seconds"] = 0
        self.assertEqual(self.exercise(polls=999), 2)
        self.assertTrue(self.process.terminated)
        self.assertEqual(self.summary()["aborted"], "deadline")

    def test_existing_output_refusal_precedes_process_start(self):
        artifact = Path(self.row["argv"][self.row["argv"].index("--output") + 1])
        artifact.parent.mkdir(parents=True); artifact.write_text("{}")
        with mock.patch.object(RUNNER.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "refuse overwrite"):
                RUNNER.run_cell(self.row, self.plan, self.identity)
        popen.assert_not_called()

    def test_diagnostic_plan_binds_one_original_controlled_request_per_arm(self):
        diagnostic = json.loads((HERE.parent / "plans/run-plan.json").read_text())
        original = json.loads((HERE.parents[2] / "plans/run-plan.json").read_text())
        self.assertEqual(diagnostic["absolute_request_cutoff"], original["absolute_request_cutoff"])
        self.assertEqual(set(diagnostic["models"]), {"baseline", "candidate"})
        self.assertEqual(len(diagnostic["matrix"]), 2)
        for row in diagnostic["matrix"]:
            source = next(item for item in original["matrix"]
                          if item["id"] == f'{row["arm"]}-controlled-ctx10240-c1')
            self.assertEqual(row["requests"], 1)
            self.assertEqual(row["seed"], 483001)
            self.assertEqual(row["diagnostic_request_index"], 0)
            self.assertEqual(row["outer_timeout_seconds"], 600)
            expected = list(source["argv"])
            expected[expected.index("--requests") + 1] = "1"
            expected[expected.index("--output") + 1] = row["argv"][row["argv"].index("--output") + 1]
            self.assertEqual(row["argv"], expected)

    def test_failed_v3_native_retains_reasoning_binding_and_reconciles(self):
        failed = copy.deepcopy(self.native)
        failed.update({"measurement_protocol": "capacity-v3", "completed": 0,
                       "failed": 1, "responses_received": 1, "request_timings": [],
                       "failures": [{"response_timing": {
                           "prompt_tokens": 10240, "output_tokens": 128,
                           "request_canary": {"capture_complete": True, "foreign_marker_count": 0,
                                              "marker": "ANVIL_REQ_483001_00000", "marker_at_start": True,
                                              "observed_marker_count": 1, "passed": True},
                           "controlled_output": {"exact_adherence": False},
                       }, "response_metadata": {
                           "output_token_source": "usage", "content_chunks": 1, "reasoning_chunks": 2,
                           "finish_reasons": ["length"], "stream_terminal_observed": True,
                           "visible_content_truncated": False, "visible_content_captured_chars": 128,
                           "visible_content_capture_limit": 8192,
                           "usage": {"prompt_tokens": 10240, "completion_tokens": 128, "total_tokens": 10368},
                       }}]})
        self.assertEqual(self.exercise(native=failed), 3)
        summary = self.summary()
        self.assertTrue(summary["coverage_passed"])
        self.assertEqual(summary["counter_reconciliation"]["status"], "exact")
        self.assertEqual(failed["serve_flags"]["reasoning_effort"], "max")
        self.assertTrue(failed["request_canaries"])
        self.assertEqual(failed["controlled_output_policy"], "strict")
        metadata = failed["failures"][0]["response_metadata"]
        self.assertEqual(set(metadata), {"output_token_source", "content_chunks", "reasoning_chunks",
                                         "finish_reasons", "stream_terminal_observed",
                                         "visible_content_truncated", "visible_content_captured_chars",
                                         "visible_content_capture_limit", "usage"})

    def test_native_binding_rejection_and_frozen_hash_drift(self):
        bad = copy.deepcopy(self.native); bad["model"] = "wrong-model"
        with self.assertRaisesRegex(ValueError, "native identity/control mismatch"):
            self.exercise(polls=1, native=bad)
        plan_path = self.root / "plans/run-plan.json"
        plan_path.write_text("{}")
        with mock.patch.object(RUNNER, "ROOT", self.root), mock.patch.object(RUNNER, "PLAN_SHA", "not-the-plan"):
            with self.assertRaisesRegex(ValueError, "frozen plan changed"):
                REAL_LOAD_PLAN()


if __name__ == "__main__":
    unittest.main()
