#!/usr/bin/env python3
"""multiplexer.py - supply-side on-demand model multiplexer for anvil-serving (T009).

ONE OpenAI-compatible endpoint that loads/swaps which model is resident on the
GPU on demand, within host-RAM limits. Single-resident: a request for a
different model stops the old backend and starts the new (an atomic swap);
concurrent requests for the already-resident model are served with NO restart.

The hard-won gotcha this guards against (repo gotcha #1/#2): with
--weight-loader-disable-mmap (needed because mmap over virtiofs is
pathologically slow) the backend loads the WHOLE weight file into host RAM;
under a WSL2 ~50%% memory cap that OOM-kills the child mid-load (scheduler died,
exit code -9). We PREDICT this and refuse with a clean 503 instead of a SIGKILL.

The backend start/stop (sglang/docker) sits behind an INJECTABLE seam so the
self-check runs with a MockBackend and NO GPU / NO subprocess.

Usage:
  python3 -m anvil_serving.multiplexer [--registry PATH] [--host H] [--port P]
                                       [--ram-cap-gb N] [--self-check]
  GET  /v1/models            list servable models (never triggers a load)
  POST /v1/chat/completions  load/swap on demand, proxy body to resident backend
  POST /v1/completions       same load/swap + proxy
  GET  /healthz              {"resident": name|null, "registry": [names]}

Exit 0 = --help or --self-check pass; 1 = self-check failure.
"""
import argparse
from . import guard
import io
import json
import os
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .gpus import gpu_uuid  # noqa: F401 (re-exported; shared with `deploy` — T007)

# --- REGISTRY TABLE: single source of truth for /v1/models AND the loader ----
# Columns: name (served /v1 id) | engine ('sglang'|'vllm', picks build_cmd branch
#   + container image) | model_path (dir handed to backend) | est_weight_gb
#   (mmap-off host-RAM cost -> drives the OOM guard) | gpu | port (per-backend
#   upstream) | args (engine-native flags spliced verbatim into the launch argv).
# engine/args are OPTIONAL (read with .get() defaults: engine -> 'sglang', args ->
# []) so REQUIRED_KEYS stays unchanged and old/minimal registries still validate.
REGISTRY = [
    # heavy (gpu 1): qwen3-coder-30b AWQ via SGLang
    {"name": "qwen3-coder-local", "engine": "sglang",
     "model_path": "/models/qwen3-coder-30b-awq",
     "volume": "fakoli-models", "est_weight_gb": 18,
     "gpu": 1, "port": 30000,
     "args": ["--attention-backend", "triton", "--tool-call-parser", "qwen3_coder",
              "--kv-cache-dtype", "fp8_e5m2", "--context-length", "131072",
              "--mem-fraction-static", "0.9"]},
    # fast/coding (gpu 0, swaps with qwen3-14b): gpt-oss-20b via vLLM (VALIDATED fast pick)
    {"name": "gpt-oss-20b", "engine": "vllm",
     "model_path": "/models/gpt-oss-20b",
     "volume": "fakoli-models", "est_weight_gb": 13,
     "gpu": 0, "port": 30001,
     "args": ["--max-model-len", "65536", "--gpu-memory-utilization", "0.90",
              "--tool-call-parser", "openai", "--enable-auto-tool-choice"]},
    # GLM-4.7-Flash is PARKED: non-viable on this sm_120 box (SGLang #25331 won't load;
    # vLLM MLA crash on long-context) -> replaced above by the validated gpt-oss-20b.
    # fast/safe (gpu 0, swaps with gpt-oss): qwen3-14b AWQ via SGLang
    {"name": "qwen3-14b-fast", "engine": "sglang",
     "model_path": "/models/qwen3-14b-awq",
     "volume": "fakoli-models", "est_weight_gb": 9,
     "gpu": 0, "port": 30001,
     "args": ["--quantization", "awq_marlin", "--attention-backend", "flashinfer",
              "--reasoning-parser", "qwen3", "--tool-call-parser", "qwen3_coder",
              "--kv-cache-dtype", "fp8_e5m2", "--context-length", "40960",
              "--mem-fraction-static", "0.85"]},
]
# NOTE: the two gpu-0 rows (gpt-oss-20b, qwen3-14b-fast) intentionally share
# upstream port 30001 — they are a single-resident SWAP PAIR (only one is ever
# resident on GPU 0), so the shared port is correct by design, not a collision.
# More broadly, per-row gpu/port are INFORMATIONAL under this multiplexer's GLOBAL
# single-resident model (only ONE backend is ever up at a time) — they document the
# intended placement, not a guarantee of concurrent residency (review note, not a bug).
# volume is the named docker volume holding the model dirs (mounted read-only at the
# parent of model_path). host_path (host bind mount) remains supported for minimal/test
# rows ONLY -- never use it for real weights: a host bind rides 9P/virtiofs on Docker
# Desktop/WSL2 and makes cold loads pathological (operator rule 2026-07-02).
# est_weight_gb are 4-bit AWQ footprint ESTIMATES feeding the OOM guard; correct
# them against real `models sync` facts when available.

# Engine -> container image. The only place engine names map to images; an engine
# absent here resolves to None so SubprocessBackend.start fails fast (pure guard).
ENGINE_IMAGE = {
    "sglang": "lmsysorg/sglang:latest",
    "vllm": "vllm/vllm-openai:latest",
}


