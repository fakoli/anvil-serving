"""Behavioral gates for incident replay; all endpoints are synthetic."""

import copy
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import threading
import time

import pytest

from anvil_serving.benchmarking import stability


def scenario(base="http://127.0.0.1:8001/v1", **changes):
    return {"schema": stability.SCENARIO_SCHEMA, "base_url": base, "model": "test-model",
            "managed_container": "synthetic-model",
            "configuration": {"label": "baseline", "recipe_sha256": "0" * 64, "registry_sha256": "3" * 64,
                              "image_digest": "sha256:" + "1" * 64, "model_revision": "2" * 40},
            "context_limit": 4096, "anchor_tokens": 256, "contender_tokens": 256,
            "anchor_max_tokens": 128, "contender_max_tokens": 32, "rounds": 1,
            "mode": "overlap", "prefix_mode": "unique", "timeout_seconds": 3, "run_timeout_seconds": 10, **changes}


def calibration(config, role, salt, key):
    return role, ["expected"], config[role + "_tokens"]


def metadata(*args):
    return {"data": [{"id": "test-model"}]}


def identity(config):
    c = config["configuration"]
    from urllib.parse import urlsplit
    return {"container_id": "a" * 64, "image_digest": c["image_digest"],
            "revision": c["model_revision"], "recipe_digest": c["recipe_sha256"],
            "registry_digest": c["registry_sha256"], "served_identity": config["model"],
            "bound_port": urlsplit(config["base_url"]).port, "running": True}


def result(**changes):
    return {"ttft": .01, "visible_content": "expected", "finish_reasons": ["stop"],
            "stream_terminal_observed": True, "usage": {"prompt_tokens": 256}, **changes}


@pytest.mark.parametrize("fault", ["terminal_only", "early_finish", "disconnect", "bad_usage", "wrong_answer"])
def test_unexercised_or_failed_case_cannot_pass_or_continue(tmp_path, fault):
    calls = []

    def stream(base, model, prompt, key, max_tokens, **kwargs):
        calls.append(prompt)
        observer = kwargs["observer"]
        if fault == "disconnect":
            observer("delta", {"content": "partial evidence"})
            raise ConnectionError("synthetic interrupted stream")
        if fault != "terminal_only":
            observer("delta", {"content": "expected"})
        return result(usage={"prompt_tokens": 1} if fault == "bad_usage" else {"prompt_tokens": 256},
                      visible_content="wrong" if fault == "wrong_answer" else "expected")

    path = tmp_path / "trial.json"
    observed = stability.run(scenario(rounds=3), path, stream=stream,
                             calibrate=calibration, metadata=metadata, identity=identity)
    assert observed["status"] == "incomplete"
    assert observed["coverage_passed"] is False
    assert len(observed["rounds"]) == 1
    # A companion can already be in flight when the anchor fails. No next round
    # may start, and terminal-only output must never release the first barrier.
    assert calls in (["anchor"], ["anchor", "contender"])
    if fault == "terminal_only":
        assert calls == ["anchor"]
    saved = json.loads(path.read_text())
    if fault == "disconnect":
        assert saved["rounds"][0]["anchor"]["partial_visible"] == "partial evidence"
    with pytest.raises(FileExistsError):
        stability.run(scenario(), path, metadata=metadata)


