"""Guarded transports preserve native stream bytes; optional pinned LIL parser checks."""
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking import performance_adapter as adapter
from anvil_serving.benchmarking.swe_agent_adapter import BudgetStopped


def plan_at(tmp_path, runner="lil", calls=2):
    return {
        "runner": runner, "adapter_sha256": "a" * 64,
        "endpoint": {"base_url": "http://127.0.0.1:30000/v1", "model": "exact-alias"},
        "parameters": {"runner": runner, "request_count": calls, "concurrency": 1,
            "warmup_requests": 0, "cache_condition": "basic",
            "workload": {"input_tokens": 0 if runner == "lil" else 16, "output_tokens": 8}},
        "request_controls": {}, "timeout_s": 20,
        "budgets": {"max_model_calls": calls, "max_completion_tokens": 8,
            "deadline_s": 20, "max_retries": 0, "min_free_disk_bytes": 0},
        "paths": {"request_events": str(tmp_path / "requests.jsonl"),
                  "native_output": str(tmp_path / "native.json"), "output": str(tmp_path / "native")},
    }


def policy_at(tmp_path, runner="lil", calls=2):
    plan = plan_at(tmp_path, runner, calls)
    ledger = adapter.AttemptLedger(plan)
    return adapter.TransportPolicy(plan, ledger, "test-only-token")


def payload():
    return {"model": "exact-alias", "stream": True, "max_tokens": 8,
            "messages": [{"role": "user", "content": "fixture"}]}


@pytest.mark.parametrize("wire,reason", [
    (b'data: {broken}\n\n', "invalid_json"),
    (b'data: {"n": NaN}\n\n', "invalid_json"),
    (b'data: []\n\n', "invalid_json_object"),
    (b'data: {"error": {}}\n\n', "error_event"),
    (b'event: error\n\n', "error_event"),
    (b'data: "\xff"\n\n', "invalid_utf8"),
    (b'not SSE\n\n', "invalid_framing"),
    (b'data: {}\rdata: {}\n\n', "invalid_framing"),
    (b'data: ' + b'x' * adapter.MAX_SSE_EVENT_BYTES, "event_too_large"),
    (b'data: {}\n\n', "truncated_stream"),
    (b'data: [DONE]\n', "truncated_stream"),
    (b'data: [DONE]\n\ndata: {}\n\n', "data_after_terminal"),
])
def test_sse_observer_rejects_wire_defects_without_retaining_payload(tmp_path, wire, reason):
    policy = policy_at(tmp_path, calls=1)
    _, model_call = policy.check("POST", policy.inference_url,
        {"Authorization": "Bearer test-only-token"}, payload())
    observer = adapter.SSEObserver(policy.ledger, model_call)
    try:
        observer.feed(wire)
    except adapter.StreamProtocolError:
        pass
    observer.finish()
    observer.finish()  # Close and iterator unwind cannot duplicate an observation.
    event = json.loads(policy.ledger.path.read_text().splitlines()[-1])
    assert event["outcome"] == "protocol_failure" and event["reason"] == reason
    assert policy.ledger.snapshot()["stream_validation"]["observed"] == 1
    assert len(observer.line) <= adapter.MAX_SSE_EVENT_BYTES


def test_httpx_sync_preserves_chunks_and_observes_split_crlf(tmp_path):
    httpx = pytest.importorskip("httpx")
    policy = policy_at(tmp_path, calls=1)
    chunks = [b'data: {"choices": []}\r', b'\n\r\n', b'data: [DONE]\r\n', b'\r', b'\n']
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            return iter(chunks)
    restore = adapter.install_httpx_guards(httpx, policy)
    try:
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
                200, stream=Stream(), headers={"content-type": "text/event-stream"})),
                headers={"Authorization": "Bearer test-only-token"}) as client:
            with client.stream("POST", policy.inference_url, json=payload()) as response:
                assert list(response.iter_raw()) == chunks
    finally:
        restore()
    assert policy.ledger.snapshot()["stream_validation"]["complete"] is True