def build_cmd(entry):
    """PURE: a registry row -> the in-container server argv, dispatched by engine.

    No subprocess, no GPU — docker_run_cmd wraps the result in `docker run`. Kept
    pure so the self-check can assert engine dispatch by inspecting the returned
    argv (no real launch).

    argv[0] is the engine BINARY ('vllm' for vllm, 'python3' for sglang); it is NOT
    re-passed as a positional — docker_run_cmd promotes it to `--entrypoint` so the
    image's own ENTRYPOINT (vllm/vllm-openai sets ['vllm','serve']) can't shadow and
    DOUBLE our command into `vllm serve vllm serve ...`.

    Single `if engine=='vllm' else sglang` branch (no plugin/registry-of-engines).
    The sglang branch bakes in ONLY the always-on defaults (--weight-loader-disable-mmap
    [gotcha #2], --enable-metrics, --max-running-requests 16); every other flag is
    per-row via `args`."""
    name, mp, port = entry["name"], entry["model_path"], entry["port"]
    args = entry.get("args", [])
    if isinstance(args, str):
        args = args.split()
    common = ["--served-model-name", name, "--host", "0.0.0.0", "--port", str(port)]
    if entry.get("engine", "sglang") == "vllm":
        # argv[0]='vllm' -> docker_run_cmd sets --entrypoint vllm, then 'serve <path> ...'
        return ["vllm", "serve", mp, *args, *common]
    # sglang (default): argv[0]='python3' -> --entrypoint python3, then the module run.
    return ["python3", "-m", "sglang.launch_server", "--model-path", mp,
            "--weight-loader-disable-mmap", "--enable-metrics",
            "--max-running-requests", "16", *args, *common]


def docker_run_cmd(entry):
    """PURE: a registry row -> the full `docker run` argv (no subprocess, no GPU).

    Overrides the image ENTRYPOINT with build_cmd's argv[0] (the engine binary) so an
    image whose own entrypoint is the API server (vllm/vllm-openai -> ['vllm','serve'])
    cannot SHADOW and double our command. The result runs `--entrypoint <bin> ... image
    <rest-of-argv>`, never a stray doubled positional.

    GPU isolation: if the entry carries a resolved `gpu_uuid` (set at launch by
    SubprocessBackend), emit `--gpus all -e CUDA_DEVICE_ORDER=PCI_BUS_ID
    -e CUDA_VISIBLE_DEVICES=<uuid>` — the ONLY isolation Docker Desktop's WSL2 backend
    actually honors (`--gpus device=N` is silently ignored there and exposes ALL GPUs).
    Without a gpu_uuid, fall back to `--gpus device={gpu}` so the self-check and
    non-WSL hosts (which DO honor device=) still work.

    Weights mount: rows with a "volume" key mount that named docker volume read-only
    at the parent of model_path (never a 9P host bind). Legacy host_path bind mounts
    remain supported for minimal/test rows; host_path defaults to model_path."""
    image = ENGINE_IMAGE.get(entry.get("engine", "sglang"))
    if image is None:
        raise BackendError(
            f"unknown engine {entry.get('engine')!r} for {entry['name']}")
    argv = build_cmd(entry)
    host_path = entry.get("host_path", entry["model_path"])
    volume = entry.get("volume")
    if volume:
        # Named docker volume holding the model dirs, mounted read-only at the PARENT
        # of model_path (fakoli-models:/models:ro with model_path=/models/<dir>).
        # NEVER a host bind mount: on Docker Desktop/WSL2 a host bind rides 9P/virtiofs
        # and makes cold loads pathological (~12 MB/s; 20-90 min) -- operator rule
        # 2026-07-02, see examples/fakoli-dark/docker-compose.yml header.
        mount = f"{volume}:{os.path.dirname(entry['model_path'])}:ro"
    else:
        mount = f"{host_path}:{entry['model_path']}"
    port = entry["port"]
    uuid = entry.get("gpu_uuid")
    if uuid:  # reliable isolation: expose all, pin by UUID via CUDA_VISIBLE_DEVICES
        gpu_flags = ["--gpus", "all",
                     "-e", "CUDA_DEVICE_ORDER=PCI_BUS_ID",
                     "-e", f"CUDA_VISIBLE_DEVICES={uuid}"]
    else:     # fallback (self-check + non-WSL hosts that honor device=N)
        gpu_flags = ["--gpus", f"device={entry['gpu']}"]
    return ["docker", "run", "--rm", "--name", f"anvil-{entry['name']}",
            # promote argv[0] to --entrypoint so the image entrypoint can't shadow it
            "--entrypoint", argv[0],
            *gpu_flags,
            "--shm-size", "16g",
            "-p", f"{port}:{port}",
            "-v", mount,
            image, *argv[1:]]


class LoadError(Exception):
    """Raised by the OOM guard when a load would exceed the RAM budget -> 503
    insufficient_memory. Strictly a RAM-shortage signal, NOT a startup failure."""


class BackendError(Exception):
    """Backend failed to START (no renderer, startup timeout, subprocess/docker
    failure) -> 503 backend_unavailable. Distinct from LoadError so a startup
    failure is never misreported as a RAM shortage."""


class UnknownModel(Exception):
    """Requested model id is not in the registry -> 404 model_not_found."""


# Every registry row MUST carry these (they drive routing + the OOM guard).
REQUIRED_KEYS = ("name", "model_path", "est_weight_gb", "gpu", "port")


def load_registry(path):
    """Load a registry TABLE (list[dict]) from JSON, else return the default.
    Validates required columns so a malformed --registry fails fast with a clear
    error instead of later misrouting to a confusing 404."""
    if not path:
        return [dict(r) for r in REGISTRY]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data["models"] if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"registry {path!r} must be a non-empty list of rows")
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"registry {path!r} row {i} must be an object, got {type(row).__name__}")
        missing = [k for k in REQUIRED_KEYS if k not in row]
        if missing:
            raise ValueError(f"registry {path!r} row {i} ({row.get('name', '?')!r}) "
                             f"missing required keys: {missing}")
    return rows


