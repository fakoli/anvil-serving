"""Pinned native performance launchers; third-party imports stay in prepared Python."""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import time
import types
from urllib.parse import urlsplit

from .aider_adapter import request_body_controls
from .swe_agent_adapter import BudgetStopped, validate_budgets

LIL_REVISION = "533ef609325390361f6bd8a3640bb4764454a7bd"
AIPERF_REVISION = "794f8bb75f8582f22e412d7e650fc71ca2a3d21a"
SOURCE_HASHES = {
    "lil": {"llm_decode_bench.py": "1abaf62aae74bc528ca456ea0674331b3bcfb8a95383f0365830d9a9f9cd3a74"},
    "aiperf": {
        "src/aiperf/config/flags/resolver.py": "b90b876cc9d66783cc8c3ac99a7c19273fd823b2c3f66d8438823290ed545e1f",
        "src/aiperf/cli.py": "5e15615c92a6037ee0a5fc7e767a98ebde692dc8622004621d0117cd2c0e3ff3",
        "src/aiperf/cli_commands/profile.py": "6f7495bb68cb42775e3613ecbd2a87eb2e2a0e6b1d0b845b92158d9bd4c9c0af",
        "src/aiperf/cli_runner/_single_run.py": "68b4fca1d2992ec05049f6b9d06172d7e3d5f1c500e5948c4eb74e25c6b8bc04",
        "src/aiperf/cli_runner/_process_setup.py": "0fab0def3f901533150beea7cf68e11e89b6c496470fb9cf9bfe7bc7b9be7f79",
        "src/aiperf/controller/multiprocess_service_manager.py": "bb6540d72bc5bb6db12ac271333ecf8879253b614591bb23c1c9614c5f9389b1",
        "src/aiperf/transports/aiohttp_client.py": "26a40b95c8cf83eb88381e0d9a676c02aba91abe2624f43367d2143c4270a271",
        "src/aiperf/config/flags/cli_config.py": "2be937b185ddfe3a19e8090aa4411f1015a2e00899d29db3055d3f7e19bdd42b",
        "src/aiperf/common/environment.py": "4e34cfc3edf5069c0d51741551b4b34c230115eefdabbf4d857ac44d92fd52e1",
    },
}
MAX_PROBE_ATTEMPTS = 32
MAX_SSE_EVENT_BYTES = 65536
PROBE_PATHS = ("/v1/models", "/get_server_info", "/version", "/metrics")


def instrumentation(runner):
    return {"lil_unconditional_warmup_disabled": runner == "lil",
            "native_stream_parser": True, "http_attempt_guard": True,
            "receive_stream_observer": True, "receive_observer_overhead_included": True,
            "max_sse_event_bytes": MAX_SSE_EVENT_BYTES}


def validate_supported_parameters(parameters):
    """One finite native cell, with no optional traffic-producing features."""
    if (parameters.get("runner") not in SOURCE_HASHES
            or type(parameters.get("request_count")) is not int
            or parameters["request_count"] < 1 or parameters.get("duration_s") is not None
            or parameters.get("warmup_requests", 0) != 0
            or parameters.get("cache_condition", "basic") != "basic"):
        raise ValueError("performance adapters require a finite request count and zero warmup")
    if parameters["runner"] == "lil" and parameters["workload"]["input_tokens"] != 0:
        raise ValueError("LIL supports only zero padding context; positive contexts require calibration qualification")