def test_preview_has_no_network_or_artifact_side_effects(tmp_path, monkeypatch):
    config = tmp_path / "scenario $(not-a-command) ; space.json"
    config.write_text(json.dumps(scenario()))
    monkeypatch.setattr(stability.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network in preview"))
    output = tmp_path / "trial.json"
    assert stability.main(["--scenario", str(config), "--output", str(output)]) == 0
    assert not output.exists()
    completed = subprocess.run([sys.executable, "-m", "anvil_serving.cli", "eval", "benchmark", "stability",
                                "--scenario", str(config), "--output", str(output), "--dry-run"],
                               text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["network_requests"] == 0
    assert not output.exists()
    from anvil_serving.cli import main
    assert main(["eval", "benchmark", "stability", "--scenario", str(config),
                 "--output", str(output), "--dry-run"]) == 0
    assert not output.exists()


def test_real_sse_overlap_is_observed_and_serial_is_distinct(tmp_path):
    started = threading.Event()
    progressed = threading.Event()
    mode = ["overlap"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(metadata()).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("x-request-id", "synthetic-id")
            self.end_headers()

            def emit(value):
                self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
                self.wfile.flush()

            anchor = body["messages"][0]["content"] == "anchor"
            if anchor:
                emit({"choices": [{"delta": {"reasoning_content": "planning"}}]})
                if mode[0] == "overlap":
                    assert started.wait(2)
                emit({"choices": [{"delta": {"content": "expected"}}]})
                progressed.set()
                time.sleep(.1)
            else:
                started.set()
                assert progressed.wait(2)
                time.sleep(.05)
                emit({"choices": [{"delta": {"content": "expected"}}]})
            emit({"choices": [{"delta": {}, "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 256, "completion_tokens": 3}})
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        for selected in ("overlap", "serial"):
            mode[0] = selected
            started.clear()
            progressed.clear()
            observed = stability.run(scenario(base, mode=selected), tmp_path / (selected + ".json"), calibrate=calibration, identity=identity)
            assert observed["status"] == "completed"
            assert observed["coverage_passed"] is True
            pair = observed["rounds"][0]
            assert pair["anchor_output_during_contender_response_wait"] is (selected == "overlap")
            assert pair["anchor"]["request_id"] == "synthetic-id"
            assert observed["scheduler_overlap"] == "not_measured"
            assert observed["performance_eligible"] is False
            from anvil_serving.benchmark_evidence import summarize_artifact, compare_summaries, discover_artifacts
            summary = summarize_artifact(tmp_path / (selected + ".json"))
            assert summary["validation_errors"] == []
            from anvil_serving.benchmark_evidence import summarize_payload
            incomplete = copy.deepcopy(observed)
            incomplete.update(status="incomplete", identity_observations=[], configuration_identity="unverified")
            assert summarize_payload(incomplete, "missing.json")["container_identity_provenance"] == "unverified"
            sanitized = copy.deepcopy(observed)
            for row in sanitized["identity_observations"]:
                row["container_id"] = "redacted:sha256:" + "b" * 64
            public_summary = summarize_payload(sanitized, "public.json")
            assert public_summary["validation_errors"] == []
            assert public_summary["container_identity_provenance"] == "sanitized"
            assert any("private source receipt" in warning for warning in public_summary["warnings"])
            assert compare_summaries([public_summary, summary])["comparable"] is False
            for invalid_id in ("redacted:sha256:short", "redacted:" + "b" * 64, "a" * 64):
                sanitized["identity_observations"][-1]["container_id"] = invalid_id
                assert summarize_payload(sanitized, "invalid.json")["validation_errors"]
                incomplete = {**sanitized, "status": "incomplete"}
                assert summarize_payload(incomplete, "mixed.json")["container_identity_provenance"] == "unverified"
            for changes in ({"promoted": True}, {"performance_eligible": True},
                            {"scheduler_overlap": "measured"}, {"identity_observations": []},
                            {"configuration_identity": "unverified"}):
                corrupted = copy.deepcopy(observed)
                corrupted.update(changes)
                assert summarize_payload(corrupted, display_path=tmp_path / "corrupt.json")["validation_errors"]
            assert summary["stability"]["coverage_passed"] is True
            assert "expected" not in json.dumps(summary)
            assert compare_summaries([summary, summary])["comparable"] is False
            assert discover_artifacts(tmp_path, kind="stability")["matched"] >= 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("changes", [{"rounds": True}, {"rounds": 100}, {"temperature": float("nan")},
                                     {"anchor_tokens": 4000}, {"base_url": "http://key@127.0.0.1/v1"},
                                     {"mode": "anything"}, {"base_url": "http://127.0.0.1:8001/v1\r\n"},
                                     {"model": "bad\nmodel"}, {"api_key_env": "KEY\x00"}])
def test_invalid_scenario_is_rejected_before_network(changes):
    with pytest.raises(ValueError):
        stability.validate_scenario(scenario(**changes))


@pytest.mark.parametrize("content", ['{', '{"schema":"a","schema":"b"}', '[]'])
def test_malformed_scenario_cli_fails_without_creating_artifact(tmp_path, content):
    path = tmp_path / "invalid.json"
    path.write_text(content)
    output = tmp_path / "result.json"
    with pytest.raises(SystemExit) as error:
        stability.main(["--scenario", str(path), "--output", str(output)])
    assert error.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("replacement", ["b" * 64, "redacted:sha256:" + "b" * 64])
def test_identity_change_stops_before_requests(tmp_path, replacement):
    count = [0]
    def changing(config):
        row = identity(config)
        count[0] += 1
        if count[0] == 2:
            row["container_id"] = replacement
        return row
    observed = stability.run(scenario(), tmp_path / "changed.json", identity=changing,
                             metadata=metadata, stream=lambda *a, **k: pytest.fail("wrong identity requested"))
    assert observed["status"] == "failed"
    assert observed["failure_class"] == "identity_mismatch"
    assert not observed["rounds"]


def test_faulty_tokenizer_cannot_expand_prompt_without_bound(monkeypatch):
    calls = []
    def tokenizer(*args):
        calls.append(1)
        return {"count": 1}
    monkeypatch.setattr(stability, "api_json", tokenizer)
    with pytest.raises(ValueError, match="prompt bound"):
        stability.calibrated_prompt(scenario(anchor_tokens=1000000, context_limit=2000000), "anchor", "salt", None)
    assert len(calls) == 1


@pytest.mark.skipif(os.name == "nt", reason="Unix device and FIFO inputs")
def test_device_fifo_and_oversized_scenario_are_rejected(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for path in (fifo, "/dev/zero"):
        with pytest.raises(ValueError, match="regular file"):
            stability.read_scenario(path)
    large = tmp_path / "large.json"
    large.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        stability.read_scenario(large)


def test_pre_network_delay_cannot_claim_overlap(tmp_path):
    entered = threading.Event()
    def stream(base, model, prompt, key, max_tokens, **kwargs):
        observe = kwargs["observer"]
        if prompt == "anchor":
            observe("response", {})
            observe("delta", {"content": "expected"})
            assert entered.wait(2)
            observe("delta", {"content": "expected"})
        else:
            entered.set()
            time.sleep(.05)  # Anchor progresses before any contender response headers.
            observe("response", {})
            observe("delta", {"content": "expected"})
        return result()
    observed = stability.run(scenario(), tmp_path / "delay.json", stream=stream,
                             calibrate=calibration, metadata=metadata, identity=identity)
    assert observed["runtime_passed"] is True
    assert observed["coverage_passed"] is False
    assert observed["status"] == "incomplete"


@pytest.mark.skipif(os.name == "nt", reason="Synthetic executable replaces local managed inventory")
@pytest.mark.parametrize("fault", [None, "drip", "non_string", "oversize", "server_error", "id_change", "long_id", "non_string_id", "reasoning_only"])
def test_real_cli_live_transport_and_hard_deadline(tmp_path, monkeypatch, fault):
    from anvil_serving import serve_recipes as recipes
    entered = threading.Event()
    advanced = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(metadata()).encode())
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            if self.path == "/tokenize":
                self.wfile.write(b'{"count": 256}')
                return
            try:
                if fault == "drip":
                    for _ in range(100):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(.1)
                    return
                def emit(value):
                    self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
                    self.wfile.flush()
                if fault == "reasoning_only":
                    emit({"choices": [{"delta": {"reasoning_content": "planning"}}]})
                    emit({"choices": [{"delta": {}, "finish_reason": "length"}],
                          "usage": {"prompt_tokens": 256, "completion_tokens": 128}})
                    return
                if fault in {"long_id", "non_string_id"}:
                    emit({"id": "a" * 513 if fault == "long_id" else 7, "choices": []})
                    return
                if fault == "id_change":
                    emit({"id": "first-id", "choices": []})
                    emit({"id": "second-id", "choices": []})
                    return
                if fault == "server_error":
                    emit({"id": "synthetic-engine-request", "error": {"type": "EngineDeadError", "message": "synthetic engine failure"}})
                    return
                if fault == "non_string":
                    emit({"choices": [{"delta": {"reasoning_content": ["invalid"]}}]})
                    return
                if fault == "oversize":
                    self.wfile.write(b"data: " + b"x" * (1024 * 1024 + 1) + b"\n")
                    return
                prompt = body["messages"][0]["content"]
                text = " ".join(re.findall(r"VALUE_[0-9a-f]+", prompt))
                if "40 distinct" in prompt:
                    emit({"choices": [{"delta": {"content": text}}]})
                    assert entered.wait(3)
                    time.sleep(.1)
                    emit({"choices": [{"delta": {"content": "continuing"}}]})
                    advanced.set()
                else:
                    entered.set()
                    assert advanced.wait(3)
                    time.sleep(.05)
                    emit({"choices": [{"delta": {"content": text}}]})
                emit({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 256}})
                self.wfile.write(b"data: [DONE]\n\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = scenario(f"http://127.0.0.1:{server.server_port}/v1", run_timeout_seconds=2 if fault == "drip" else 10)
    row = {"Id": "a" * 64, "Name": "/synthetic-model", "Image": config["configuration"]["image_digest"],
           "Config": {"Labels": {recipes.RECIPE_MANAGED_LABEL: recipes.RECIPE_MANAGED_VALUE,
                     recipes.RECIPE_MODEL_LABEL: "test-model", recipes.RECIPE_REVISION_LABEL: "2" * 40,
                     recipes.RECIPE_DIGEST_LABEL: "0" * 64, recipes.RECIPE_REGISTRY_DIGEST_LABEL: "3" * 64}},
           "State": {"Running": True, "Status": "running"},
           "HostConfig": {"PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(server.server_port)}]}}}
    executable = tmp_path / "docker"
    executable.write_text("#!/usr/bin/env python3\nimport sys\nprint(" + repr("a" * 64) + " if sys.argv[1] == 'ps' else " + repr(json.dumps([row])) + ")\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    source, output = tmp_path / "scenario.json", tmp_path / "out.json"
    source.write_text(json.dumps(config))
    try:
        start = time.monotonic()
        completed = subprocess.run([sys.executable, "-m", "anvil_serving.cli", "eval", "benchmark", "stability",
                                    "--scenario", str(source), "--output", str(output), "--confirm"],
                                   text=True, capture_output=True, timeout=15)
        observed = json.loads(output.read_text())
        assert completed.returncode == (0 if fault is None else 1), completed.stderr + str(observed)
        if fault == "drip":
            assert time.monotonic() - start < 6
            assert observed["failure_class"] == "client_timeout"
            assert observed["server_compute_stopped"] == "unknown"
        elif fault:
            anchor = observed["rounds"][0]["anchor"]
            expected_failure = {"server_error": "server_stream_error", "reasoning_only": "semantic_output_absent"}
            assert anchor["failure_classes"] == [expected_failure.get(fault, "protocol_error")]
            if fault == "server_error":
                assert anchor["stream_id"] == "synthetic-engine-request"
                assert "EngineDeadError" in anchor["server_error"]
            if fault == "reasoning_only":
                assert anchor["result"]["usage"]["completion_tokens"] == 128
                assert anchor["result"]["finish_reasons"] == ["length"]
                assert anchor["result"]["visible_content"] == ""
                assert observed["rounds"][0]["contender"]["status"] in {"not_run", "failed"}
            else:
                assert observed["rounds"][0]["contender"]["status"] == "not_run"
        else:
            assert observed["coverage_passed"] is True
            assert len(observed["identity_observations"]) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