def test_native_arguments_have_no_secret_or_optional_traffic(tmp_path):
    for runner in ("lil", "aiperf"):
        plan = plan_at(tmp_path, runner)
        args = adapter.native_arguments(plan)
        assert "--api-key" not in args and "--config" not in args
        assert "--request-count" in args
        if runner == "lil":
            assert args[args.index("--host") + 1] == "http://127.0.0.1:30000"
        else:
            assert args[args.index("--workers-max") + 1] == "1"
            assert args[args.index("--tokenizer") + 1] == "builtin"
            assert "--warmup-request-count" not in args
        adapter.validate_supported_parameters(plan["parameters"])
        plan["parameters"]["cache_condition"] = "cold"
        with pytest.raises(ValueError):
            adapter.validate_supported_parameters(plan["parameters"])


@pytest.mark.parametrize("change", ["alias", "cap", "stream", "controls", "auth", "admin", "origin"])
def test_policy_rejects_before_send(tmp_path, change):
    policy = policy_at(tmp_path)
    body, url, headers = payload(), policy.inference_url, {"Authorization": "Bearer test-only-token"}
    if change == "alias":
        body["model"] = "fallback"
    elif change == "cap":
        body["max_tokens"] = 9
    elif change == "stream":
        body["stream"] = False
    elif change == "controls":
        policy.controls = {"reasoning_effort": "high"}
    elif change == "auth":
        headers = {}
    elif change == "admin":
        url = policy.origin + "/abort_request"
    elif change == "origin":
        url = "http://127.0.0.1:30001/v1/chat/completions"
    with pytest.raises(BudgetStopped):
        policy.check("POST", url, headers, body)
    assert policy.ledger.snapshot()["model_calls"] == 0


def test_httpx_sync_failure_attempt_and_redirect_are_bounded(tmp_path):
    httpx = pytest.importorskip("httpx")
    policy = policy_at(tmp_path, calls=2)
    seen = []
    def respond(request):
        seen.append(request)
        if len(seen) == 1:
            raise httpx.ConnectError("fixture failure")
        return httpx.Response(307, headers={"location": policy.inference_url})
    restore = adapter.install_httpx_guards(httpx, policy)
    try:
        with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True,
                          headers={"Authorization": "Bearer test-only-token"}) as client:
            with pytest.raises(httpx.ConnectError):
                client.post(policy.inference_url, json=payload())
            with pytest.raises(adapter.StreamProtocolError, match="response_status_or_content_type"):
                client.post(policy.inference_url, json=payload())
            with pytest.raises(BudgetStopped):
                client.post(policy.inference_url, json=payload())
    finally:
        restore()
    assert len(seen) == 2
    assert policy.ledger.snapshot()["model_calls"] == 2
    assert all(0 < value <= 20 for value in seen[0].extensions["timeout"].values())
    policy.ledger.close("budget_exhausted")
    events = [json.loads(line) for line in policy.ledger.path.read_text().splitlines()]
    assert [row["seq"] for row in events] == list(range(1, len(events) + 1))
    assert "test-only-token" not in policy.ledger.path.read_text()
    assert events[-1]["byte_count"] == sum(len(line) for line in policy.ledger.path.read_bytes().splitlines(keepends=True)[:-1])


def test_probe_cap_is_separate_and_admin_requests_fail_closed(tmp_path):
    policy = policy_at(tmp_path)
    headers = {"Authorization": "Bearer test-only-token"}
    policy.check("GET", policy.origin + "/v1/models", headers, None)
    assert policy.ledger.snapshot()["model_calls"] == 0
    for _ in range(adapter.MAX_PROBE_ATTEMPTS - 1):
        policy.check("GET", policy.origin + "/v1/models", headers, None)
    with pytest.raises(BudgetStopped):
        policy.check("GET", policy.origin + "/v1/models", headers, None)
    assert policy.ledger.snapshot()["http_calls"] == adapter.MAX_PROBE_ATTEMPTS