def verify_source(root, runner, *, installed=False):
    root = Path(root).resolve(strict=True)
    for relative, expected in SOURCE_HASHES[runner].items():
        relative = relative.removeprefix("src/aiperf/") if installed else relative
        path = root / relative
        if (path.is_symlink() or not path.resolve(strict=True).is_relative_to(root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise RuntimeError("performance native source seam changed")
    return root


class AttemptLedger:
    """Small flock transaction shared by native fork workers; never stores secrets."""

    def __init__(self, plan):
        if sys.platform != "linux":
            raise RuntimeError("managed performance adapters currently require Linux")
        self.path = Path(plan["paths"]["request_events"])
        self.state_path = self.path.with_suffix(".state.json")
        self.configured = validate_budgets(plan["budgets"])
        self.deadline = time.monotonic() + min(self.configured["deadline_s"], plan["timeout_s"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=False)
        with self.state_path.open("x") as file:
            json.dump({"model_calls": 0, "http_calls": 0, "probes": {},
                "streams": {"valid": 0, "protocol_failure": 0, "cancelled": 0, "usage_observed": 0},
                "reason": None, "event_count": 0, "byte_count": 0}, file)
        self.inference_limit = min(self.configured["max_model_calls"], plan["parameters"]["request_count"])
        self.record("performance_adapter_ready", adapter_sha256=plan["adapter_sha256"],
                    instrumentation=instrumentation(plan["runner"]))

    @contextmanager
    def transaction(self):
        import fcntl
        # Each worker opens its own description: inherited flock FDs do not exclude siblings.
        with self.state_path.open("r+") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            state = json.load(file)
            try:
                yield state
            finally:
                file.seek(0)
                json.dump(state, file)
                file.truncate()
                file.flush()
                fcntl.flock(file, fcntl.LOCK_UN)

    def _record(self, state, event, **fields):
        value = {"schema": "anvil-serving.performance-observation/v1", "event": event,
                 "seq": state["event_count"] + 1, **fields}
        line = json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n"
        if state["byte_count"] + len(line.encode()) > 1_000_000 and event != "adapter_finished":
            state["reason"] = "event_bytes"
            raise BudgetStopped("performance event budget exhausted")
        with self.path.open("a") as file:
            file.write(line)
            file.flush()
        state["event_count"] += 1
        state["byte_count"] += len(line.encode())

    def record(self, event, **fields):
        with self.transaction() as state:
            self._record(state, event, **fields)

    def reject(self, reason):
        with self.transaction() as state:
            state["reason"] = state["reason"] or reason
            self._record(state, "performance_transport_rejected", reason=reason)
        raise BudgetStopped(reason)

    def admit(self, *, inference, path):
        with self.transaction() as state:
            remaining = self.deadline - time.monotonic()
            reason = state["reason"]
            if remaining <= 0:
                reason = "deadline_s"
            if shutil.disk_usage(self.path.parent).free < self.configured["min_free_disk_bytes"]:
                reason = "min_free_disk_bytes"
            if inference and state["model_calls"] >= self.inference_limit:
                reason = "max_model_calls"
            if not inference and sum(state["probes"].values()) >= MAX_PROBE_ATTEMPTS:
                reason = "probe_attempts"
            if reason:
                state["reason"] = reason
                self._record(state, "performance_transport_rejected", reason=reason)
                raise BudgetStopped(reason)
            state["http_calls"] += 1
            if inference:
                state["model_calls"] += 1
            else:
                state["probes"][path] = state["probes"].get(path, 0) + 1
            self._record(state, "performance_transport_started" if inference else "performance_probe_started",
                         model_call=state["model_calls"], http_call=state["http_calls"],
                         method="POST" if inference else "GET", path=path, pid=os.getpid())
            return remaining, state["model_calls"] if inference else None

    def stream_finished(self, model_call, outcome, **fields):
        with self.transaction() as state:
            self._record(state, "performance_stream_finished", model_call=model_call,
                         outcome=outcome, **fields)
            state["streams"][outcome] += 1
            state["streams"]["usage_observed"] += int(fields["usage_observed"])

    def snapshot(self):
        with self.transaction() as state:
            streams = state["streams"]
            observed = sum(streams[key] for key in ("valid", "protocol_failure", "cancelled"))
            return {"configured": self.configured, "exhausted": state["reason"] is not None,
                    "reason": state["reason"], "model_calls": state["model_calls"],
                    "http_calls": state["http_calls"], "probe_calls": sum(state["probes"].values()),
                    "token_usage_verified": False,
                    "stream_validation": {**streams, "admitted": state["model_calls"],
                        "observed": observed,
                        "complete": 0 < observed == streams["valid"] == state["model_calls"]}}

    def close(self, outcome):
        with self.transaction() as state:
            self._record(state, "adapter_finished", status="complete", outcome=outcome,
                         event_count=state["event_count"], byte_count=state["byte_count"])


class StreamProtocolError(ValueError):
    """Fixed, payload-free error for a rejected native response stream."""


class SSEObserver:
    """Bounded wire validity only; native parsers still own all metric calculation.

    Observe before yielding each unchanged chunk. This receive-side work is part
    of the measured timing. Keep at most one 64 KiB event, never a whole response.
    """

    def __init__(self, ledger, model_call):
        self.ledger, self.model_call = ledger, model_call
        self.line = bytearray()
        self.data = []
        self.event_bytes = 0
        self.terminal = self.usage = self.finished = False
        self.reason = None

    def reject(self, reason):
        self.reason = self.reason or reason
        self.finish()
        raise StreamProtocolError("performance stream protocol failure: " + self.reason)

    def headers(self, status, content_type):
        if status != 200 or content_type.split(";", 1)[0].strip().lower() != "text/event-stream":
            self.reject("response_status_or_content_type")

    def feed(self, chunk):
        start = 0
        while start < len(chunk):
            newline = chunk.find(b"\n", start)
            end = len(chunk) if newline < 0 else newline + 1
            size = end - start
            if self.event_bytes + size > MAX_SSE_EVENT_BYTES:
                self.reject("event_too_large")
            self.event_bytes += size
            self.line.extend(chunk[start:end])
            start = end
            if newline < 0:
                break
            raw = bytes(self.line[:-1])
            self.line.clear()
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            try:
                line = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                self.reject("invalid_utf8")
            if "\r" in line or "\x00" in line:
                self.reject("invalid_framing")
            if not line:
                self._event()
                self.event_bytes = 0
            elif line.startswith(":"):
                continue
            else:
                field, separator, value = line.partition(":")
                if not separator or field not in {"data", "event", "id", "retry"}:
                    self.reject("invalid_framing")
                value = value.removeprefix(" ")
                if field == "data":
                    self.data.append(value)
                elif field == "event" and value == "error":
                    self.reject("error_event")

    def _event(self):
        if not self.data:
            return
        data = "\n".join(self.data)
        self.data.clear()
        if self.terminal:
            self.reject("data_after_terminal")
        if data == "[DONE]":
            self.terminal = True
            return
        def invalid_constant(value):
            raise ValueError("non-JSON constant")
        try:
            value = json.loads(data, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            self.reject("invalid_json")
        if not isinstance(value, dict):
            self.reject("invalid_json_object")
        if "error" in value:
            self.reject("error_event")
        usage = value.get("usage")
        if isinstance(usage, dict) and all(type(usage.get(key)) is int and usage[key] >= 0
                for key in ("prompt_tokens", "completion_tokens")):
            self.usage = True

    def terminal_pending(self):
        # LIL stops on the DONE line, before a separately delivered blank line.
        return not self.finished and not self.terminal and (
            self.data == ["[DONE]"] or self.line == b"data: [DONE]\r")

    def finish(self, *, cancelled=False, error=False):
        if self.finished:
            return
        if self.reason:
            outcome = "protocol_failure"
        elif cancelled:
            outcome, self.reason = "cancelled", "cancelled"
        elif error or self.event_bytes or not self.terminal:
            outcome, self.reason = "protocol_failure", "transport_error" if error else "truncated_stream"
        else:
            outcome = "valid"
        self.ledger.stream_finished(self.model_call, outcome, reason=self.reason,
            terminal_observed=self.terminal, usage_observed=self.usage)
        self.finished = True


class TransportPolicy:
    def __init__(self, plan, ledger, token):
        self.endpoint, self.ledger, self.token = plan["endpoint"], ledger, token
        base = self.endpoint["base_url"].rstrip("/")
        parsed = urlsplit(base)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != "/v1"):
            raise ValueError("performance base URL must end with /v1 and have no credentials or query")
        self.inference_url = base + "/chat/completions"
        self.origin = base[:-3]
        self.probes = {self.origin + path for path in PROBE_PATHS} if plan["runner"] == "lil" else set()
        self.controls = request_body_controls(plan["request_controls"])
        self.output_tokens = plan["parameters"]["workload"]["output_tokens"]

    def check(self, method, url, headers, body):
        inference = method == "POST" and url == self.inference_url
        if not inference and not (method == "GET" and url in self.probes and body is None):
            self.ledger.reject("endpoint_or_method")
        normalized = {str(k).lower(): str(v) for k, v in headers.items()}
        expected = "Bearer " + self.token if self.token else None
        if normalized.get("authorization") != expected:
            self.ledger.reject("authorization")
        if inference:
            try:
                payload = json.loads(body) if isinstance(body, (bytes, str)) else body
                caps = [payload[key] for key in ("max_tokens", "max_completion_tokens") if key in payload]
                valid = (isinstance(payload, dict) and payload.get("model") == self.endpoint["model"]
                         and len(caps) == 1 and type(caps[0]) is int
                         and caps[0] == self.output_tokens
                         and caps[0] <= self.ledger.configured["max_completion_tokens"]
                         and payload.get("stream") is True and type(payload.get("n", 1)) is int
                         and payload.get("n", 1) == 1
                         and all(payload.get(k) == v for k, v in self.controls.items()))
            except (TypeError, ValueError):
                valid = False
            if not valid:
                self.ledger.reject("request_shape_or_controls")
        return self.ledger.admit(inference=inference, path=urlsplit(url).path)


def install_httpx_guards(httpx, policy):
    originals = httpx.Client.send, httpx.AsyncClient.send

    class SyncStream(httpx.SyncByteStream):
        def __init__(self, stream, observer):
            self.stream, self.observer = stream, observer
            self.iterator = iter(stream)

        def __iter__(self):
            return self

        def __next__(self):
            try:
                chunk = next(self.iterator)
                self.observer.feed(chunk)
                return chunk
            except StopIteration:
                self.observer.finish()
                raise
            except BaseException as exc:
                self.observer.finish(cancelled=isinstance(exc, KeyboardInterrupt), error=True)
                raise

        def close(self):
            try:
                while self.observer.terminal_pending():
                    try:
                        next(self)
                    except StopIteration:
                        break
            finally:
                self.stream.close()
                self.observer.finish()

    class AsyncStream(httpx.AsyncByteStream):
        def __init__(self, stream, observer):
            self.stream, self.observer = stream, observer
            self.iterator = stream.__aiter__()

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                chunk = await anext(self.iterator)
                self.observer.feed(chunk)
                return chunk
            except StopAsyncIteration:
                self.observer.finish()
                raise
            except BaseException as exc:
                self.observer.finish(cancelled=isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)),
                                     error=True)
                raise

        async def aclose(self):
            try:
                while self.observer.terminal_pending():
                    try:
                        await asyncio.wait_for(anext(self), max(0, policy.ledger.deadline - time.monotonic()))
                    except StopAsyncIteration:
                        break
            finally:
                await self.stream.aclose()
                task = asyncio.current_task()
                self.observer.finish(cancelled=bool(task and task.cancelling()))

    def prepare(client, request, args, kwargs):
        if args or getattr(client, "_trust_env", False):
            policy.ledger.reject("ambient_http_transport")
        remaining, model_call = policy.check(request.method, str(request.url), request.headers,
                                             request.content or None)
        kwargs["follow_redirects"] = False
        kwargs["auth"] = None  # Do not permit an auth flow to resend outside this seam.
        request.extensions["timeout"] = {
            name: min(value, remaining) if isinstance(value, (int, float)) and value > 0 else remaining
            for name, value in {"connect": None, "read": None, "write": None, "pool": None,
                                **request.extensions.get("timeout", {})}.items()}
        return SSEObserver(policy.ledger, model_call) if model_call is not None else None

    def observe(response, observer, stream_class):
        observer.headers(response.status_code, response.headers.get("content-type", ""))
        if response.is_stream_consumed:
            observer.feed(response.content)
            observer.finish()
        else:
            response.stream = stream_class(response.stream, observer)

    def send(client, request, *args, **kwargs):
        observer = prepare(client, request, args, kwargs)
        if observer is None:
            return originals[0](client, request, **kwargs)
        stream = kwargs.get("stream", False)
        response = None
        try:
            response = originals[0](client, request, **{**kwargs, "stream": True})
            observe(response, observer, SyncStream)
            if not stream:
                response.read()
            return response
        except BaseException as exc:
            observer.finish(cancelled=isinstance(exc, KeyboardInterrupt), error=True)
            if response is not None:
                response.close()
            raise

    async def async_send(client, request, *args, **kwargs):
        observer = prepare(client, request, args, kwargs)
        if observer is None:
            return await originals[1](client, request, **kwargs)
        stream = kwargs.get("stream", False)
        response = None
        try:
            response = await originals[1](client, request, **{**kwargs, "stream": True})
            observe(response, observer, AsyncStream)
            if not stream:
                await response.aread()
            return response
        except BaseException as exc:
            observer.finish(cancelled=isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)), error=True)
            if response is not None:
                await response.aclose()
            raise

    # Native clients default trust_env=True. Set it at construction, before proxy discovery.
    constructors = httpx.Client.__init__, httpx.AsyncClient.__init__
    def sync_init(self, *args, **kwargs):
        kwargs["trust_env"] = False
        constructors[0](self, *args, **kwargs)
    def async_init(self, *args, **kwargs):
        kwargs["trust_env"] = False
        constructors[1](self, *args, **kwargs)
    httpx.Client.__init__, httpx.AsyncClient.__init__ = sync_init, async_init
    httpx.Client.send, httpx.AsyncClient.send = send, async_send

    def restore():
        httpx.Client.send, httpx.AsyncClient.send = originals
        httpx.Client.__init__, httpx.AsyncClient.__init__ = constructors
    return restore