def available_ram_gb():
    """Best-effort available host RAM in GB. Linux/WSL first (respects the VM/cgroup
    cap we actually run under); Windows fallback for the dev box; inf if unknown."""
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e9
    except (ValueError, AttributeError, OSError):
        pass
    try:  # Windows fallback: GlobalMemoryStatusEx via ctypes
        import ctypes

        class _MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("ml", ctypes.c_ulong),
                        ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("avv", ctypes.c_ulonglong),
                        ("ae", ctypes.c_ulonglong)]

        m = _MS()
        m.l = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ap / 1e9
    except Exception:
        return float("inf")  # unknown -> don't block; let the real backend decide


def oom_guard(entry, avail_gb, cap_gb=None, safety=1.15):
    """Refuse a load that would exceed the RAM budget. Raises LoadError (no SIGKILL).

    need = est_weight_gb * safety  (mmap-off cost + headroom for activations/runtime).
    budget = avail_gb, optionally pinned below the probe by --ram-cap-gb (e.g. to
    model the ~50%% WSL cap explicitly)."""
    need = entry["est_weight_gb"] * safety
    budget = avail_gb if cap_gb is None else min(avail_gb, cap_gb)
    if need > budget:
        raise LoadError(
            f"would OOM loading {entry['name']}: needs ~{need:.0f} GB "
            f"(est_weight {entry['est_weight_gb']} x {safety}), only {budget:.0f} GB "
            f"available (raise WSL .wslconfig memory= or pick a smaller/quantized model)")


# --- INJECTABLE BACKEND SEAM (duck-typed; attr `current`, methods start/stop) -
class SubprocessBackend:
    """Default real backend: launch the entry's engine (sglang or vllm) in a
    `docker run` container (via the pure docker_run_cmd) pinned to its GPU, block
    until its /health is up. Idempotent stop. GPU-touching; NOT used in the
    self-check. Only mmap-off + metrics (+ a concurrency cap) are baked into the
    sglang launch; every other flag is per-row `args`."""

    def __init__(self, startup_timeout=600):
        self.startup_timeout = startup_timeout
        self.current = None
        self._proc = None

    def start(self, entry):
        import subprocess
        port = entry["port"]
        # Resolve the GPU UUID at launch (IMPURE) and inject it into a COPY of the
        # entry so the pure docker_run_cmd emits `--gpus all` + CUDA_VISIBLE_DEVICES
        # isolation — the only pinning Docker Desktop's WSL2 backend actually honors
        # (`--gpus device=N` is ignored there). None -> docker_run_cmd falls back to
        # device=N (non-WSL hosts honor that).
        launch = dict(entry)
        uuid = gpu_uuid(entry["gpu"])
        if uuid:
            launch["gpu_uuid"] = uuid
        # docker_run_cmd is PURE (and self-checked): it overrides --entrypoint with the
        # engine binary so the image entrypoint can't shadow/double the command, and
        # bind-mounts host_path -> model_path. Raises BackendError on unknown engine.
        cmd = docker_run_cmd(launch)
        self._container = f"anvil-{entry['name']}"
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        except OSError as e:  # docker not on PATH, exec failure -> clean 503
            raise BackendError(f"cannot launch docker for {entry['name']}: {e}")
        base = f"http://127.0.0.1:{port}/v1"
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            # A child that already exited (port conflict, missing image, daemon
            # down) can never become healthy: fail NOW instead of spinning the
            # full startup timeout while holding the multiplexer lock.
            rc = self._proc.poll()
            if rc is not None:
                self._cleanup(best_effort=True)
                raise BackendError(
                    f"docker run for {entry['name']} exited rc={rc} before "
                    f"the backend came up")
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=5) as r:
                    if r.status == 200:
                        self.current = entry["name"]
                        return base
            except Exception:
                time.sleep(2)
        # Timed out: clean up best-effort so the timeout error (the diagnosis
        # that matters) is never masked by a failing docker rm.
        self._cleanup(best_effort=True)
        raise BackendError(f"backend for {entry['name']} did not come up within "
                           f"{self.startup_timeout}s")

    def stop(self):
        """Evict the resident backend. Raises :class:`BackendError` if the
        container could not be confirmed removed — the GPU is then still
        occupied, and pretending it is free would make the NEXT start collide
        on the port/VRAM and fail far more opaquely."""
        self._cleanup(best_effort=False)

    def _cleanup(self, *, best_effort):
        import subprocess
        c = getattr(self, "_container", None)
        rm_failed = None
        if c:
            try:
                rc = subprocess.call(["docker", "rm", "-f", c],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.STDOUT)
                if rc != 0:
                    rm_failed = f"docker rm -f {c} exited rc={rc}"
            except Exception as e:
                rm_failed = f"docker rm -f {c} raised {type(e).__name__}: {e}"
        if self._proc is not None:
            # Reap the docker-run client so a swap-heavy server doesn't
            # accumulate zombies. guard.terminate_then_kill is the canonical
            # one-attempt escalation (terminate -> kill, bounded, never loops).
            guard.terminate_then_kill(self._proc)
            self._proc = None
        if rm_failed and not best_effort:
            # Do NOT clear `current`: the old container may still hold the GPU.
            raise BackendError(
                f"could not remove container ({rm_failed}); the GPU may still "
                f"be occupied — refusing to swap on top of it")
        self.current = None


class MockBackend:
    """Self-check backend: no GPU, no subprocess, fully deterministic.
    `fail_on` is a set of model names whose start() raises BackendError, so the
    swap-rollback path can be exercised without any real backend."""

    def __init__(self, fail_on=()):
        self.started = []
        self.stops = 0
        self.current = None
        self.fail_on = set(fail_on)

    def start(self, entry):
        if entry["name"] in self.fail_on:
            raise BackendError(f"mock start failure for {entry['name']}")
        self.started.append(entry["name"])
        self.current = entry["name"]
        return "http://mock/" + entry["name"]

    def stop(self):
        if self.current is not None:
            self.stops += 1
            self.current = None