def test_aiohttp_guard_preserves_native_response_and_fork_budget(tmp_path):
    aiohttp = pytest.importorskip("aiohttp")
    policy = policy_at(tmp_path, "aiperf", calls=1)
    chunks = [b'data: {"choices": []}\r', b'\n\r', b'\ndata: [DONE]\r\n\r', b'\n']
    class Content:
        async def iter_any(self):
            for chunk in chunks:
                yield chunk
    class Response:
        status = 200
        headers = {"content-type": "text/event-stream"}
        def __init__(self):
            self.content = Content()
        def release(self):
            pass
        def close(self):
            pass
    class Session:
        trust_env = False
        headers = {"Authorization": "Bearer test-only-token"}
        timeout = aiohttp.ClientTimeout(total=100)
        async def _request(self, method, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            assert kwargs["timeout"].total <= 20
            return Response()
    module = SimpleNamespace(ClientSession=Session, ClientTimeout=aiohttp.ClientTimeout)
    restore = adapter.install_aiohttp_guard(module, policy)
    async def send():
        response = await Session()._request("POST", policy.inference_url, data=json.dumps(payload()).encode())
        result = [chunk async for chunk in response.content.iter_any()]
        response.release()
        return result
    def child():
        assert asyncio.run(send()) == chunks
    try:
        process = multiprocessing.get_context("fork").Process(target=child)
        process.start()
        process.join(5)
        assert process.exitcode == 0
        with pytest.raises(BudgetStopped):
            asyncio.run(send())
    finally:
        restore()
    assert policy.ledger.snapshot()["model_calls"] == 1
    assert policy.ledger.snapshot()["stream_validation"]["complete"] is True


@pytest.mark.parametrize("case", ["reasoning_bundled", "missing_usage", "malformed", "disconnect", "cancellation", "crlf"])
def test_guarded_native_lil_stream_parser(tmp_path, case):
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("rich")
    source = os.environ.get("ANVIL_PERFORMANCE_LIL_SOURCE")
    if not source:
        pytest.skip("set ANVIL_PERFORMANCE_LIL_SOURCE to the immutable native source for parser qualification")
    source = Path(source)
    import hashlib
    assert hashlib.sha256(source.read_bytes()).hexdigest() == adapter.SOURCE_HASHES["lil"]["llm_decode_bench.py"]
    native = runpy.run_path(str(source), run_name="anvil_lil_test")
    policy = policy_at(tmp_path, calls=1)
    cancel = asyncio.Event()
    def event(value):
        separator = "\r\n" if case == "crlf" else "\n"
        return ("data: " + json.dumps(value) + separator * 2).encode()
    reasoning = event({"id": "fixture", "choices": [{"delta": {"reasoning_content": "reason"}}]})
    content = event({"choices": [{"delta": {"content": "two words"}}]})
    usage = event({"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 7}})
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield reasoning + content  # Bundled network chunks remain separate native SSE events.
            if case == "disconnect":
                raise httpx.ReadError("fixture disconnect")
            if case == "cancellation":
                cancel.set()
                raise asyncio.CancelledError()
            if case == "malformed":
                yield b"data: {broken json\n\n"
            if case != "missing_usage":
                yield usage
            terminal = b"data: [DONE]\r\n\r\n" if case == "crlf" else b"data: [DONE]\n\n"
            for byte in terminal:
                yield bytes([byte])  # Split both CRLF pairs across transport chunks.
    async def run():
        samples, chunks = [], [0]
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Stream(), headers={"content-type": "text/event-stream"})),
                headers={"Authorization": "Bearer test-only-token"}) as client:
            result = await native["stream_one_request"](client, policy.inference_url, payload(),
                0, cancel, chunks, shared_request_samples=samples,
                shared_started_count=[0], shared_completed_count=[0], target_request_count=1)
        return result, samples, chunks
    restore = adapter.install_httpx_guards(httpx, policy)
    try:
        result, samples, chunks = asyncio.run(run())
    finally:
        restore()
    assert policy.ledger.snapshot()["model_calls"] == 1
    assert chunks == [2]
    streams = policy.ledger.snapshot()["stream_validation"]
    expected = "cancelled" if case == "cancellation" else (
        "protocol_failure" if case in {"malformed", "disconnect"} else "valid")
    assert streams[expected] == streams["observed"] == 1
    assert streams["complete"] is (expected == "valid")
    if case in {"reasoning_bundled", "crlf"}:
        assert samples[0].output_tokens == 7
        assert samples[0].ttft >= 0
    elif case == "missing_usage":
        assert samples[0].output_tokens == 2  # Native chunk fallback must not be labelled token usage.
    elif case in {"disconnect", "malformed"}:
        assert result.error
    else:
        assert cancel.is_set()