def install_aiohttp_guard(aiohttp, policy):
    original = aiohttp.ClientSession._request

    class Content:
        def __init__(self, content, observer):
            self.content, self.observer = content, observer

        def __getattr__(self, name):
            return getattr(self.content, name)

        async def iter_any(self):
            # Exact pinned AIPerf receive seam. No chunk regrouping or decoding here.
            try:
                async for chunk in self.content.iter_any():
                    self.observer.feed(chunk)
                    yield chunk
            except BaseException as exc:
                self.observer.finish(cancelled=isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)),
                                     error=not isinstance(exc, GeneratorExit))
                raise
            else:
                self.observer.finish()

    async def request(session, method, url, **kwargs):
        if session.trust_env or kwargs.get("proxy") or kwargs.get("auth"):
            policy.ledger.reject("ambient_http_transport")
        headers = dict(session.headers)
        headers.update(kwargs.get("headers") or {})
        remaining, model_call = policy.check(method, str(url), headers, kwargs.get("data", kwargs.get("json")))
        kwargs["allow_redirects"] = False
        timeout = kwargs.get("timeout", session.timeout)
        total = getattr(timeout, "total", None)
        kwargs["timeout"] = aiohttp.ClientTimeout(total=min(total, remaining) if total else remaining)
        observer = SSEObserver(policy.ledger, model_call)
        response = None
        try:
            response = await original(session, method, url, **kwargs)
            observer.headers(response.status, response.headers.get("content-type", ""))
            response.content = Content(response.content, observer)
            original_release, original_close = response.release, response.close
            def release():
                try:
                    return original_release()
                finally:
                    observer.finish()
            def close():
                try:
                    return original_close()
                finally:
                    observer.finish()
            response.release, response.close = release, close
            return response
        except BaseException as exc:
            observer.finish(cancelled=isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)), error=True)
            if response is not None:
                response.close()
            raise
    aiohttp.ClientSession._request = request
    return lambda: setattr(aiohttp.ClientSession, "_request", original)


