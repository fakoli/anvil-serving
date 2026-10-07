"""The managed MLX Audio launcher gates real preload and cleans up its broker."""
import asyncio
from contextlib import asynccontextmanager
import importlib
import inspect
import json
import os
from pathlib import Path
import queue
import select
import signal
import subprocess
import sys
import threading
import types

import pytest


COMMIT = "70f4add32911bab6f869b824864ad9f1e24dcb97"


def runtime():
    return importlib.import_module("anvil_serving.voice.audio_runtime")


def snapshot(tmp_path):
    path = tmp_path / "hub" / "models--org--audio" / "snapshots" / ("a" * 40)
    blobs = path.parent.parent / "blobs"
    blobs.mkdir(parents=True)
    path.mkdir(parents=True)
    (blobs / ("b" * 40)).write_text('{"model_type":"qwen3_tts"}')
    (blobs / ("c" * 64)).write_bytes(b"synthetic weights")
    (path / "config.json").symlink_to("../../blobs/" + "b" * 40)
    (path / "model.safetensors").symlink_to("../../blobs/" + "c" * 64)
    return path


class Distribution:
    version = "0.5.8"
    def read_text(self, name):
        assert name == "direct_url.json"
        return json.dumps({"url": "https://github.com/Blaizzy/mlx-audio", "vcs_info": {
            "vcs": "git", "commit_id": COMMIT}})


def upstream(*, fail_load=False, missing_metadata=False, loaded_model=None, after_load=None):
    events = []
    class Provider:
        def __init__(self):
            self.models = {}
            self.lock = asyncio.Lock()
        def load_model(self, name):
            events.append(("load", threading.get_ident(), name))
            if fail_load:
                raise RuntimeError("synthetic load failure")
            if name not in self.models:
                self.models[name] = object() if loaded_model is None else loaded_model
            return self.models[name]
        async def get_available_models(self):
            return [] if missing_metadata else list(self.models)
        async def remove_model(self, name):
            self.models.pop(name, None)
            return True
    module = types.SimpleNamespace(ModelProvider=Provider, model_provider=Provider(),
                                   INFERENCE_BROKER=None, events=events,
                                   app=types.SimpleNamespace(router=types.SimpleNamespace()))
    class Broker:
        def __init__(self):
            self.stopped = False
        def submit(self, **kwargs):
            result = queue.Queue()
            cancel = threading.Event()
            def worker():
                try:
                    assert kwargs == {"endpoint_kind": "model-load", "model_name": kwargs["model_name"], "payload": None}
                    loaded = module.model_provider.load_model(kwargs["model_name"])
                    if after_load is not None:
                        after_load(loaded)
                except Exception as exc:
                    result.put(types.SimpleNamespace(kind="error", error=exc))
                result.put(types.SimpleNamespace(kind="done"))
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
            return types.SimpleNamespace(result_queue=result, cancel=cancel.set)
        def stop_and_join(self):
            self.stopped = True
            events.append("stopped")
    def broker():
        if module.INFERENCE_BROKER is None:
            module.INFERENCE_BROKER = Broker()
        return module.INFERENCE_BROKER
    @asynccontextmanager
    async def lifespan(app):
        events.append("upstream_start")
        try:
            yield
        finally:
            if module.INFERENCE_BROKER is not None:
                module.INFERENCE_BROKER.stop_and_join()
                module.INFERENCE_BROKER = None
            events.append("upstream_shutdown")
    module.get_inference_broker = broker
    module.app_lifespan = lifespan
    module.app.router.lifespan_context = lifespan
    return module


def test_normal_operator_import_does_not_import_optional_engine():
    before = set(sys.modules)
    runtime()
    assert not {"mlx", "mlx.core", "mlx_audio", "uvicorn", "fastapi"} & (set(sys.modules) - before)