# --- MULTIPLEXER: holds the registry + the injected seams + swap logic --------
class Multiplexer:
    #: Default seconds a swap waits for in-flight requests on the OLD resident
    #: to finish before stopping it anyway (bounded availability: a hung or
    #: glacial client must not block model swaps forever).
    DEFAULT_DRAIN_TIMEOUT = 30.0

    def __init__(self, registry, backend, ram_probe=available_ram_gb, ram_cap_gb=None,
                 drain_timeout=DEFAULT_DRAIN_TIMEOUT):
        self.table = {r["name"]: r for r in registry}
        self.order = [r["name"] for r in registry]
        self.backend = backend          # <-- injectable backend seam
        self.ram_probe = ram_probe      # <-- injectable for deterministic OOM test
        self.cap = ram_cap_gb
        self.resident = None
        self.base_url = None
        self.drain_timeout = drain_timeout
        # One lock + condition governs ALL shared state (resident/base_url/
        # in-flight counts/swap flag). The relay itself runs OUTSIDE the lock;
        # only the bookkeeping around it takes it (ADR-0006).
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._inflight = {}             # model name -> live lease count
        self._swapping = False          # a swap is draining/loading right now

    def models_payload(self):
        """OpenAI /v1/models body. NEVER triggers a load."""
        return {"object": "list",
                "data": [{"id": n, "object": "model", "owned_by": "anvil"}
                         for n in self.order]}

    def inflight(self, name):
        """Live lease count for `name` (introspection/tests)."""
        with self._cond:
            return self._inflight.get(name, 0)

    def lease(self, name):
        """Context manager: ensure `name` is resident, hold an in-flight lease
        on it for the duration of the block, and yield its base_url.

        The lease is what a swap DRAINS against (ADR-0006): while any lease on
        the resident model is live, `ensure_loaded(<other>)` waits (up to
        `drain_timeout`) before stopping the resident backend, so an active
        relay is not severed mid-stream. Acquisition is atomic with the
        resident check — a swap can never sneak between "ensure_loaded
        returned" and "the relay registered itself"."""
        return _Lease(self, name)

    def _acquire(self, name):
        with self._cond:
            base = self._ensure_loaded_locked(name)
            self._inflight[name] = self._inflight.get(name, 0) + 1
            return base

    def _release(self, name):
        with self._cond:
            n = self._inflight.get(name, 0) - 1
            if n > 0:
                self._inflight[name] = n
            else:
                self._inflight.pop(name, None)
            # Wake a swap waiting for the drain (and any queued requests).
            self._cond.notify_all()

    def ensure_loaded(self, name):
        """Make `name` resident and return its backend base_url. Load-on-demand,
        single-resident swap, OOM-guarded, drain-aware (ADR-0006). Raises
        UnknownModel on unknown model, LoadError on OOM-guard refusal,
        BackendError on a start failure."""
        with self._cond:
            return self._ensure_loaded_locked(name)

    def _ensure_loaded_locked(self, name):
        """Core load/swap logic. Caller holds ``self._cond``."""
        if name not in self.table:
            raise UnknownModel(name)
        # Queue behind an in-progress swap: Condition.wait releases the lock
        # while the swapping thread drains/loads, so without this gate a
        # request for the OLD model could keep taking fresh leases on the
        # dying resident and starve the swap forever. New arrivals wait here
        # and re-evaluate residency once the swap settles.
        while self._swapping:
            self._cond.wait()
        # AC3: already resident -> serve immediately, NO restart, NO churn
        if self.resident == name and self.backend.current == name:
            return self.base_url
        entry = self.table[name]
        # AC2: run the OOM guard FIRST, BEFORE draining/evicting the good model.
        # Credit the evictee's weight to the budget: its mmap-off weights
        # are part of what the probe currently sees as used, and a SWAP
        # frees them before the new load. Without the credit, a legitimate
        # swap on a tight box is falsely 503'd forever (e.g. 18 GB resident
        # of a 32 GB budget leaves ~14 GB "available", refusing a 13 GB
        # model whose eviction-adjusted budget is really ~32 GB).
        avail = self.ram_probe()
        prior_resident = (
            self.table.get(self.resident) if self.resident else None
        )
        if self.backend.current is not None and prior_resident is not None:
            avail += prior_resident.get("est_weight_gb", 0)
        oom_guard(entry, avail, self.cap)
        # Capture the prior resident so a failed swap can be rolled back.
        prior_name = self.resident
        prior_entry = self.table.get(prior_name) if prior_name else None
        self._swapping = True
        try:
            # SWAP: single-resident GPU -> drain old, stop old, start new
            if self.backend.current is not None:
                # DRAIN (ADR-0006): wait for live leases on the old resident to
                # finish before stopping it, so no active relay is severed.
                # Bounded: after `drain_timeout` the swap proceeds anyway and
                # the laggards are severed (logged) — availability of the NEW
                # model must not hinge on a hung client. cond.wait releases
                # the lock, so leases can drain (and queued requests park in
                # the `while self._swapping` gate above).
                if prior_name is not None:
                    deadline = time.monotonic() + self.drain_timeout
                    while self._inflight.get(prior_name, 0) > 0:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            print(
                                f"[multiplexer] drain timeout ({self.drain_timeout}s): "
                                f"{self._inflight.get(prior_name, 0)} in-flight "
                                f"request(s) on {prior_name!r} will be severed by "
                                f"the swap to {name!r}", flush=True)
                            break
                        self._cond.wait(timeout=remaining)
                self.backend.stop()
                self.resident = None
                self.base_url = None
            try:
                self.base_url = self.backend.start(entry)
            except Exception:
                # SWAP ROLLBACK: the new model failed to start. Don't leave the
                # GPU empty after evicting a good resident — try to restore it.
                if prior_entry is not None:
                    try:
                        self.base_url = self.backend.start(prior_entry)
                        self.resident = prior_name
                    except Exception:
                        self.resident = None  # prior also failed -> clean empty state
                        self.base_url = None
                else:
                    self.resident = None
                    self.base_url = None
                raise  # surface the original start failure (BackendError -> 503)
            self.resident = name
            return self.base_url
        finally:
            self._swapping = False
            self._cond.notify_all()  # release the queued requests


