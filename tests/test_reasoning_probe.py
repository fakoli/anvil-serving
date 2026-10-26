"""Offline proof of bounded default-relay dispatch and retained diagnostic evidence."""
import copy
import hashlib
import json
import stat
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from anvil_serving import reasoning_probe as probe

_PLAN = json.loads((Path(__file__).parents[1] / "docs/benchmarks/reasoning-limit-probe.json").read_text())
_MARKERS = {"start": [1, 2], "end": [9], "forced_end": [9]}


def _inputs(tmp_path, port=1, extras='thinking_token_budget = 49152\ntop_k = 20', plan=None):
    router = tmp_path / "router.toml"
    router.write_text(f'''[router]
[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:{port}/v1"
model = "diagnostic-model"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "SYNTHETIC_PROBE_KEY"
[router.tiers.extra_body]
{extras}
[router.model_routes]
llm.primary = "primary"
''')
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({**_PLAN, "execution_authorized": True,
                                    "live_identity_verified": True} if plan is None else plan))
    boundary = tmp_path / "boundary.json"
    boundary.write_text(json.dumps({**_MARKERS, "expected_model": "diagnostic-model",
        "router_config_sha256": hashlib.sha256(router.read_bytes()).hexdigest()}))
    return router, plan_path, boundary, tmp_path / "result"


def _response(count=64, answer="9567, 1085, 10652", **changes):
    response = {"model": "diagnostic-model", "prompt_token_ids": [10, 1, 2],
                "usage": None, "choices": [{"index": 0, "finish_reason": "stop",
                "token_ids": [3] * count + [9, 10],
                "message": {"role": "assistant", "content": answer}}]}
    response.update(changes)
    return response