def test_runtime_pin_requires_exact_version_and_direct_git_commit():
    runtime().verify_runtime_pin("0.5.8", COMMIT, _distribution=lambda name: Distribution())
    for dist in [types.SimpleNamespace(version="0.5.7", read_text=lambda name: Distribution().read_text(name)),
                 types.SimpleNamespace(version="0.5.8", read_text=lambda name: None),
                 types.SimpleNamespace(version="0.5.8", read_text=lambda name: json.dumps({"vcs_info": {"vcs": "git", "commit_id": "a" * 40}}))]:
        with pytest.raises(runtime().AudioRuntimeError):
            runtime().verify_runtime_pin("0.5.8", COMMIT, _distribution=lambda name: dist)
    with pytest.raises(runtime().AudioRuntimeError, match="reviewed"):
        runtime().verify_runtime_pin("0.5.8", "a" * 40, _distribution=lambda name: Distribution())


@pytest.mark.parametrize("failure", ["mutable", "external_config", "broken_weight", "missing_weight", "bad_config", "missing_shard", "symlink_root"])
def test_runtime_rejects_unsafe_or_incomplete_immutable_snapshot(tmp_path, failure):
    path = snapshot(tmp_path)
    if failure == "mutable":
        path.rename(path.with_name("main"))
        path = path.with_name("main")
    elif failure == "external_config":
        (path / "config.json").unlink()
        outside = tmp_path / "outside.json"
        outside.write_text('{}')
        (path / "config.json").symlink_to(outside)
    elif failure == "broken_weight":
        (path.parent.parent / "blobs" / ("c" * 64)).unlink()
    elif failure == "missing_weight":
        (path / "model.safetensors").unlink()
    elif failure == "bad_config":
        (path.parent.parent / "blobs" / ("b" * 40)).write_text('[]')
    elif failure == "missing_shard":
        (path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"layer": "missing.safetensors"}}))
    else:
        real = path.with_name("d" * 40)
        path.rename(real)
        path.symlink_to(real, target_is_directory=True)
    with pytest.raises(runtime().AudioRuntimeError):
        runtime().validate_snapshot(str(path))


def test_runtime_preloads_on_worker_before_readiness_and_preserves_upstream_cleanup(tmp_path):
    path = snapshot(tmp_path)
    module = upstream()
    app = runtime().prepare_app(module, str(path), startup_timeout=1)
    async def check():
        async with app.router.lifespan_context(app):
            assert await module.model_provider.get_available_models() == [str(path)]
            loaded = module.model_provider.load_model(str(path))
            assert loaded is module.model_provider.load_model(str(path))
            with pytest.raises(runtime().AudioRuntimeError, match="pinned"):
                module.model_provider.load_model("org/remote-model")
            with pytest.raises(runtime().AudioRuntimeError, match="pinned"):
                await module.model_provider.remove_model(str(path))
            assert "stopped" not in module.events
        assert module.events[-2:] == ["stopped", "upstream_shutdown"]
    asyncio.run(check())
    first_load = next(item for item in module.events if isinstance(item, tuple))
    assert first_load[1] != threading.get_ident()


@pytest.mark.parametrize("failure", ["load", "metadata"])
def test_runtime_preload_failure_never_yields_readiness_and_stops_broker(tmp_path, failure):
    module = upstream(fail_load=failure == "load", missing_metadata=failure == "metadata")
    app = runtime().prepare_app(module, str(snapshot(tmp_path)), startup_timeout=1)
    async def check():
        with pytest.raises((runtime().AudioRuntimeError, RuntimeError)):
            async with app.router.lifespan_context(app):
                pytest.fail("startup accepted an unready model")
    asyncio.run(check())
    assert module.events[-2:] == ["stopped", "upstream_shutdown"]


