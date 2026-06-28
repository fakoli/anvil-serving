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
import argparse, io, json, os, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from . import deploy
except Exception:  # allow `python multiplexer.py` direct run
    deploy = None

# --- REGISTRY TABLE: single source of truth for /v1/models AND the loader ----
# Columns: name (served /v1 id) | model_path (dir handed to backend) |
#   est_weight_gb (mmap-off host-RAM cost -> drives the OOM guard) |
#   gpu | port (per-backend upstream) | extra (passthrough kwargs -> deploy.render)
REGISTRY = [
    {"name": "coder-35b-awq", "model_path": "/models/qwen3-coder-35b-awq",
     "est_weight_gb": 24, "gpu": 0, "port": 30000,
     "extra": {"kv_dtype": "fp8_e5m2", "context": 131072}},
    {"name": "coder-30b-fp8", "model_path": "/models/qwen3-coder-30b-fp8",
     "est_weight_gb": 34, "gpu": 0, "port": 30000,
     "extra": {"kv_dtype": "fp8_e5m2", "context": 131072}},
]


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
    """Default real backend: launch sglang (flags via deploy.render) for an entry,
    block until its /healthz is up. Idempotent stop. GPU-touching; NOT used in the
    self-check."""

    def __init__(self, render=None, startup_timeout=600):
        self.render = render or (deploy.render if deploy else None)
        self.startup_timeout = startup_timeout
        self.current = None
        self._proc = None

    def start(self, entry):
        import subprocess, tempfile
        port = entry["port"]
        if self.render is None:
            raise BackendError("no renderer available (deploy module not importable)")
        compose = self.render(entry["model_path"], gpu_index=entry["gpu"],
                              served_name=entry["name"], port=port,
                              **entry.get("extra", {}))
        f = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8")
        f.write(compose)
        f.close()
        self._compose_file = f.name
        self._proc = subprocess.Popen(
            ["docker", "compose", "-f", f.name, "up"],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}/v1"
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=5) as r:
                    if r.status == 200:
                        self.current = entry["name"]
                        return base
            except Exception:
                time.sleep(2)
        self.stop()
        raise BackendError(f"backend for {entry['name']} did not come up within "
                           f"{self.startup_timeout}s")

    def stop(self):
        import subprocess
        cf = getattr(self, "_compose_file", None)
        if cf:
            try:
                subprocess.call(["docker", "compose", "-f", cf, "down"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            except Exception:
                pass
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
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
    def __init__(self, registry, backend, ram_probe=available_ram_gb, ram_cap_gb=None):
        self.table = {r["name"]: r for r in registry}
        self.order = [r["name"] for r in registry]
        self.backend = backend          # <-- injectable backend seam
        self.ram_probe = ram_probe      # <-- injectable for deterministic OOM test
        self.cap = ram_cap_gb
        self.resident = None
        self.base_url = None
        self._lock = threading.Lock()   # serializes load/swap; forward path is outside

    def models_payload(self):
        """OpenAI /v1/models body. NEVER triggers a load."""
        return {"object": "list",
                "data": [{"id": n, "object": "model", "owned_by": "anvil"}
                         for n in self.order]}

    def ensure_loaded(self, name):
        """Make `name` resident and return its backend base_url. Load-on-demand,
        single-resident swap, OOM-guarded. Raises UnknownModel on unknown model,
        LoadError on OOM-guard refusal, BackendError on a start failure."""
        if name not in self.table:
            raise UnknownModel(name)
        with self._lock:
            # AC3: already resident -> serve immediately, NO restart, NO churn
            if self.resident == name and self.backend.current == name:
                return self.base_url
            entry = self.table[name]
            # AC2: run the OOM guard FIRST, BEFORE evicting the good model
            oom_guard(entry, self.ram_probe(), self.cap)
            # Capture the prior resident so a failed swap can be rolled back.
            prior_name = self.resident
            prior_entry = self.table.get(prior_name) if prior_name else None
            # SWAP: single-resident GPU -> stop old, start new
            if self.backend.current is not None:
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
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    if clen is not None:
        h.send_header("Content-Length", clen)  # non-streaming: pass length through
    else:
        h.close_connection = True              # streaming: close-delimited body
        h.send_header("Connection", "close")
    h.end_headers()
    while True:
        chunk = resp.read(chunk_size)
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
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n)
            try:
                model = json.loads(body)["model"]
            except Exception as e:
                self._err(400, "invalid_request", f"bad body: {e}")
                return
            # ensure_loaded (load/swap) happens here; forwarding is per-thread/outside lock
            try:
                base = mux.ensure_loaded(model)
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
            # AC1/streaming: open upstream and relay verbatim, incrementally.
            url = base + path[len("/v1"):]
            try:
                resp = _open_upstream(url, body)
            except urllib.error.HTTPError as e:
                resp = e  # relay the upstream error status + body verbatim
            except (urllib.error.URLError, OSError) as e:
                # AC2: backend down / connection refused / swapped mid-flight ->
                # clean 503 instead of dropping the request with a raw traceback.
                self._err(503, "backend_unavailable", f"backend unreachable: {e}")
                return
            try:
                relay(resp, self)
            finally:
                try:
                    resp.close()
                except Exception:
                    pass

        def log_message(self, *a):  # quiet
            pass

    return Handler


def serve(mux, host="0.0.0.0", port=8000):
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

    print("self-check OK")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m anvil_serving.multiplexer",
        description="On-demand OpenAI-compatible model multiplexer (single-resident, "
                    "RAM-guarded swap).")
    ap.add_argument("--registry", default=None, help="JSON registry table to override the default")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--ram-cap-gb", type=float, default=None,
                    help="pin the RAM budget below the probe (e.g. model the ~50%% WSL cap)")
    ap.add_argument("--self-check", action="store_true",
                    help="run the mock asserts and exit (no server, no GPU)")
    a = ap.parse_args(argv)
    if a.self_check:
        _self_check()
        return 0
    registry = load_registry(a.registry)
    mux = Multiplexer(registry, SubprocessBackend(), ram_cap_gb=a.ram_cap_gb)
    serve(mux, a.host, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
