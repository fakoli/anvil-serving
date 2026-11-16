"""Offline proof of bounded default-relay dispatch and retained diagnostic evidence."""
import hashlib
import json
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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_port, captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


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


def test_cli_error_is_nonzero_and_sanitized(tmp_path, capsys):
    paths = _inputs(tmp_path)
    paths[1].write_bytes(b"private-invalid-json")
    args = ["--router-config", str(paths[0]), "--plan", str(paths[1]),
            "--boundary-config", str(paths[2]), "--output", str(paths[3]), "--confirm"]
    assert probe.main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "error", "error": "JSONDecodeError"}


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