def native_arguments(plan):
    params, endpoint, paths = plan["parameters"], plan["endpoint"], plan["paths"]
    if plan["runner"] == "lil":
        return ["--host", endpoint["base_url"].rstrip("/")[:-3], "--model", endpoint["model"],
            "--concurrency", str(params["concurrency"]), "--contexts", "0", "--request-count",
            str(params["request_count"]), "--warmup-request-count", "0", "--decode-warmup-seconds", "0",
            "--skip-prefill", "--display-mode", "plain", "--no-hw-monitor", "--respect-eos",
            "--no-resume", "--max-tokens", str(params["workload"]["output_tokens"]),
            "--output", paths["native_output"]]
    args = ["profile", "--streaming", "--endpoint-type", "chat", "--model", endpoint["model"],
            "--url", endpoint["base_url"], "--workers-max", "1", "--request-count", str(params["request_count"]),
            "--concurrency", str(params["concurrency"]), "--ui", "none", "--random-seed", "42",
            "--no-server-metrics", "--no-gpu-telemetry", "--wait-for-model-timeout", "0",
            "--request-timeout-seconds", str(min(plan["timeout_s"], plan["budgets"]["deadline_s"])),
            "--isl", str(params["workload"]["input_tokens"]), "--isl-stddev", "0",
            "--osl", str(params["workload"]["output_tokens"]), "--osl-stddev", "0",
            "--use-server-token-count", "--tokenizer", "builtin", "--output-artifact-dir", paths["output"]]
    controls = request_body_controls(plan["request_controls"])
    if controls:
        args += ["--extra-inputs", json.dumps(controls)]
    return args


