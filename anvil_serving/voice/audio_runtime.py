"""Managed, single-snapshot MLX Audio runtime entrypoint.

This file is also runnable by an external audio environment's Python directly.
Importing it from the ordinary Anvil operator process imports only stdlib. MLX,
FastAPI and uvicorn belong to the reviewed external runtime, loaded only by run.
"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterator
from contextlib import asynccontextmanager
from functools import wraps
import importlib
import importlib.metadata
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import stat
import sys
import threading
import time


MLX_AUDIO_VERSION = "0.5.8"
MLX_AUDIO_COMMIT = "70f4add32911bab6f869b824864ad9f1e24dcb97"
LAUNCHER_POLICY_VERSION = "mlx-audio-launcher/v2"
MLX_MEMORY_VERSION = "0.32.3"
_MAX_CACHE_LIMIT_MIB = 1024 * 1024
_SOURCE_URL = "https://github.com/Blaizzy/mlx-audio"
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_BLOB = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MAX_ENTRIES = 20_000
_MAX_CONFIG_BYTES = 8 * 1024**2
_LOGGER = logging.getLogger("anvil-serving.mlx-audio")


class AudioRuntimeError(ValueError):
    """The declared native audio runtime or immutable model cannot be accepted."""


def verify_runtime_pin(version=MLX_AUDIO_VERSION, commit=MLX_AUDIO_COMMIT,
                       *, _distribution=None):
    """Accept only the reviewed distribution version and recorded Git source."""
    if (version, commit) != (MLX_AUDIO_VERSION, MLX_AUDIO_COMMIT):
        raise AudioRuntimeError("MLX Audio runtime pin is not a reviewed version/source pair")
    try:
        distribution = (_distribution or importlib.metadata.distribution)("mlx-audio")
        direct = json.loads(distribution.read_text("direct_url.json") or "null")
    except (importlib.metadata.PackageNotFoundError, ValueError, TypeError, OSError):
        raise AudioRuntimeError("MLX Audio requires installed distribution metadata with its exact Git source pin") from None
    if distribution.version != version:
        raise AudioRuntimeError("installed MLX Audio version does not match the reviewed runtime pin")
    if not isinstance(direct, dict):
        raise AudioRuntimeError("installed MLX Audio has no exact Git source provenance")
    source = direct.get("url")
    vcs = direct.get("vcs_info")
    if (
        not isinstance(source, str) or source.removesuffix(".git") != _SOURCE_URL
        or not isinstance(vcs, dict) or vcs.get("vcs") != "git"
        or vcs.get("commit_id") != commit
    ):
        raise AudioRuntimeError("installed MLX Audio Git source does not match the reviewed commit")
    return {"version": version, "commit": commit, "source": _SOURCE_URL}


def _directories(path: Path):
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise AudioRuntimeError("runtime directory ancestry must contain only directories, without symlinks")
        if current.parent == current:
            return
        current = current.parent


def _snapshot_file(path: Path, repo: Path):
    _directories(path.parent)
    try:
        info = path.lstat()
    except OSError:
        raise AudioRuntimeError("immutable snapshot contains a missing or unreadable file") from None
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        if os.path.isabs(target):
            raise AudioRuntimeError("immutable snapshot contains an absolute file link")
        candidate = Path(os.path.abspath(os.path.join(path.parent, target)))
        if candidate.parent != repo / "blobs" or not _BLOB.fullmatch(candidate.name):
            raise AudioRuntimeError("immutable snapshot file link does not point to its repository blob")
        _directories(candidate.parent)
        try:
            info = candidate.lstat()
        except OSError:
            raise AudioRuntimeError("immutable snapshot has a broken blob link") from None
        path = candidate
    if not stat.S_ISREG(info.st_mode):
        raise AudioRuntimeError("immutable snapshot entry must resolve to a regular local file")
    return path, info


def _json_file(path: Path, repo: Path):
    actual, info = _snapshot_file(path, repo)
    if info.st_size > _MAX_CONFIG_BYTES:
        raise AudioRuntimeError("model configuration exceeds the runtime metadata limit")
    descriptor = os.open(actual, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise AudioRuntimeError("model configuration changed during validation")
            payload = json.loads(handle.read(_MAX_CONFIG_BYTES + 1))
    except (ValueError, OSError):
        raise AudioRuntimeError("model configuration is not readable JSON") from None
    if not isinstance(payload, dict):
        raise AudioRuntimeError("model configuration must be a JSON object")
    return payload


def validate_snapshot(model: str):
    """Validate an exact HF cache snapshot and its local config/weight links.

    Artifact hash/completeness evidence belongs to ``models pull``. Startup
    additionally refuses mutable paths, unsafe links and absent weight shards;
    real engine preload proves the selected local files can initialize.
    """
    if not isinstance(model, str) or not os.path.isabs(model) or any(
        character in model for character in "\x00\r\n"
    ):
        raise AudioRuntimeError("--model must be an absolute immutable HF snapshot path")
    snapshot = Path(os.path.abspath(model))
    repo = snapshot.parent.parent
    if (
        not _REVISION.fullmatch(snapshot.name) or snapshot.parent.name != "snapshots"
        or not repo.name.startswith("models--") or "--" not in repo.name[len("models--"):]
    ):
        raise AudioRuntimeError("--model must select models--OWNER--REPO/snapshots/40_HEX_COMMIT")
    _directories(snapshot)
    if not snapshot.is_dir():
        raise AudioRuntimeError("immutable model snapshot does not exist")
    files = set()
    pending = [(snapshot, 0)]
    entries = 0
    while pending:
        directory, depth = pending.pop()
        if depth > 32:
            raise AudioRuntimeError("immutable snapshot exceeds the directory depth limit")
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > _MAX_ENTRIES:
                    raise AudioRuntimeError("immutable snapshot exceeds the file inventory limit")
                path = Path(child.path)
                if child.is_dir(follow_symlinks=False):
                    pending.append((path, depth + 1))
                else:
                    if path.name.endswith(".incomplete"):
                        raise AudioRuntimeError("immutable snapshot contains an incomplete file")
                    _snapshot_file(path, repo)
                    files.add(path.relative_to(snapshot).as_posix())
    config = _json_file(snapshot / "config.json", repo)
    if not isinstance(config.get("model_type"), str) or not config["model_type"]:
        raise AudioRuntimeError("model config requires its real model_type")
    weights = {name for name in files if "/" not in name and name.endswith((".safetensors", ".npz"))}
    if not weights or any(_snapshot_file(snapshot / name, repo)[1].st_size == 0 for name in weights):
        raise AudioRuntimeError("immutable snapshot has no nonempty model weight files")
    for index in ("model.safetensors.index.json", "weights.safetensors.index.json"):
        if index not in files:
            continue
        mapping = _json_file(snapshot / index, repo).get("weight_map")
        if not isinstance(mapping, dict) or not mapping:
            raise AudioRuntimeError("weight index requires a nonempty shard map")
        if any(not isinstance(name, str) or name not in weights for name in mapping.values()):
            raise AudioRuntimeError("immutable snapshot is missing a declared weight shard")
    return str(snapshot)


async def _preload(upstream, model, timeout, stop_requested):
    """Submit real model-load work on the broker's inference-owned MLX streams."""
    handle = upstream.get_inference_broker().submit(
        endpoint_kind="model-load", model_name=model, payload=None,
    )
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while True:
            if stop_requested():
                raise AudioRuntimeError("runtime shutdown requested during model preload")
            if asyncio.get_running_loop().time() >= deadline:
                raise AudioRuntimeError("model preload exceeded the startup deadline")
            try:
                chunk = handle.result_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            if chunk.kind == "error":
                raise chunk.error or AudioRuntimeError("MLX Audio model preload failed")
            if chunk.kind == "done":
                break
        advertised = await upstream.model_provider.get_available_models()
        if advertised != [model]:
            raise AudioRuntimeError("preload did not produce the exact real cached model metadata")
    finally:
        handle.cancel()