@contextmanager
def _server(responses, *, trickle=False, before_response=None, status=200,
            models=None, metrics=None, checks=None, get_status=200, get_trickle=False):
    captured = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def do_GET(self):
            if checks is not None:
                checks.append((self.path, self.headers.get("Authorization")))
            if self.path == "/v1/models":
                raw = json.dumps({"data": [{"id": "diagnostic-model"}]} if models is None else models).encode()
            elif self.path == "/metrics":
                selected = "vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n" if metrics is None else metrics[min(len(captured), len(metrics) - 1)]
                raw = selected.encode()
            else:
                self.send_error(404)
                return
            self._reply(raw, get_status, get_trickle)
        def do_POST(self):
            captured.append(self.rfile.read(int(self.headers["Content-Length"])))
            if before_response:
                before_response()
            selected = responses[min(len(captured) - 1, len(responses) - 1)]
            raw = selected if isinstance(selected, bytes) else json.dumps(selected).encode()
            self._reply(raw, status, trickle)
        def _reply(self, raw, response_status, slowly):
            self.send_response(response_status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                if slowly:
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.02)
                else:
                    self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_preview_never_dispatches_or_writes(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("preview attempted network or output")
    monkeypatch.setattr(probe, "_CaptureRelay", forbidden)
    monkeypatch.setattr(probe, "_Artifacts", forbidden)
    result = probe.run(*paths, False)
    assert result["status"] == "preview" and result["network"] is False
    assert not paths[-1].exists()


def test_default_relay_exact_bytes_controls_and_durable_files(tmp_path):
    with _server([_response()]) as (port, captured):
        paths = _inputs(tmp_path, port)
        before = paths[0].read_bytes()
        result = probe.run(*paths, True)
    assert len(captured) == 1
    output = paths[-1]
    assert (output / "0-request.json").read_bytes() == captured[0]
    body = json.loads(captured[0])
    assert body == {"model": "diagnostic-model", "messages": [{"role": "user", "content": _PLAN["cases"][0]["prompt"]}],
        "stream": False, "max_tokens": 512, "top_k": 20, **probe._CONTROLS}
    record = result["records"][0]
    assert record["terminal"] == "response" and record["oracle"] is True
    assert record["boundary"] == "boundary_consistent_not_forced_provenance"
    assert record["usage"] is None and result["engine_drain"] == "unknown"
    assert result["runtime_verified"] is False and result["promoted"] is False
    assert paths[0].read_bytes() == before and result["shared_configuration_changed"] is False
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for path in output.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for kind in ("request_artifact", "response_artifact"):
        artifact = record[kind]
        assert artifact["sha256"] == hashlib.sha256((output / artifact["file"]).read_bytes()).hexdigest()
    assert (output / "0-start.json").stat().st_mtime_ns <= (output / "0-request.json").stat().st_mtime_ns
    assert len(list(output.glob("*-terminal.json"))) == 1


def test_second_case_requires_natural_close_and_exact_oracle(tmp_path):
    with _server([_response(2), _response(64, "96233,62513,158746")]) as (port, captured):
        result = probe.run(*_inputs(tmp_path, port), True)
    assert len(captured) == len(result["records"]) == 2
    assert all(record["oracle"] for record in result["records"])


@pytest.mark.parametrize("alter", ["missing_ids", "wrong_model", "extra_answer", "wrong_oracle", "length", "duplicate_end", "bad_ids", "extra_choice", "tool_call"])
def test_anomalies_prevent_second_request(tmp_path, alter):
    response = _response(2)
    choice = response["choices"][0]
    if alter == "missing_ids":
        del response["prompt_token_ids"]
    elif alter == "wrong_model":
        response["model"] = "other-model"
    elif alter == "extra_answer":
        choice["message"]["content"] = "Answer: 9567,1085,10652"
    elif alter == "wrong_oracle":
        choice["message"]["content"] = "9567,1085,10653"
    elif alter == "length":
        choice["finish_reason"] = "length"
    elif alter == "duplicate_end":
        choice["token_ids"].append(9)
    elif alter == "bad_ids":
        choice["token_ids"] = [True, 9]
    elif alter == "extra_choice":
        response["choices"].append(copy.deepcopy(choice))
    else:
        choice["message"]["tool_calls"] = [{"function": {"name": "unexpected", "arguments": "{}"}}]
    with _server([response]) as (port, captured):
        result = probe.run(*_inputs(tmp_path, port), True)
    assert len(captured) == len(result["records"]) == 1
    if alter == "missing_ids":
        assert result["records"][0]["terminal"] == "response"
        assert result["records"][0]["boundary"] == "unknown"


@pytest.mark.parametrize("raw", [b"{broken JSON secret-marker", b"[]", b'{"choices": "bad"}'])
def test_malformed_raw_retained_with_one_sanitized_terminal(tmp_path, raw):
    with _server([raw]) as (port, captured):
        paths = _inputs(tmp_path, port)
        result = probe.run(*paths, True)
    record = result["records"][0]
    assert result["status"] == "error" and record["terminal"] == "error"
    assert (paths[-1] / "0-response.json").read_bytes() == raw
    assert (paths[-1] / "0-request.json").read_bytes() == captured[0]
    assert "secret-marker" not in json.dumps(result)
    assert len(list(paths[-1].glob("*-terminal.json"))) == 1


def test_refusal_preserves_start_request_and_terminal(tmp_path):
    with _server([b"private refusal"], status=503) as (port, captured):
        paths = _inputs(tmp_path, port)
        result = probe.run(*paths, True)
    assert len(captured) == 1 and result["status"] == "error"
    assert result["records"][0]["response_artifact"] is None
    assert (paths[-1] / "0-request.json").exists()
    assert (paths[-1] / "0-start.json").exists()
    assert (paths[-1] / "0-terminal.json").exists()


def test_absolute_deadline_interrupts_post_header_trickle(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "_REQUEST_SECONDS", 0.15)
    with _server([_response()], trickle=True) as (port, captured):
        started = time.monotonic()
        result = probe.run(*_inputs(tmp_path, port), True)
        elapsed = time.monotonic() - started
    assert elapsed < 1.5 and len(captured) == 1
    assert result["status"] == "error" and result["records"][0]["terminal"] == "error"
    assert result["records"][0]["error"] in {"RequestCancelledError", "RequestDeadlineExceeded"}
    assert result["engine_drain"] == "unknown"


def test_configuration_change_is_reported_and_stops_next_dispatch(tmp_path):
    paths = None
    def mutate():
        with paths[0].open("a") as output:
            output.write("\n# changed concurrently\n")
    with _server([_response(2)], before_response=mutate) as (port, captured):
        paths = _inputs(tmp_path, port)
        result = probe.run(*paths, True)
    assert len(captured) == 1 and result["status"] == "error"
    assert result["shared_configuration_changed"] is True
    assert result["router_config_sha256_before"] != result["router_config_sha256_after"]
    assert result["records"][1]["error"] == "ValueError"


@pytest.mark.parametrize("key,value", [("limits", {**probe._LIMITS, "retries": 1}),
    ("request_common", {**probe._COMMON, "n": 2}),
    ("request_common", {**probe._COMMON, "stream": 0}),
    ("isolated_tier_extra_body", {**probe._SETTINGS, "thinking_token_budget": 65}),
    ("runtime", {**probe._RUNTIME, "speculative_tokens": True}),
    ("cases", list(reversed(probe._CASES)))])
def test_pinned_plan_rejects_changed_contract_before_writes(tmp_path, key, value):
    plan = copy.deepcopy(_PLAN)
    plan[key] = value
    paths = _inputs(tmp_path, plan=plan)
    with pytest.raises(ValueError):
        probe.run(*paths, True)
    assert not paths[-1].exists()


@pytest.mark.parametrize("extras", ['model = "other"', 'max_tokens = 10000', 'n = 2', 'stop = ["x"]', 'messages = []'])
def test_tier_cannot_override_contract(tmp_path, extras):
    paths = _inputs(tmp_path, extras=extras)
    with pytest.raises(ValueError):
        probe.run(*paths, True)
    assert not paths[-1].exists()


@pytest.mark.parametrize("key,value", [("start", [True]), ("end", [-1]), ("forced_end", [8]), ("router_config_sha256", "0" * 64)])
def test_boundary_config_is_strict(tmp_path, key, value):
    paths = _inputs(tmp_path)
    boundary = json.loads(paths[2].read_text())
    boundary[key] = value
    paths[2].write_text(json.dumps(boundary))
    with pytest.raises(ValueError):
        probe.run(*paths, True)
    assert not paths[-1].exists()


def test_artifact_exclusivity_and_symlink_rejection(tmp_path):
    output = tmp_path / "output"
    artifacts = probe._Artifacts(output)
    try:
        artifacts.write("once", b"original")
        with pytest.raises(FileExistsError):
            artifacts.write("once", b"replace")
        (output / "linked").symlink_to(tmp_path / "outside")
        with pytest.raises(FileExistsError):
            artifacts.write("linked", b"escape")
    finally:
        artifacts.close()
    assert (output / "once").read_bytes() == b"original"
    with pytest.raises(FileExistsError):
        probe._Artifacts(output)
    (tmp_path / "link").symlink_to(output, target_is_directory=True)
    with pytest.raises(OSError):
        probe._Artifacts(tmp_path / "link" / "escape")
    assert not (output / "escape").exists()


def test_pinned_directory_survives_path_swap(tmp_path):
    output = tmp_path / "output"
    artifacts = probe._Artifacts(output)
    moved = tmp_path / "moved"
    output.rename(moved)
    output.symlink_to(tmp_path, target_is_directory=True)
    try:
        artifacts.write("evidence", b"retained")
    finally:
        artifacts.close()
    assert (moved / "evidence").read_bytes() == b"retained"
    assert not (tmp_path / "evidence").exists()


def test_cli_error_is_nonzero_and_sanitized(tmp_path, capsys):
    paths = _inputs(tmp_path)
    paths[1].write_bytes(b"private-invalid-json")
    args = ["--router-config", str(paths[0]), "--plan", str(paths[1]),
            "--boundary-config", str(paths[2]), "--output", str(paths[3]), "--confirm"]
    assert probe.main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "error", "error": "JSONDecodeError"}