@pytest.mark.parametrize("runner", ["lil", "aiperf"])
@pytest.mark.parametrize("case", ["reasoning_bundled", "missing_usage", "malformed", "disconnect", "cancellation",
                                 "crlf", "oversize", "unterminated"])
def test_prepared_native_process_fake_sse(tmp_path, runner, case):
    """Opt-in real pinned native process probe; its only endpoint is a local fixture."""
    import hashlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import socket
    import subprocess
    import threading
    import time

    receipt_path = os.environ.get("ANVIL_PERFORMANCE_SMOKE_RECEIPT")
    if not receipt_path:
        pytest.skip("set ANVIL_PERFORMANCE_SMOKE_RECEIPT for prepared native process qualification")
    prepared = json.loads(Path(receipt_path).read_text())
    receipt = prepared["environments"][runner]
    from uuid import uuid4
    native_secret = "anvil-perf-canary-" + uuid4().hex
    controls = {"reasoning_effort": "high", "sampling": {
        "temperature": {"sent": True, "effective_request": 0.25},
        "top_p": {"sent": True, "effective_request": 0.8}}}
    seen = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass
        def do_GET(self):
            seen.append(("GET", self.path))
            body = json.dumps({"data": [{"id": "exact-alias"}]} if self.path == "/v1/models" else {}).encode()
            self.send_response(200 if self.path == "/v1/models" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("POST", self.path, body))
            assert self.path == "/v1/chat/completions" and body["model"] == "exact-alias"
            assert self.headers["Authorization"] == "Bearer " + native_secret
            assert body["reasoning_effort"] == "high" and body["temperature"] == 0.25 and body["top_p"] == 0.8
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            def event(value):
                separator = "\r\n" if case == "crlf" else "\n"
                return ("data: " + json.dumps(value) + separator * 2).encode()
            try:
                self.wfile.write(event({"id": "fixture", "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"reasoning_content": "reason"}}]}) +
                    event({"id": "fixture", "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": "two words"}}]}))
                self.wfile.flush()
                if case == "disconnect":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                if case == "cancellation":
                    time.sleep(3)
                    return
                if case == "malformed":
                    self.wfile.write(b"data: {broken json\n\n")
                if case == "oversize":
                    self.wfile.write(b"data: " + b"x" * adapter.MAX_SSE_EVENT_BYTES + b"\n\n")
                if case != "missing_usage":
                    self.wfile.write(event({"id": "fixture", "object": "chat.completion.chunk", "choices": [],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 7, "total_tokens": 11}}))
                if case == "crlf":
                    for chunk in (b"data: [DONE]\r", b"\n\r", b"\n"):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        time.sleep(0.01)
                else:
                    self.wfile.write(b"data: [DONE]\n" if case == "unterminated" else b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    plan = plan_at(tmp_path, runner, calls=1)
    plan["request_controls"] = controls
    plan["endpoint"]["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
    plan.update(environment=receipt, cache_root=prepared["cache_root"], plan_sha256="f" * 64,
                adapter_sha256=hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest())
    plan["paths"].update(work=str(tmp_path / "work"), home=str(tmp_path / "home"),
                          adapter_result=str(tmp_path / "adapter-result.json"))
    plan["paths"]["native_output"] = str(tmp_path / "native" / (
        "lil.json" if runner == "lil" else "profile_export_aiperf.json"))
    for key in ("work", "home", "output"):
        Path(plan["paths"][key]).mkdir()
    plan["timeout_s"] = plan["budgets"]["deadline_s"] = 45
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan))
    env = {"PATH": os.environ["PATH"], "HOME": plan["paths"]["home"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "OPENAI_API_KEY": native_secret, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    if receipt.get("tokenizer"):
        env["TIKTOKEN_CACHE_DIR"] = str(Path(prepared["cache_root"]) / receipt["tokenizer"]["cache_key"])
    executable = str(Path(prepared["cache_root"]) / receipt["cache_key"] / receipt["executable"])
    command = [executable, "-B", "-c", "import json,sys;sys.path.insert(0,sys.argv.pop(1));from anvil_serving.benchmarking.performance_adapter import run_plan;run_plan(json.load(open(sys.argv[1])))", str(Path(adapter.__file__).resolve().parents[2]), str(plan_file)]
    try:
        process = subprocess.Popen(command, cwd=plan["paths"]["work"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        if case == "cancellation":
            deadline = time.monotonic() + 40
            while not any(row[0] == "POST" for row in seen) and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            import signal
            os.killpg(process.pid, signal.SIGINT)
        try:
            output, _ = process.communicate(timeout=50)
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
            pytest.fail("native fake-SSE process exceeded timeout: " + output[-1500:])
    finally:
        server.shutdown()
        server.server_close()
    (tmp_path / "native-process.log").write_text(output)
    assert sum(row[0] == "POST" for row in seen) == 1, output[-3000:]
    events = [json.loads(line) for line in Path(plan["paths"]["request_events"]).read_text().splitlines()]
    assert sum(row["event"] == "performance_transport_started" for row in events) == 1
    if runner == "aiperf":
        attempts = [row for row in events if row["event"] == "performance_transport_started"]
        assert len({row["pid"] for row in attempts}) == 1
        assert attempts[0]["pid"] != process.pid
        assert any(row["event"] == "performance_worker_start_method" and row["start_method"] == "fork" for row in events)
    report_path = Path(plan["paths"]["adapter_result"])
    if case == "cancellation":
        if report_path.exists():
            terminal = json.loads(report_path.read_text())
            assert terminal["outcome"] == "cancelled", output[-3000:]
            assert terminal["stream_validation"]["complete"] is False
        else:
            assert not any(row["event"] == "adapter_finished" for row in events)
    elif case in {"reasoning_bundled", "missing_usage", "crlf"}:
        assert process.returncode == 0, output[-3000:]
        terminal = json.loads(report_path.read_text())
        assert terminal["outcome"] == "completed"
        assert terminal["stream_validation"]["valid"] == 1
        assert terminal["stream_validation"]["usage_observed"] == int(case != "missing_usage")
        assert terminal["token_usage_verified"] is False
        assert Path(plan["paths"]["native_output"]).is_file(), output[-3000:]
    else:
        terminal = json.loads(report_path.read_text())
        assert terminal["outcome"] == "protocol_failure", output[-3000:]
        assert terminal["stream_validation"]["protocol_failure"] == 1
        assert terminal["stream_validation"]["complete"] is False

    for artifact in tmp_path.rglob("*"):
        if artifact.is_file() and not artifact.is_symlink():
            assert native_secret.encode() not in artifact.read_bytes(), f"credential leaked into {artifact.name}"