def _validate_cache_limit(value):
    if value is not None and (
        type(value) is not int or not 0 <= value <= _MAX_CACHE_LIMIT_MIB
    ):
        raise AudioRuntimeError("MLX cache limit must be an integer from 0 through 1048576 MiB")
    return value


def _verify_memory_runtime():
    try:
        version = importlib.metadata.version("mlx")
    except importlib.metadata.PackageNotFoundError:
        raise AudioRuntimeError("MLX memory controls require reviewed MLX 0.32.3") from None
    if version != MLX_MEMORY_VERSION:
        raise AudioRuntimeError("MLX memory controls require reviewed MLX 0.32.3")


def _memory_snapshot(memory_api):
    return {
        "active_bytes": memory_api.get_active_memory(),
        "cache_bytes": memory_api.get_cache_memory(),
        "peak_bytes": memory_api.get_peak_memory(),
    }


def _memory_record(event, **fields):
    _LOGGER.info("%s", json.dumps({"event": event, "launcher_policy": LAUNCHER_POLICY_VERSION,
                                 **fields}, sort_keys=True))


def _traced_generate(generate, memory_api, *, worker_thread=None):
    """Keep serial generator/value semantics and sample only its owning worker."""
    sequence = 0

    @wraps(generate)
    def traced(*args, **kwargs):
        nonlocal sequence
        if worker_thread is not None and threading.get_ident() != worker_thread:
            _memory_record("mlx_generation_memory", phase="bypass",
                           measurement="unavailable-outside-inference-worker")
            return generate(*args, **kwargs)
        sequence += 1
        request_index = sequence
        owner_thread = threading.get_ident()
        started = time.monotonic()
        _memory_record("mlx_generation_memory", phase="start", request_index=request_index,
                       peak_scope="since-prior-reset", **_memory_snapshot(memory_api))
        memory_api.reset_peak_memory()

        def finish(outcome):
            if threading.get_ident() != owner_thread:
                # A discarded generator can be finalized by unrelated GC. Never
                # touch MLX's worker-owned state from that thread.
                _memory_record("mlx_generation_memory", phase="end", request_index=request_index,
                               outcome=outcome, measurement="unavailable-outside-owning-worker")
                return
            _memory_record("mlx_generation_memory", phase="end", request_index=request_index,
                           outcome=outcome, elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                           peak_scope="serial-generation", **_memory_snapshot(memory_api))

        try:
            result = generate(*args, **kwargs)
        except BaseException:
            finish("error")
            raise
        if not isinstance(result, Iterator):
            finish("complete")
            return result

        def results():
            outcome = "closed"
            delegating = False
            try:
                # Enter the finally boundary before returning the iterator, so
                # cancellation before the first model yield is recorded too.
                yield None
                delegating = True
                yield from result
                outcome = "complete"
            except GeneratorExit:
                raise
            except BaseException:
                outcome = "error"
                raise
            finally:
                try:
                    # yield-from owns close after delegation begins. Before
                    # that boundary, close the original iterator ourselves.
                    close = getattr(result, "close", None)
                    if not delegating and callable(close):
                        close()
                except BaseException:
                    outcome = "error"
                    raise
                finally:
                    finish(outcome)

        iterator = results()
        next(iterator)
        return iterator

    return traced