def load_lil_namespace(path):
    """Disable the pinned unconditional 64-token JIT probe; warmup=0 must mean zero."""
    source = Path(path).read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_HASHES["lil"]["llm_decode_bench.py"]:
        raise RuntimeError("LIL startup adaptation source changed")
    tree = ast.parse(source, filename=str(path))
    functions = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_benchmark"]
    probes = [node for node in ast.walk(functions[0]) if isinstance(node, ast.If)
              and ast.unparse(node.test) == "not standalone_prefill_contexts or args.skip_prefill"]
    if len(probes) != 1:
        raise RuntimeError("LIL unconditional warmup seam changed")
    probes[0].test = ast.copy_location(ast.Constant(False), probes[0].test)
    module = types.ModuleType("anvil_lil_benchmark")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module.__dict__


def _run_lil(plan, root, policy):
    import httpx
    restore = install_httpx_guards(httpx, policy)
    old_argv = sys.argv
    namespace = None
    parsed = None
    try:
        os.environ["LLM_BENCH_NO_UPDATE_CHECK"] = "1"
        sys.argv = [str(root / "llm_decode_bench.py"), *native_arguments(plan)]
        namespace = load_lil_namespace(sys.argv[0])
        globals_ = namespace["main"].__globals__
        original_parse = globals_["parse_args"]
        original_stream = globals_["stream_one_request"]
        def parse_args():
            nonlocal parsed
            parsed = original_parse()
            parsed.api_key = policy.token
            if parsed.model == "Qwen3.5":
                raise ValueError("LIL's model autodetection sentinel is not an explicit alias")
            return parsed
        async def stream(client, url, payload, *args, **kwargs):
            return await original_stream(client, url, {**payload, **policy.controls}, *args, **kwargs)
        globals_["parse_args"], globals_["stream_one_request"] = parse_args, stream
        globals_["main"]()
    finally:
        # Native checkpoints/results already written are never removed or normalized in place.
        if namespace is not None:
            namespace["_restore_terminal"]()
            sys.modules.pop("anvil_lil_benchmark", None)
        sys.argv = old_argv
        restore()