def test_runtime_shutdown_requested_during_preload_cancels_and_cleans_worker(tmp_path):
    module = upstream()
    def pending(**kwargs):
        handle = types.SimpleNamespace(result_queue=queue.Queue(), cancel=lambda: module.events.append("cancelled"))
        return handle
    broker = module.get_inference_broker()
    broker.submit = pending
    module.INFERENCE_BROKER = None
    def start_broker():
        module.INFERENCE_BROKER = broker
        return broker
    module.get_inference_broker = start_broker
    app = runtime().prepare_app(module, str(snapshot(tmp_path)), startup_timeout=1,
                                stop_requested=lambda: True)
    async def check():
        with pytest.raises(runtime().AudioRuntimeError, match="shutdown"):
            async with app.router.lifespan_context(app):
                pytest.fail("startup survived requested shutdown")
    asyncio.run(check())
    assert module.events[-3:] == ["cancelled", "stopped", "upstream_shutdown"]


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "::1", "example.test"])
def test_runtime_refuses_non_loopback_host_before_optional_import(tmp_path, host):
    with pytest.raises(runtime().AudioRuntimeError, match="127.0.0.1"):
        runtime().run(str(snapshot(tmp_path)), host=host, port=30101,
                      _load_upstream=lambda: pytest.fail("imported optional engine"))


def test_runtime_runs_one_process_uvicorn_and_keeps_runtime_offline(tmp_path, monkeypatch):
    module = upstream()
    path = snapshot(tmp_path)
    monkeypatch.setattr(runtime(), "verify_runtime_pin", lambda *_a, **_k: None)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_IMPLICIT_TOKEN"):
        monkeypatch.setenv(name, "0")
    seen = {}
    class Config:
        def __init__(self, app, **kwargs):
            self.app, self.kwargs = app, kwargs
    class Server:
        should_exit = False
        started = False
        def __init__(self, config):
            self.config = config
        def run(self):
            seen.update(self.config.kwargs)
            async def serve():
                async with self.config.app.router.lifespan_context(self.config.app):
                    self.started = True
            asyncio.run(serve())
    uvicorn = types.SimpleNamespace(Config=Config, Server=Server)
    assert runtime().run(str(path), host="127.0.0.1", port=30101,
                          _load_upstream=lambda: (module, uvicorn)) == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 30101
    assert seen["workers"] == 1 and seen["reload"] is False and seen["lifespan"] == "on"
    assert runtime().os.environ["HF_HUB_OFFLINE"] == "1"
    assert runtime().os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert module.events[-2:] == ["stopped", "upstream_shutdown"]


def test_runtime_uvicorn_startup_failure_is_nonzero_even_when_should_exit_is_set(tmp_path, monkeypatch):
    module = upstream()
    monkeypatch.setattr(runtime(), "verify_runtime_pin", lambda *_a, **_k: None)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_IMPLICIT_TOKEN"):
        monkeypatch.setenv(name, "0")
    class Config:
        def __init__(self, app, **kwargs):
            self.app = app
    class Server:
        should_exit = True
        started = False
        def __init__(self, config):
            self.config = config
        def run(self):
            pass
    with pytest.raises(runtime().AudioRuntimeError, match="startup"):
        runtime().run(str(snapshot(tmp_path)), _load_upstream=lambda: (
            module, types.SimpleNamespace(Config=Config, Server=Server)))


class Memory:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.active, self.cache, self.peak = 100, 200, 300
    def _call(self, name):
        self.events.append((name, threading.get_ident()))
    def set_cache_limit(self, limit):
        self._call("cache_limit")
        self.limit = limit
        return 999
    def get_active_memory(self):
        self._call("active")
        return self.active
    def get_cache_memory(self):
        self._call("cache")
        return self.cache
    def get_peak_memory(self):
        self._call("peak")
        return self.peak
    def reset_peak_memory(self):
        self._call("reset_peak")
        self.peak = 0


@pytest.mark.parametrize("value", [-1, 1048577, True, 0.5, "256"])
def test_cache_limit_refuses_invalid_policy_before_optional_import(tmp_path, value):
    with pytest.raises(runtime().AudioRuntimeError, match="cache limit"):
        runtime().run(str(snapshot(tmp_path)), mlx_cache_limit_mib=value,
                      _load_upstream=lambda: pytest.fail("imported optional engine"))