def prepare_app(upstream, model, *, startup_timeout=600, stop_requested=lambda: False,
                mlx_cache_limit_mib=None, mlx_memory_trace=False):
    """Configure one provider dependency and compose the upstream ASGI lifespan."""
    model = validate_snapshot(model)
    _validate_cache_limit(mlx_cache_limit_mib)
    if type(mlx_memory_trace) is not bool:
        raise AudioRuntimeError("MLX memory trace must be a boolean")
    if not math.isfinite(startup_timeout) or not 0 < startup_timeout <= 3600:
        raise AudioRuntimeError("startup timeout must be positive and at most 3600 seconds")
    if upstream.model_provider.models or upstream.INFERENCE_BROKER is not None:
        # Never repurpose an app already executing another model or broker.
        raise AudioRuntimeError("MLX Audio runtime must start from a fresh provider and broker")
    class PinnedModelProvider(upstream.ModelProvider):
        policy_applied = False
        traced_models = None
        inference_worker = None

        def load_model(self, model_name):
            if model_name != model:
                raise AudioRuntimeError("runtime accepts only its exact pinned local snapshot model")
            if not self.policy_applied:
                self.inference_worker = threading.get_ident()
                memory_api = upstream.mx if mlx_cache_limit_mib is not None or mlx_memory_trace else None
                previous = None
                if mlx_cache_limit_mib is not None:
                    previous = memory_api.set_cache_limit(mlx_cache_limit_mib * 1024**2)
                _memory_record("mlx_memory_policy", cache_limit_mib=mlx_cache_limit_mib,
                               previous_cache_limit_bytes=previous, trace=mlx_memory_trace,
                               mlx_version=MLX_MEMORY_VERSION if memory_api else "upstream-default",
                               applied_on="inference-worker", memory_limit="upstream-default-guideline")
                self.policy_applied = True
                self.traced_models = []
            loaded = super().load_model(model_name)
            if mlx_memory_trace and not any(item is loaded for item in self.traced_models):
                generate = getattr(loaded, "generate", None)
                if callable(generate):
                    loaded.generate = _traced_generate(generate, upstream.mx,
                                                       worker_thread=self.inference_worker)
                self.traced_models.append(loaded)
            return loaded
        async def remove_model(self, model_name):
            raise AudioRuntimeError("runtime cannot remove or switch its pinned model")
    upstream.model_provider = PinnedModelProvider()
    original_lifespan = upstream.app.router.lifespan_context
    @asynccontextmanager
    async def managed_lifespan(app):
        async with original_lifespan(app):
            _LOGGER.info("preloading exact snapshot through the MLX Audio inference worker: %s", model)
            await _preload(upstream, model, startup_timeout, stop_requested)
            _LOGGER.info("real cached model is ready: %s", model)
            yield
    upstream.app.router.lifespan_context = managed_lifespan
    return upstream.app