def test_boundary_positions_and_repeated_markers():
    assert probe._boundary([1, 2], [3] * 64 + [9], _MARKERS) == "boundary_consistent_not_forced_provenance"
    assert probe._boundary([1, 2], [3] * 63 + [9], _MARKERS) == "natural_below_budget"
    assert probe._boundary([1, 2], [3] * 65 + [9], _MARKERS) == "invalid_after_budget"
    assert probe._boundary([1, 2], [3, 9, 9], _MARKERS) == "invalid_repeated_or_missing_close"
    assert probe._boundary([1, 2], [1, 2, 3, 9], _MARKERS) == "invalid_repeated_or_nested_boundary"
    assert probe._boundary([1, 2], [3], _MARKERS) == "unknown"
    assert probe._boundary([1, 2, 3, 9, 1, 2], [3, 9], _MARKERS) == "natural_below_budget"


def test_all_request_control_phases_use_pinned_deadline(tmp_path, monkeypatch):
    controls = []
    original = probe.RequestControl
    def capture(**kwargs):
        controls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(probe, "RequestControl", capture)
    with _server([_response()]) as (port, _):
        probe.run(*_inputs(tmp_path, port), True)
    assert controls == [{"admission_timeout_s": duration, "startup_timeout_s": duration,
                         "idle_timeout_s": duration, "total_timeout_s": duration}
                        for duration in (5, 5, 120)]