@pytest.mark.parametrize("value", [0, 256])
def test_cache_policy_is_applied_once_before_load_on_inference_worker(tmp_path, value, caplog):
    module = upstream()
    module.mx = Memory(module.events)
    path = snapshot(tmp_path)
    with caplog.at_level("INFO", logger="anvil-serving.mlx-audio"):
        app = runtime().prepare_app(module, str(path), mlx_cache_limit_mib=value)
        async def check():
            async with app.router.lifespan_context(app):
                module.model_provider.load_model(str(path))
                module.model_provider.load_model(str(path))
        asyncio.run(check())
    cache_event = next(item for item in module.events if isinstance(item, tuple) and item[0] == "cache_limit")
    load_event = next(item for item in module.events if isinstance(item, tuple) and item[0] == "load")
    assert cache_event[1] == load_event[1] != threading.get_ident()
    assert module.events.index(cache_event) < module.events.index(load_event)
    assert module.mx.limit == value * 1024**2
    assert sum(isinstance(item, tuple) and item[0] == "cache_limit" for item in module.events) == 1
    policy = next(json.loads(record.message) for record in caplog.records if "mlx_memory_policy" in record.message)
    assert policy["cache_limit_mib"] == value and policy["previous_cache_limit_bytes"] == 999
    assert policy["memory_limit"] == "upstream-default-guideline"


def test_unset_cache_policy_keeps_upstream_allocation_and_generation_defaults(tmp_path):
    module = upstream()
    module.mx = Memory()
    app = runtime().prepare_app(module, str(snapshot(tmp_path)))
    async def check():
        async with app.router.lifespan_context(app):
            pass
    asyncio.run(check())
    assert module.mx.events == []


@pytest.mark.parametrize("version", ["0.32.2", "0.33.0"])
def test_memory_controls_refuse_unreviewed_mlx_version(monkeypatch, version):
    monkeypatch.setattr(runtime().importlib.metadata, "version", lambda name: version)
    with pytest.raises(runtime().AudioRuntimeError, match="0.32.3"):
        runtime()._verify_memory_runtime()