def _run_aiperf(plan, policy, finish):
    import aiohttp
    spec = importlib.util.find_spec("aiperf")
    if spec is None or spec.origin is None:
        raise RuntimeError("prepared AIPerf package is missing")
    verify_source(Path(spec.origin).parent, "aiperf", installed=True)
    restore = install_aiohttp_guard(aiohttp, policy)
    original_exit, parent_pid = os._exit, os.getpid()
    def native_exit(code):
        if os.getpid() == parent_pid:
            finish("completed" if code == 0 else "failed")
        original_exit(code)
    os._exit = native_exit
    try:
        from aiperf.cli import app
        from aiperf.cli_runner import _single_run
        from aiperf.config.flags import resolver
        original_resolve = resolver.resolve_config
        def resolve_config(cli_config, config_file=None):
            if config_file is not None or cli_config.config_file is not None:
                raise RuntimeError("unexpected AIPerf external configuration")
            return original_resolve(cli_config.model_copy(update={"api_key": policy.token or None}))
        resolver.resolve_config = resolve_config
        original_setup = _single_run._configure_multiprocessing_start_method
        def fork_setup(using_dashboard):
            original_setup(using_dashboard)
            multiprocessing.set_start_method("fork", force=True)
            if multiprocessing.get_start_method() != "fork":
                raise RuntimeError("AIPerf transport guard requires inherited fork workers")
            policy.ledger.record("performance_worker_start_method", start_method="fork")
        _single_run._configure_multiprocessing_start_method = fork_setup
        try:
            multiprocessing.set_start_method("fork", force=True)
            if multiprocessing.get_start_method() != "fork":
                raise RuntimeError("AIPerf fork setup failed")
            app(native_arguments(plan))
        finally:
            _single_run._configure_multiprocessing_start_method = original_setup
            resolver.resolve_config = original_resolve
    finally:
        os._exit = original_exit
        restore()