def test_effective_body_is_checked_before_dispatch(tmp_path, monkeypatch):
    original = probe.RelayBackend._build_body
    def overridden(self, request):
        body = original(self, request)
        body["max_tokens"] = 999
        return body
    monkeypatch.setattr(probe.RelayBackend, "_build_body", overridden)
    with _server([_response()]) as (port, captured):
        paths = _inputs(tmp_path, port)
        result = probe.run(*paths, True)
    assert captured == [] and result["status"] == "error"
    assert result["records"][0]["error"] == "ValueError"
    assert json.loads((paths[-1] / "0-request.json").read_bytes())["max_tokens"] == 999


def test_window_reserves_cleanup_and_never_starts_late(tmp_path, monkeypatch):
    # Leave insufficient room for another full request plus cleanup.
    monkeypatch.setattr(probe, "_WINDOW_SECONDS", 179)
    with _server([_response()]) as (port, captured):
        result = probe.run(*_inputs(tmp_path, port), True)
    assert captured == [] and result["records"] == [] and result["status"] == "error"


def test_input_size_duplicate_keys_and_symlinks_fail_closed(tmp_path):
    paths = _inputs(tmp_path)
    paths[1].write_bytes(b" " * (probe._MAX_BYTES + 1))
    with pytest.raises(ValueError, match="size"):
        probe.run(*paths, True)
    paths[1].write_bytes(b'{"schema":"one","schema":"two"}')
    with pytest.raises(ValueError, match="duplicate"):
        probe.run(*paths, True)
    paths[1].unlink()
    paths[1].symlink_to(paths[2])
    with pytest.raises(OSError):
        probe.run(*paths, True)
    assert not paths[-1].exists()


def test_oversized_response_is_bounded_and_terminal(tmp_path):
    with _server([b"x" * (probe._MAX_BYTES + 1)]) as (port, captured):
        paths = _inputs(tmp_path, port)
        result = probe.run(*paths, True)
    assert len(captured) == 1 and result["status"] == "error"
    assert result["records"][0]["response_artifact"] is None
    assert all(path.stat().st_size <= probe._MAX_BYTES for path in paths[-1].iterdir())


@pytest.mark.parametrize("flags", [(False, False), (False, True), (True, False)])
def test_historical_authority_flags_allow_preview_but_not_execution(tmp_path, flags):
    plan = {**_PLAN, "execution_authorized": flags[0], "live_identity_verified": flags[1]}
    with _server([_response()]) as (port, captured):
        paths = _inputs(tmp_path, port, plan=plan)
        assert probe.run(*paths, False)["status"] == "preview"
        with pytest.raises(ValueError, match="operator-attested"):
            probe.run(*paths, True)
        assert captured == [] and not paths[-1].exists()