def test_memory_cli_options_reach_runtime_and_help_is_explicit(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(runtime(), "run", lambda model, **kwargs: seen.update(model=model, **kwargs) or 0)
    assert runtime().main(["--model", "/synthetic/snapshot", "--mlx-cache-limit-mib", "0", "--mlx-memory-trace"]) == 0
    assert seen["mlx_cache_limit_mib"] == 0 and seen["mlx_memory_trace"] is True
    with pytest.raises(SystemExit) as help_exit:
        runtime().main(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "--mlx-cache-limit-mib" in help_text and "0 disables cache" in help_text


@pytest.mark.parametrize("mode", ["complete", "value", "error", "closed", "unstarted-close"])
def test_memory_trace_preserves_results_and_records_finally_without_request_text(caplog, mode):
    memory = Memory()
    result = object()
    def generation(text, *, max_tokens=750):
        assert text == "private caller text" and max_tokens == 750
        memory.active, memory.cache, memory.peak = 400, 500, 600
        if mode == "error":
            raise RuntimeError("synthetic generation failure")
        yield result
    if mode == "value":
        def generation(text, *, max_tokens=750):
            memory.active, memory.cache, memory.peak = 400, 500, 600
            return result
    traced = runtime()._traced_generate(generation, memory)
    assert inspect.signature(traced) == inspect.signature(generation)
    with caplog.at_level("INFO", logger="anvil-serving.mlx-audio"):
        observed = traced("private caller text")
        if mode == "value":
            assert observed is result
        elif mode == "error":
            with pytest.raises(RuntimeError, match="synthetic generation"):
                next(observed)
        elif mode in {"closed", "unstarted-close"}:
            if mode == "closed":
                assert next(observed) is result
            observed.close()
        else:
            assert list(observed) == [result]
    records = [json.loads(record.message) for record in caplog.records if "mlx_generation_memory" in record.message]
    assert len(records) == 2
    assert records[0]["phase"] == "start" and records[0]["active_bytes"] == 100
    assert records[1]["phase"] == "end" and records[1]["peak_scope"] == "serial-generation"
    expected = "error" if mode == "error" else "closed" if "close" in mode else "complete"
    assert records[1]["outcome"] == expected
    if mode != "unstarted-close":
        assert records[1]["active_bytes"] == 400 and records[1]["cache_bytes"] == 500
        assert records[1]["peak_bytes"] == 600
    assert "private caller text" not in caplog.text
    assert all(item[1] == threading.get_ident() for item in memory.events)


def test_memory_trace_finalization_outside_worker_never_calls_mlx(caplog):
    memory = Memory()
    def generation():
        yield "audio"
    with caplog.at_level("INFO", logger="anvil-serving.mlx-audio"):
        observed = runtime()._traced_generate(generation, memory)()
        calls_before = len(memory.events)
        thread = threading.Thread(target=observed.close)
        thread.start()
        thread.join()
    assert len(memory.events) == calls_before
    record = json.loads(caplog.records[-1].message)
    assert record["measurement"] == "unavailable-outside-owning-worker"


@pytest.mark.parametrize("started", [False, True])
def test_memory_trace_records_failed_close_and_preserves_its_exception(caplog, started):
    memory = Memory()
    class Results:
        def __iter__(self):
            return self
        def __next__(self):
            return "audio"
        def close(self):
            raise RuntimeError("synthetic close failure")
    with caplog.at_level("INFO", logger="anvil-serving.mlx-audio"):
        observed = runtime()._traced_generate(lambda: Results(), memory)()
        if started:
            assert next(observed) == "audio"
        with pytest.raises(RuntimeError, match="synthetic close failure"):
            observed.close()
    records = [json.loads(record.message) for record in caplog.records if "mlx_generation_memory" in record.message]
    assert [(record["phase"], record.get("outcome")) for record in records] == [
        ("start", None), ("end", "error")]


def test_memory_trace_delegates_iterator_close_exactly_once():
    closes = []
    class Results:
        def __iter__(self):
            return self
        def __next__(self):
            return "audio"
        def close(self):
            closes.append("closed")
    observed = runtime()._traced_generate(lambda: Results(), Memory())()
    assert next(observed) == "audio"
    observed.close()
    assert closes == ["closed"]


def test_memory_trace_bypasses_calls_outside_preload_worker_without_sampling_mlx(caplog):
    memory = Memory()
    result = object()
    observed = []
    traced = runtime()._traced_generate(lambda: result, memory,
                                       worker_thread=threading.get_ident())
    with caplog.at_level("INFO", logger="anvil-serving.mlx-audio"):
        thread = threading.Thread(target=lambda: observed.append(traced()))
        thread.start()
        thread.join()
    assert observed == [result]
    assert memory.events == []
    record = json.loads(caplog.records[-1].message)
    assert record["phase"] == "bypass"
    assert record["measurement"] == "unavailable-outside-inference-worker"


def test_provider_traces_cached_serial_model_once_with_separate_request_peaks(tmp_path, caplog):
    memory = Memory()
    def generate(text):
        memory.active, memory.cache, memory.peak = 400, 500, 600
        yield text
    model = types.SimpleNamespace(generate=generate)
    observed = []
    def after_load(loaded):
        observed.append(list(loaded.generate("cold")))
        observed.append(list(loaded.generate("warm")))
    module = upstream(loaded_model=model, after_load=after_load)
    module.mx = memory
    path = snapshot(tmp_path)
    with caplog.at_level("INFO", logger="anvil-serving.mlx-audio"):
        app = runtime().prepare_app(module, str(path), mlx_memory_trace=True)
        async def check():
            async with app.router.lifespan_context(app):
                loaded = module.model_provider.load_model(str(path))
                wrapped = loaded.generate
                assert module.model_provider.load_model(str(path)) is loaded
                assert loaded.generate is wrapped
                calls_before = len(memory.events)
                assert list(loaded.generate("outside-worker")) == ["outside-worker"]
                assert len(memory.events) == calls_before
        asyncio.run(check())
    assert observed == [["cold"], ["warm"]]
    assert all(item[1] != threading.get_ident() for item in memory.events)
    records = [json.loads(record.message) for record in caplog.records if "mlx_generation_memory" in record.message]
    assert sum(record["phase"] == "bypass" for record in records) == 1
    records = [record for record in records if record["phase"] != "bypass"]
    assert [(record["request_index"], record["phase"]) for record in records] == [
        (1, "start"), (1, "end"), (2, "start"), (2, "end")]
    assert sum(item[0] == "reset_peak" for item in memory.events) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX native-service signal contract")
def test_direct_external_python_entrypoint_handles_sigterm_and_cleans_broker(tmp_path):
    path = snapshot(tmp_path)
    packages = tmp_path / "external-packages"
    packages.mkdir()
    audio = packages / "mlx_audio"
    audio.mkdir()
    (audio / "__init__.py").write_text("")
    distribution = packages / "mlx_audio-0.5.8.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text("Name: mlx-audio\nVersion: 0.5.8\n")
    (distribution / "direct_url.json").write_text(Distribution().read_text("direct_url.json"))
    (audio / "server.py").write_text('''
import asyncio, os, queue, types
from contextlib import asynccontextmanager
class ModelProvider:
    def __init__(self): self.models = {}; self.lock = asyncio.Lock()
    def load_model(self, name):
        self.models.setdefault(name, object()); return self.models[name]
    async def get_available_models(self): return list(self.models)
model_provider = ModelProvider()
INFERENCE_BROKER = None
class Broker:
    def submit(self, *, endpoint_kind, model_name, payload):
        assert endpoint_kind == "model-load" and payload is None
        model_provider.load_model(model_name)
        result = queue.Queue(); result.put(types.SimpleNamespace(kind="done"))
        return types.SimpleNamespace(result_queue=result, cancel=lambda: None)
    def stop_and_join(self):
        with open(os.environ["CLEANUP_RECORD"], "w") as handle: handle.write(str(os.getpid()))
def get_inference_broker():
    global INFERENCE_BROKER
    if INFERENCE_BROKER is None: INFERENCE_BROKER = Broker()
    return INFERENCE_BROKER
@asynccontextmanager
async def app_lifespan(app):
    try: yield
    finally:
        if INFERENCE_BROKER is not None: INFERENCE_BROKER.stop_and_join()
app = types.SimpleNamespace(router=types.SimpleNamespace(lifespan_context=app_lifespan))
''')
    (packages / "uvicorn.py").write_text('''
import asyncio, signal
class Config:
    def __init__(self, app, **kwargs):
        assert kwargs["host"] == "127.0.0.1" and kwargs["workers"] == 1 and not kwargs["reload"]
        self.app = app
class Server:
    def __init__(self, config): self.config = config; self.started = False; self.should_exit = False
    def handle_exit(self, sig, frame): self.should_exit = True
    def run(self):
        signal.signal(signal.SIGTERM, self.handle_exit)
        async def serve():
            async with self.config.app.router.lifespan_context(self.config.app):
                self.started = True; print("READY", flush=True)
                while not self.should_exit: await asyncio.sleep(0.01)
        asyncio.run(serve())
''')
    cleanup = tmp_path / "cleanup.txt"
    child = subprocess.Popen(
        [sys.executable, str(Path(runtime().__file__)), "--model", str(path), "--port", "30101"],
        env={**os.environ, "PYTHONPATH": str(packages), "CLEANUP_RECORD": str(cleanup)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert select.select([child.stdout], [], [], 5)[0], "runtime did not finish real preload"
        assert child.stdout.readline().strip() == "READY"
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, (stdout, stderr)
        assert cleanup.read_text() == str(child.pid)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