class _Lease:
    """Context manager pairing `Multiplexer._acquire` with `_release`.

    A tiny class (not ``@contextmanager``) so acquisition happens in
    ``__enter__`` — constructing the lease object is side-effect free, and a
    failed acquire (UnknownModel/LoadError/BackendError) propagates before any
    lease is registered."""

    __slots__ = ("_mux", "_name", "base_url")

    def __init__(self, mux, name):
        self._mux = mux
        self._name = name
        self.base_url = None

    def __enter__(self):
        self.base_url = self._mux._acquire(self._name)
        return self.base_url

    def __exit__(self, *exc):
        self._mux._release(self._name)
        return False


# --- HTTP FRONT: one handler maps OpenAI routes onto the Multiplexer ----------
def _open_upstream(url, body, timeout=900):
    """POST raw `body` bytes to `url` and return the OPEN response object (caller
    streams + closes it). The body is never read/re-encoded here, so streaming
    (stream:true -> text/event-stream) is preserved end to end."""
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def relay(resp, h, chunk_size=65536):
    """Stream an already-open upstream response `resp` to handler-like `h`
    INCREMENTALLY, copying the upstream status + Content-Type verbatim (no JSON
    re-encode). Length-delimited when the upstream sends Content-Length (typical
    non-streaming reply); close-delimited (Connection: close, flush per chunk)
    when it does not — which is the streaming/SSE case. `h` needs send_response,
    send_header, end_headers, a `wfile` with write/flush, and close_connection."""
    status = getattr(resp, "status", None)
    if status is None:
        status = getattr(resp, "code", 200)  # urllib HTTPError carries .code
    ctype = resp.headers.get("Content-Type", "application/octet-stream")
    clen = resp.headers.get("Content-Length")
    cenc = resp.headers.get("Content-Encoding")
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    if cenc is not None:
        h.send_header("Content-Encoding", cenc)  # body is copied verbatim
    if clen is not None:
        h.send_header("Content-Length", clen)  # non-streaming: pass length through
    else:
        h.close_connection = True              # streaming: close-delimited body
        h.send_header("Connection", "close")
    h.end_headers()
    # read1() returns as soon as ANY bytes are available in the current chunk;
    # plain read(n) on an http.client response BLOCKS until n bytes accumulate
    # or EOF — which turns an SSE stream of tiny `data:` events into one big
    # end-of-stream delivery (TTFT == full completion time). Fall back to
    # read(n) for response-like objects without read1 (tests, HTTPError).
    read = getattr(resp, "read1", None) or resp.read
    while True:
        chunk = read(chunk_size)
        if not chunk:
            break
        h.wfile.write(chunk)
        h.wfile.flush()  # push each chunk to the client immediately (SSE)


def make_handler(mux):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status, obj=None, raw=None):
            payload = raw if raw is not None else json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _err(self, status, etype, message):
            self._send(status, {"error": {"type": etype, "message": message}})

        def do_GET(self):
            if self.path.rstrip("/") == "/v1/models":
                self._send(200, mux.models_payload())
            elif self.path.rstrip("/") == "/healthz":
                self._send(200, {"resident": mux.resident, "registry": mux.order})
            else:
                self._err(404, "not_found", f"no route {self.path}")

        def do_POST(self):
            path = self.path
            if path not in ("/v1/chat/completions", "/v1/completions"):
                self._err(404, "not_found", f"no route {path}")
                return
            # Chunked request bodies are not decoded here (mirrors the router
            # front door): reject explicitly instead of reading an empty body
            # and answering a misleading "bad body".
            if self.headers.get_all("Transfer-Encoding"):
                self.close_connection = True
                self._err(411, "invalid_request",
                          "chunked request bodies are unsupported; send "
                          "Content-Length")
                return
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n)
            try:
                model = json.loads(body)["model"]
            except Exception as e:
                self._err(400, "invalid_request", f"bad body: {e}")
                return
            if not isinstance(model, str):
                # A non-string model (e.g. an object) must be a clean 400, not
                # an unhashable-type TypeError out of the registry lookup.
                self._err(400, "invalid_request",
                          f"model must be a string, got {type(model).__name__}")
                return
            # lease(): load/swap happens inside acquisition; the lease is then
            # HELD for the whole relay so a concurrent swap drains (waits for)
            # this request instead of stopping the backend under it (ADR-0006).
            try:
                lease = mux.lease(model)
                base = lease.__enter__()
            except UnknownModel:
                self._err(404, "model_not_found",
                          f"unknown model {model!r}; known: {mux.order}")
                return
            except LoadError as e:
                self._err(503, "insufficient_memory", str(e))  # OOM guard: RAM shortage
                return
            except BackendError as e:
                self._err(503, "backend_unavailable",       # startup failure (not RAM)
                          f"backend failed to start: {e}")
                return
            except Exception as e:
                # Anything else is a bug, but the caller still deserves an HTTP
                # response instead of a connection reset + server traceback.
                self._err(500, "internal_error",
                          f"unexpected error: {type(e).__name__}")
                return
            try:
                # AC1/streaming: open upstream and relay verbatim, incrementally.
                url = base + path[len("/v1"):]
                try:
                    resp = _open_upstream(url, body)
                except urllib.error.HTTPError as e:
                    resp = e  # relay the upstream error status + body verbatim
                except (urllib.error.URLError, OSError) as e:
                    # AC2: backend down / connection refused / drain-timeout
                    # severed mid-flight -> clean 503 instead of dropping the
                    # request with a raw traceback.
                    self._err(503, "backend_unavailable", f"backend unreachable: {e}")
                    return
                try:
                    relay(resp, self)
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
            finally:
                lease.__exit__(None, None, None)

        def log_message(self, *a):  # quiet
            pass

    return Handler


