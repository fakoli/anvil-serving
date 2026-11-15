"""The managed MLX Audio launcher gates real preload and cleans up its broker."""
import asyncio
from contextlib import asynccontextmanager
import importlib
import json
import queue
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