@pytest.mark.parametrize("url", ["http://100.64.0.10:30000/v1", "http://example.test:30000/v1",
    "http://user:private-marker@127.0.0.1:30000/v1", "http://127.0.0.1:30000/v1?token=private-marker",
    "http://127.0.0.1:30000/v1#private-marker", "https://127.0.0.1:30000/v1", "http://127.1:30000/v1",
    "http://2130706433:30000/v1", "http://127.0.0.1/v1", "http://127.0.0.1:30000/other/../v1",
    "http://127.0.0.1:30000/v1/", "http://127.0.0.1:030000/v1", "http://127.0.0.1:30000/v1?",
    "http://127.0.0.1:30000/v1#"])
def test_endpoint_tricks_fail_before_output_or_auth_resolution(tmp_path, monkeypatch, url):
    paths = _inputs(tmp_path)
    paths[0].write_text(paths[0].read_text().replace("http://127.0.0.1:1/v1", url))
    boundary = json.loads(paths[2].read_bytes())
    boundary["router_config_sha256"] = hashlib.sha256(paths[0].read_bytes()).hexdigest()
    paths[2].write_text(json.dumps(boundary))
    def forbidden(*args):
        raise AssertionError("invalid endpoint attempted authorization/network")
    monkeypatch.setattr(probe, "_owner_state", forbidden)
    with pytest.raises(ValueError) as error:
        probe.run(*paths, True)
    assert "private-marker" not in str(error.value)
    assert not paths[-1].exists()


def test_fresh_owner_gets_each_request_use_same_auth_without_summary_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PROBE_KEY", "synthetic-test-only")
    checks = []
    with _server([_response(2), _response(64, "96233,62513,158746")], checks=checks) as (port, captured):
        result = probe.run(*_inputs(tmp_path, port), True)
    assert len(captured) == 2
    assert checks == [(path, "Bearer synthetic-test-only") for path in
                      ("/v1/models", "/metrics", "/v1/models", "/metrics")]
    assert "synthetic-test-only" not in json.dumps(result)
    assert all(record["owner_state"]["running"] == record["owner_state"]["waiting"] == 0
               for record in result["records"])
    assert result["runtime_verified"] is False
    assert result["runtime_identity_authority"] == "operator_attested"


def test_busy_owner_before_second_case_stops_second_post(tmp_path):
    metrics = ["vllm:num_requests_running{model_name=\"diagnostic-model\"} 0\nvllm:num_requests_waiting 0\n",
               "vllm:num_requests_running 1\nvllm:num_requests_waiting 2\n"]
    with _server([_response(2)], metrics=metrics) as (port, captured):
        result = probe.run(*_inputs(tmp_path, port), True)
    assert len(captured) == 1 and result["status"] == "error"
    second = result["records"][1]
    assert second["owner_state"]["running"] == 1 and second["owner_state"]["waiting"] == 2
    assert second["owner_state"]["idle"] is False and second["request_artifact"] is None
    assert result["engine_drain"] == "unknown"


@pytest.mark.parametrize("kwargs", [
    {"models": {"data": [{"id": "other-model"}]}},
    {"models": {"data": [{"id": "diagnostic-model"}, {"id": "other-model"}]}},
    {"metrics": ["vllm:num_requests_running 0\n"]},
    {"metrics": ["vllm:num_requests_running NaN\nvllm:num_requests_waiting 0\n"]},
    {"metrics": ["vllm:num_requests_running 0\nvllm:num_requests_waiting 1\n"]},
    {"get_status": 302}, {"get_status": 401},
])
def test_unverified_identity_or_occupancy_never_posts(tmp_path, kwargs):
    with _server([_response()], **kwargs) as (port, captured):
        result = probe.run(*_inputs(tmp_path, port), True)
    assert captured == [] and result["status"] == "error"
    assert result["records"][0]["request_artifact"] is None


def test_owner_get_trickle_has_absolute_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "_CHECK_SECONDS", 0.1)
    with _server([_response()], get_trickle=True) as (port, captured):
        started = time.monotonic()
        result = probe.run(*_inputs(tmp_path, port), True)
        elapsed = time.monotonic() - started
    assert elapsed < 1 and captured == [] and result["status"] == "error"