def serve(mux, host="127.0.0.1", port=8000):
    # Loopback by default (project security posture — CLAUDE.md / SECURITY.md):
    # this endpoint is unauthenticated and can drive GPU model load/swap, so a
    # non-loopback bind must be an explicit operator decision, warned loudly.
    if host not in ("127.0.0.1", "::1"):
        print(f"WARNING: binding the multiplexer to {host!r} exposes an "
              f"UNAUTHENTICATED model-swap endpoint on the network; any peer "
              f"can load/evict GPU models. See SECURITY.md.", flush=True)
    httpd = ThreadingHTTPServer((host, port), make_handler(mux))
    print(f"anvil multiplexer on http://{host}:{port}  models={mux.order}")
    httpd.serve_forever()


# --- SELF-CHECK: assert-based, MockBackend, no GPU/sockets/subprocess ---------
def _self_check():
    reg = [
        {"name": "a", "model_path": "/m/a", "est_weight_gb": 20, "gpu": 0,
         "port": 30000, "extra": {}},
        {"name": "b", "model_path": "/m/b", "est_weight_gb": 34, "gpu": 0,
         "port": 30001, "extra": {}},
    ]
    be = MockBackend()
    mux = Multiplexer(reg, be, ram_probe=lambda: 100.0)  # plenty of RAM

    # /v1/models lists the table and triggers NO load
    ids = [r["id"] for r in mux.models_payload()["data"]]
    assert ids == ["a", "b"] and be.started == [], ids

    # AC1: load-on-demand — first request for "a" starts it once
    url = mux.ensure_loaded("a")
    assert url == "http://mock/a" and be.started == ["a"] and be.current == "a"

    # AC3: concurrent/repeat request for resident model — NO restart, NO churn
    for _ in range(5):
        assert mux.ensure_loaded("a") == "http://mock/a"
    assert be.started == ["a"] and be.stops == 0  # still loaded once, never bounced

    # SWAP: a different model -> stop old, start new (single-resident)
    url_b = mux.ensure_loaded("b")
    assert (url_b == "http://mock/b" and be.started == ["a", "b"]
            and be.stops == 1 and be.current == "b")

    # AC2: OOM-reject is graceful AND non-destructive on a cold load.
    tight = Multiplexer(reg, MockBackend(), ram_probe=lambda: 16.0)  # only 16 GB free
    try:
        tight.ensure_loaded("a")  # 20*1.15=23 > 16 -> rejected
        assert False, "expected LoadError"
    except LoadError as e:
        assert "would OOM" in str(e)
    assert tight.resident is None and tight.backend.stops == 0  # nothing started/evicted

    # AC2 non-destructive on a LIVE server: low RAM must not kill the resident model.
    mux2 = Multiplexer(reg, MockBackend(), ram_probe=lambda: 100.0)
    mux2.ensure_loaded("a")
    mux2.ram_probe = lambda: 16.0  # RAM drops; a swap to "b" would now OOM
    try:
        mux2.ensure_loaded("b")
        assert False, "expected LoadError"
    except LoadError:
        pass
    assert (mux2.resident == "a" and mux2.backend.current == "a"
            and mux2.backend.stops == 0)  # "a" survives

    # unknown model -> UnknownModel (handler maps to 404), no load attempted
    try:
        mux.ensure_loaded("nope")
        assert False, "expected UnknownModel"
    except UnknownModel:
        pass

    # load_registry validation: a malformed row fails fast (not a later misrouted 404)
    import tempfile
    bad = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump([{"name": "x"}], bad)  # missing model_path/est_weight_gb/gpu/port
    bad.close()
    try:
        load_registry(bad.name)
        assert False, "expected ValueError for malformed registry"
    except ValueError as e:
        assert "missing required keys" in str(e), e
    os.unlink(bad.name)

    # --- STREAMING RELAY: an SSE-style chunked body must pass through untouched.
    class _FakeResp:
        def __init__(self, status, ctype, chunks, clen=None):
            self.status = status
            self.headers = {"Content-Type": ctype}
            if clen is not None:
                self.headers["Content-Length"] = str(clen)
            self._chunks = list(chunks)
        def read(self, n=-1):
            return self._chunks.pop(0) if self._chunks else b""
        def close(self):
            pass

    class _FakeHandler:
        def __init__(self):
            self.status = None
            self.sent = []
            self.wfile = io.BytesIO()
            self.close_connection = False
            self.ended = False
        def send_response(self, s): self.status = s
        def send_header(self, k, v): self.sent.append((k, v))
        def end_headers(self): self.ended = True

    sse = [b"data: {\"i\":1}\n\n", b"data: {\"i\":2}\n\n", b"data: [DONE]\n\n"]
    fr = _FakeResp(200, "text/event-stream", sse)  # no Content-Length -> streamed
    fh = _FakeHandler()
    relay(fr, fh)
    hdrs = dict(fh.sent)
    assert fh.status == 200, fh.status
    assert hdrs.get("Content-Type") == "text/event-stream", hdrs           # NOT forced json
    assert "Content-Length" not in hdrs and fh.close_connection is True    # close-delimited
    assert fh.wfile.getvalue() == b"".join(sse)                            # bytes verbatim
    assert fh.ended

    # NON-STREAMING relay still works: real Content-Type + length, bytes verbatim.
    nb = b'{"id":"x","object":"chat.completion","choices":[]}'
    fr2 = _FakeResp(200, "application/json", [nb], clen=len(nb))
    fh2 = _FakeHandler()
    relay(fr2, fh2)
    hdrs2 = dict(fh2.sent)
    assert hdrs2.get("Content-Type") == "application/json"
    assert hdrs2.get("Content-Length") == str(len(nb)) and fh2.close_connection is False
    assert fh2.wfile.getvalue() == nb

    # --- SWAP ROLLBACK: a non-OOM start failure mid-swap must not strand the GPU.
    # Case A: the new model fails -> restore the prior resident; error still surfaces.
    be3 = MockBackend(fail_on={"b"})
    mux3 = Multiplexer(reg, be3, ram_probe=lambda: 100.0)
    mux3.ensure_loaded("a")
    try:
        mux3.ensure_loaded("b")     # stop "a", start "b" raises -> rollback to "a"
        assert False, "expected BackendError"
    except BackendError:
        pass
    assert (mux3.resident == "a" and mux3.backend.current == "a"), \
        (mux3.resident, mux3.backend.current)  # good model restored, no half-state

    # Case B: the prior also fails to come back -> resident=None, clean empty state.
    be4 = MockBackend()
    mux4 = Multiplexer(reg, be4, ram_probe=lambda: 100.0)
    mux4.ensure_loaded("a")
    be4.fail_on = {"a", "b"}        # now every start fails
    try:
        mux4.ensure_loaded("b")
        assert False, "expected BackendError"
    except BackendError:
        pass
    assert (mux4.resident is None and mux4.backend.current is None), \
        (mux4.resident, mux4.backend.current)  # no half-state, error surfaced

    # --- ENGINE DISPATCH: build_cmd is pure -> assert correct engine by inspecting
    # argv (no Multiplexer, no backend, no launch). Uses the real REGISTRY.
    vrow = next(r for r in REGISTRY if r["engine"] == "vllm")
    vcmd = build_cmd(vrow)
    assert "vllm" in vcmd and vrow["model_path"] in vcmd, vcmd      # vllm + model path
    assert "openai" in vcmd, vcmd                                   # gpt-oss tool-call-parser flag spliced
    assert "sglang.launch_server" not in vcmd, vcmd                 # NOT the wrong engine

    srow = next(r for r in REGISTRY if r["engine"] == "sglang")
    scmd = build_cmd(srow)
    assert "sglang.launch_server" in scmd and srow["model_path"] in scmd, scmd
    assert "qwen3_coder" in scmd, scmd                              # a qwen flag spliced
    assert "vllm" not in scmd, scmd                                 # NOT the wrong engine
    assert "--weight-loader-disable-mmap" in scmd, scmd            # gotcha #2 default preserved
    assert "--max-running-requests" in scmd, scmd                  # concurrency cap restored

    # --- DOCKER ARGV (the bug that timed out every vllm row): the image ENTRYPOINT
    # MUST be overridden so it can't shadow+double the command. Inspect the FULL argv
    # positionally (substring-only checks are exactly what let the doubling slip).
    vdock = docker_run_cmd(vrow)
    assert "--entrypoint" in vdock, vdock
    assert vdock[vdock.index("--entrypoint") + 1] == "vllm", vdock  # entrypoint = the binary
    vimg = vdock.index(ENGINE_IMAGE["vllm"])
    vpost = vdock[vimg + 1:]                                        # args AFTER the image
    assert vpost[0] == "serve", vpost                              # 'serve <path> ...'
    assert "vllm" not in vpost, vpost                              # NOT a doubled 'vllm serve'
    assert vpost.count("serve") == 1, vpost                        # no stray dup positional
    assert vpost[1] == vrow["model_path"], vpost                   # model path is the positional
    # volume mounted read-only at the parent of model_path (never a 9P host bind)
    assert f"{vrow['volume']}:{os.path.dirname(vrow['model_path'])}:ro" in vdock, vdock

    sdock = docker_run_cmd(srow)
    assert sdock[sdock.index("--entrypoint") + 1] == "python3", sdock  # sglang -> python3
    simg = sdock.index(ENGINE_IMAGE["sglang"])
    spost = sdock[simg + 1:]
    assert spost[:2] == ["-m", "sglang.launch_server"], spost     # module run, not doubled
    assert "python3" not in spost and "vllm" not in spost, spost  # no doubled binary / wrong engine
    assert f"{srow['volume']}:{os.path.dirname(srow['model_path'])}:ro" in sdock, sdock
    # legacy host_path rows still bind-mount host_path -> model_path (test/minimal only)
    legacy = docker_run_cmd({"name": "t", "engine": "vllm", "model_path": "/m/x",
                             "host_path": "/tmp/x", "est_weight_gb": 1, "gpu": 0,
                             "port": 1, "args": []})
    assert "/tmp/x:/m/x" in legacy, legacy

    # unknown engine -> BackendError before any launch (pure guard)
    try:
        docker_run_cmd({"name": "x", "engine": "bogus", "model_path": "/m/x",
                        "gpu": 0, "port": 1})
        assert False, "expected BackendError for unknown engine"
    except BackendError:
        pass

    # both fast-tier rows share gpu 0 -> single-resident swap pair (mixed engines)
    fast = [r for r in REGISTRY if r["port"] == 30001]
    assert len(fast) == 2 and {r["gpu"] for r in fast} == {0}, fast
    assert {r["engine"] for r in fast} == {"vllm", "sglang"}, fast

    # every engine resolves to an image (catches a typo'd engine before any launch)
    for r in REGISTRY:
        assert r["engine"] in ENGINE_IMAGE, r["engine"]
    assert ENGINE_IMAGE.get("bogus") is None                       # unknown-engine guard is pure

    # --- GPU ISOLATION: Docker Desktop WSL2 ignores `--gpus device=N`, so a row with
    # a resolved gpu_uuid MUST emit `--gpus all` + CUDA_VISIBLE_DEVICES (NOT device=).
    # Pure: inject a FAKE uuid (no nvidia-smi call in the self-check).
    iso_row = dict(srow, gpu_uuid="GPU-deadbeef-0000-1111-2222-333344445555")
    idock = docker_run_cmd(iso_row)
    gi = idock.index("--gpus")
    assert idock[gi + 1] == "all", idock                           # expose all, pin via env
    assert "-e" in idock and f"CUDA_VISIBLE_DEVICES={iso_row['gpu_uuid']}" in idock, idock
    assert "CUDA_DEVICE_ORDER=PCI_BUS_ID" in idock, idock          # stable index<->UUID order
    assert f"device={iso_row['gpu']}" not in idock, idock          # the IGNORED form is gone

    # fallback: a row WITHOUT gpu_uuid keeps `--gpus device={gpu}` (self-check + non-WSL)
    fdock = docker_run_cmd(srow)
    assert f"device={srow['gpu']}" in fdock, fdock
    assert "CUDA_VISIBLE_DEVICES" not in " ".join(fdock), fdock    # no env-isolation injected

    # --- IN-FLIGHT DRAINING (ADR-0006): a swap must WAIT for live leases on the
    # old resident before stopping it, then proceed; a drain timeout severs.
    be5 = MockBackend()
    mux5 = Multiplexer(reg, be5, ram_probe=lambda: 100.0, drain_timeout=5.0)
    lease_a = mux5.lease("a")
    lease_a.__enter__()                          # a live request on "a"
    assert mux5.inflight("a") == 1

    swap_done = threading.Event()

    def _swap_to_b():
        mux5.ensure_loaded("b")
        swap_done.set()

    swapper = threading.Thread(target=_swap_to_b, daemon=True)
    swapper.start()
    # The swap must NOT complete while the lease is held: "a" stays resident.
    assert not swap_done.wait(timeout=0.3)
    assert be5.current == "a" and be5.stops == 0, (be5.current, be5.stops)
    # Releasing the lease drains the swap: "b" becomes resident promptly.
    lease_a.__exit__(None, None, None)
    assert swap_done.wait(timeout=5.0), "swap did not proceed after drain"
    swapper.join(timeout=5.0)
    assert be5.current == "b" and be5.stops == 1 and mux5.resident == "b"
    assert mux5.inflight("a") == 0

    # A NEW request for the old model during the drain queues behind the swap
    # (it must not take a fresh lease on the dying resident and starve the swap).
    be6 = MockBackend()
    mux6 = Multiplexer(reg, be6, ram_probe=lambda: 100.0, drain_timeout=5.0)
    l_a = mux6.lease("a")
    l_a.__enter__()
    done_b = threading.Event()
    threading.Thread(target=lambda: (mux6.ensure_loaded("b"), done_b.set()),
                     daemon=True).start()
    # Give the swapper time to enter the drain wait, then race an "a" request.
    time.sleep(0.2)
    a_result = {}

    def _late_a():
        with mux6.lease("a") as base:
            a_result["base"] = base
    late = threading.Thread(target=_late_a, daemon=True)
    late.start()
    assert not done_b.wait(timeout=0.3)          # still draining (lease held)
    l_a.__exit__(None, None, None)               # drain completes
    assert done_b.wait(timeout=5.0)
    late.join(timeout=10.0)
    # The late "a" request was queued, then served after a swap BACK to "a".
    assert a_result.get("base") == "http://mock/a", a_result
    assert be6.current == "a" and mux6.resident == "a"

    # DRAIN TIMEOUT: a lease that never releases must not block a swap forever.
    be7 = MockBackend()
    mux7 = Multiplexer(reg, be7, ram_probe=lambda: 100.0, drain_timeout=0.2)
    hung = mux7.lease("a")
    hung.__enter__()                             # never released ("hung" client)
    t0 = time.monotonic()
    mux7.ensure_loaded("b")                      # severs the laggard after 0.2s
    assert time.monotonic() - t0 >= 0.15, "swap did not wait for the drain window"
    assert be7.current == "b" and mux7.resident == "b"
    hung.__exit__(None, None, None)              # laggard release stays harmless
    assert mux7.inflight("a") == 0

    print("self-check OK")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m anvil_serving.multiplexer",
        description="On-demand OpenAI-compatible model multiplexer (single-resident, "
                    "RAM-guarded swap).")
    ap.add_argument("--registry", default=None, help="JSON registry table to override the default")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind host (default 127.0.0.1 — loopback; this endpoint "
                         "is UNAUTHENTICATED, so a non-loopback bind is warned)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--ram-cap-gb", type=float, default=None,
                    help="pin the RAM budget below the probe (e.g. model the ~50%% WSL cap)")
    ap.add_argument("--drain-timeout", type=float,
                    default=Multiplexer.DEFAULT_DRAIN_TIMEOUT,
                    help="seconds a swap waits for in-flight requests on the old "
                         "model to finish before severing them (default %(default)s; "
                         "0 = swap immediately, the pre-ADR-0006 behaviour)")
    ap.add_argument("--self-check", action="store_true",
                    help="run the mock asserts and exit (no server, no GPU)")
    a = ap.parse_args(argv)
    if a.self_check:
        _self_check()
        return 0
    registry = load_registry(a.registry)
    mux = Multiplexer(registry, SubprocessBackend(), ram_cap_gb=a.ram_cap_gb,
                      drain_timeout=a.drain_timeout)
    serve(mux, a.host, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