def run_plan(plan):
    from .artifacts import atomic_write_json
    from .performance_prepare import verify_performance_environment
    validate_supported_parameters(plan["parameters"])
    receipt = plan["environment"]
    executable = verify_performance_environment(receipt, cache_root=plan["cache_root"], runner_name=plan["runner"])
    if Path(sys.prefix).resolve() != Path(executable).parent.parent.resolve():
        raise RuntimeError("performance launcher is outside its prepared environment")
    root = verify_source(Path(plan["cache_root"]) / receipt["cache_key"] / receipt["source_path"], plan["runner"])
    if (Path.cwd().resolve() != Path(plan["paths"]["work"]).resolve()
            or Path(os.environ.get("HOME", "")).resolve() != Path(plan["paths"]["home"]).resolve()
            or Path(".env").exists() or Path(".env").is_symlink()):
        raise RuntimeError("performance native import requires an owned clean working directory")
    token = os.environ.get("OPENAI_API_KEY", "")
    ledger = AttemptLedger(plan)
    outcome = "failed"
    finished = False
    def finish(status):
        nonlocal finished
        if finished:
            return
        snapshot = ledger.snapshot()
        streams = snapshot["stream_validation"]
        if status == "cancelled" or streams["cancelled"]:
            status = "cancelled"
        elif snapshot["exhausted"]:
            status = "budget_exhausted"
        elif streams["protocol_failure"] or (streams["admitted"] and not streams["complete"]):
            status = "protocol_failure"
        elif status == "completed" and not streams["complete"]:
            status = "protocol_failure"
        ledger.close(status)
        atomic_write_json(plan["paths"]["adapter_result"], {
            "schema": "anvil-serving.performance-adapter-result/v1", "outcome": status,
            "plan_sha256": plan["plan_sha256"], "adapter_sha256": plan["adapter_sha256"],
            "budget": snapshot, "events_complete": True, "token_usage_verified": False,
            "stream_validation": streams,
            "instrumentation": instrumentation(plan["runner"])})
        finished = True
    try:
        policy = TransportPolicy(plan, ledger, token)
        if plan["runner"] == "lil":
            _run_lil(plan, root, policy)
        else:
            _run_aiperf(plan, policy, finish)
        outcome = "completed" if not ledger.snapshot()["exhausted"] else "budget_exhausted"
    except BudgetStopped:
        outcome = "budget_exhausted" if ledger.snapshot()["exhausted"] else "boundary_rejected"
        raise
    except KeyboardInterrupt:
        outcome = "cancelled"
        raise
    except SystemExit as exc:
        outcome = "completed" if exc.code in (0, None) else "failed"
        raise
    finally:
        finish(outcome)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    args = parser.parse_args(argv)
    plan = json.loads(Path(args.plan).read_text())
    from .performance import verify_plan
    verify_plan(plan)
    run_plan(plan)


if __name__ == "__main__":
    main()