@pytest.mark.parametrize("usage", ["private-marker", {"opaque": "private-marker"},
    {"prompt_tokens": True}, {"completion_tokens": -1}, {"completion_tokens": 1_000_000_001},
    {"completion_tokens": 3}, {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 8},
    {"completion_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 5}},
    {"completion_tokens_details": {"reasoning_tokens": "private-marker"}},
    {"prompt_tokens_details": {"cached_tokens": 0, "opaque": "private-marker"}},
    {"completion_tokens_details": "private-marker"},
    {"reasoning_tokens": 999999999}, {"prompt_tokens_details": {"cached_tokens": 4}},
    {"prompt_tokens_details": {"created_cache_tokens": 4}},
    {"prompt_tokens_details": {"multimodal_tokens": {"private-marker": 1}}},
    {"completion_tokens_details": {"reasoning_tokens": 3}},
    {"reasoning_tokens": 1, "completion_tokens_details": {"reasoning_tokens": 2}}])
def test_invalid_or_opaque_usage_stops_second_and_is_not_echoed(tmp_path, usage):
    with _server([_response(2, usage=usage)]) as (port, captured):
        paths = _inputs(tmp_path, port)
        result = probe.run(*paths, True)
    assert len(captured) == 1
    record = result["records"][0]
    assert record["response_anomalies"] and record["oracle"] is True
    assert "private-marker" not in json.dumps(result)
    assert json.loads((paths[-1] / "0-response.json").read_bytes())["usage"] == usage


def test_nullable_valid_usage_projection_preserves_native_counts():
    usage = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7,
             "prompt_tokens_details": {"cached_tokens": None, "created_cache_tokens": 0,
                                       "multimodal_tokens": None},
             "completion_tokens_details": {"reasoning_tokens": 2}}
    result = probe._analyze(json.dumps(_response(2, usage=usage)).encode(),
                            probe._CASES[0], {**_MARKERS, "expected_model": "diagnostic-model"})
    assert result["usage"] == usage and result["response_anomalies"] == []


@pytest.mark.parametrize("finish", ["private-marker", {"opaque": "private-marker"}, None])
def test_opaque_finish_reason_is_unknown_not_echoed(finish):
    response = _response(2)
    response["choices"][0]["finish_reason"] = finish
    result = probe._analyze(json.dumps(response).encode(), probe._CASES[0],
                            {**_MARKERS, "expected_model": "diagnostic-model"})
    assert result["finish_reason"] == "unknown" and result["response_anomalies"]
    assert "private-marker" not in json.dumps(result)


def test_candidate_patch_is_explicit_and_preserves_original_packet(tmp_path):
    plan = copy.deepcopy(_PLAN)
    plan["runtime"]["image_digest"] = "sha256:" + "a" * 64
    paths = _inputs(tmp_path, plan=plan)
    with pytest.raises(ValueError, match="runtime differs"):
        probe.run(*paths, False)
    plan["runtime_patch"] = {
        "base_image_digest": probe._RUNTIME["image_digest"],
        "base_source_sha256": probe._PATCH_BASE_SOURCE,
        "upstream_commit": probe._PATCH_COMMIT,
        "source_sha256": probe._PATCH_SOURCE,
    }
    paths[1].write_text(json.dumps(plan))
    assert probe.run(*paths, False)["network"] is False
    for key in plan["runtime_patch"]:
        altered = copy.deepcopy(plan)
        altered["runtime_patch"][key] = "unreviewed"
        paths[1].write_text(json.dumps(altered))
        with pytest.raises(ValueError, match="pinned upstream"):
            probe.run(*paths, False)
    for source_hash in ("b" * 64, probe._PATCH_BASE_SOURCE):
        altered = copy.deepcopy(plan)
        altered["runtime_patch"]["source_sha256"] = source_hash
        with pytest.raises(ValueError, match="pinned upstream"):
            probe._validate_runtime_patch(altered)
    plan["runtime"]["image_digest"] = probe._RUNTIME["image_digest"]
    with pytest.raises(ValueError, match="distinct immutable"):
        probe._validate_runtime_patch(plan)
    assert not paths[-1].exists()