def _logging_config(log_dir):
    handlers = {"console": {"class": "logging.StreamHandler", "stream": "ext://sys.stderr",
                            "formatter": "runtime"}}
    selected = ["console"]
    if log_dir:
        if not os.path.isabs(log_dir):
            raise AudioRuntimeError("--log-dir must be an absolute path")
        directory = Path(os.path.abspath(log_dir))
        _directories(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "mlx-audio-runtime.log"
        if os.path.lexists(target) and not stat.S_ISREG(target.lstat().st_mode):
            raise AudioRuntimeError("runtime log target must be a regular file")
        handlers["file"] = {"class": "logging.FileHandler", "filename": str(target),
                            "mode": "a", "encoding": "utf-8", "formatter": "runtime"}
        selected.append("file")
    return {"version": 1, "disable_existing_loggers": False,
            "formatters": {"runtime": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
            "handlers": handlers, "root": {"handlers": selected, "level": "INFO"},
            "loggers": {"uvicorn": {"handlers": selected, "level": "INFO", "propagate": False}}}


def _load_optional_runtime():
    return importlib.import_module("mlx_audio.server"), importlib.import_module("uvicorn")


def run(model, *, host="127.0.0.1", port=8000, log_dir=None, startup_timeout=600,
        mlx_audio_version=MLX_AUDIO_VERSION, mlx_audio_commit=MLX_AUDIO_COMMIT,
        mlx_cache_limit_mib=None, mlx_memory_trace=False, _load_upstream=None):
    """Runtime process entrypoint; uvicorn owns signals and upstream broker cleanup."""
    if host != "127.0.0.1":
        raise AudioRuntimeError("native audio runtime host must be exactly 127.0.0.1")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise AudioRuntimeError("native audio runtime port must be from 1 through 65535")
    _validate_cache_limit(mlx_cache_limit_mib)
    if type(mlx_memory_trace) is not bool:
        raise AudioRuntimeError("MLX memory trace must be a boolean")
    model = validate_snapshot(model)
    verify_runtime_pin(mlx_audio_version, mlx_audio_commit)
    if mlx_cache_limit_mib is not None or mlx_memory_trace:
        _verify_memory_runtime()
    log_config = _logging_config(log_dir)
    # This process is a launcher in the external audio environment, never the
    # core operator. Artifacts and ancillary assets must already be installed.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN"):
        os.environ.pop(name, None)
    upstream, uvicorn = (_load_upstream or _load_optional_runtime)()
    server = None
    app = prepare_app(upstream, model, startup_timeout=startup_timeout,
                      stop_requested=lambda: server is not None and server.should_exit,
                      mlx_cache_limit_mib=mlx_cache_limit_mib, mlx_memory_trace=mlx_memory_trace)
    configuration = uvicorn.Config(app, host=host, port=port, workers=1, reload=False,
                                   loop="asyncio", lifespan="on", log_config=log_config,
                                   timeout_graceful_shutdown=30)
    server = uvicorn.Server(configuration)
    server.run()
    if not server.started:
        raise AudioRuntimeError("MLX Audio server failed its real model startup")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run one pinned local MLX Audio snapshot as a managed native service.")
    parser.add_argument("--model", required=True, help="absolute models--OWNER--REPO/snapshots/COMMIT path")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1",))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-dir", help="optional absolute directory for append-only runtime logs")
    parser.add_argument("--startup-timeout", type=float, default=600, help="model preload deadline in seconds, at most 3600")
    parser.add_argument("--mlx-audio-version", default=MLX_AUDIO_VERSION, choices=(MLX_AUDIO_VERSION,))
    parser.add_argument("--mlx-audio-commit", default=MLX_AUDIO_COMMIT, choices=(MLX_AUDIO_COMMIT,))
    parser.add_argument("--mlx-cache-limit-mib", type=int,
                        help="optional MLX 0.32.3 free-buffer cache limit, 0..1048576 MiB; 0 disables cache")
    parser.add_argument("--mlx-memory-trace", action="store_true",
                        help="record serial generation active/cache/peak bytes on the inference worker")
    args = parser.parse_args(argv)
    try:
        return run(args.model, host=args.host, port=args.port, log_dir=args.log_dir,
                   startup_timeout=args.startup_timeout, mlx_audio_version=args.mlx_audio_version,
                   mlx_audio_commit=args.mlx_audio_commit,
                   mlx_cache_limit_mib=args.mlx_cache_limit_mib, mlx_memory_trace=args.mlx_memory_trace)
    except (AudioRuntimeError, ImportError, OSError) as exc:
        print("native MLX Audio runtime refused: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
