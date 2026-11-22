"""One supervised GPU owner: isolated image children or one exclusive video child.

This internal Comfy-compatible endpoint has no public authentication surface. Keep
its publication loopback-only behind the existing authenticated media gateway.
The outer memory supervisor owns the lifetime; children inherit its exact lease.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import nullcontext
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from .comfyui import ComfyUIClient, MAX_METADATA_BYTES, MAX_UPLOAD_BYTES
from .errors import MediaError, MediaSubmissionRefused
from .gpu_budget import ENV as BUDGET_ENV, FEATURE as BUDGET_FEATURE
from .gpu_telemetry import DEADLINE_SECONDS, GpuTelemetryContained, GpuTelemetryDeferred, NvmlSampler
from .gpu_telemetry import valid_options as valid_telemetry_options
from .memory_guard import load_policy, signal_reason, _read_signal
from .worker_runtime import require_admission, runtime_status, WorkerRuntimeRefused

FEATURE = "anvil.media.worker-pool.v1"
HEADER = "X-Anvil-Execution-Lease"
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}")
TERMINAL = {"completed", "failed", "canceled"}
NATIVE_RETENTION_SECONDS = 86400


def _json(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def _check(condition, message, status=409):
    if not condition:
        raise MediaError("worker_pool_refused", message, status=status)


def load_config(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    _check(isinstance(value, dict) and set(value) - {"gpuTelemetry"} == {
        "schema", "root", "command", "imageBudgetMiB", "contextReserveMiB", "deviceReserveMiB", "workflows",
        "hostPolicy", "hostSignal", "imageCommitReserveGiB"
    } and value["schema"] == FEATURE, "invalid worker pool configuration")
    _check("gpuTelemetry" not in value or valid_telemetry_options(value["gpuTelemetry"]),
           "invalid GPU telemetry configuration")
    _check(isinstance(value["root"], str) and Path(value["root"]).is_absolute(), "pool root must be absolute")
    _check(all(isinstance(value[k], str) and Path(value[k]).is_absolute() for k in ("hostPolicy", "hostSignal")),
           "host guard paths must be absolute")
    command = value["command"]
    _check(isinstance(command, list) and 2 <= len(command) <= 100
           and all(isinstance(s, str) and s and len(s) <= 4096 for s in command)
           and Path(command[0]).is_absolute(), "invalid child command")
    _check(not set(command) & {"--listen", "--port", "--input-directory", "--output-directory", "--user-directory",
                             "--temp-directory", "--disable-dynamic-vram", "--disable-cuda-malloc", "--lowvram",
                             "--novram", "--highvram", "--gpu-only", "--disable-async-offload"},
           "pool owns child isolation and memory flags")
    for key, low, high in (("imageBudgetMiB", 256, 32768), ("contextReserveMiB", 1024, 8192),
                           ("deviceReserveMiB", 1024, 8192), ("imageCommitReserveGiB", 1, 128)):
        _check(type(value[key]) is int and low <= value[key] <= high, "invalid GPU memory budget")
    workflows = value["workflows"]
    _check(isinstance(workflows, list) and 1 <= len(workflows) <= 128, "invalid qualified workflow set")
    keys = set()
    for row in workflows:
        _check(isinstance(row, dict) and set(row) == {"id", "version", "profile", "mode"}
               and all(isinstance(row[k], str) and len(row[k]) <= 128 for k in row)
               and row["mode"] in {"images", "exclusive"}, "invalid qualified workflow entry")
        key = (row["id"], row["version"], row["profile"])
        _check(key not in keys, "duplicate qualified workflow entry")
        keys.add(key)
    return value


def gpu_memory():
    """Physical device headroom, independent of the child's clamped Torch view."""
    result = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
                            capture_output=True, timeout=4, check=True)
    rows = result.stdout.decode("ascii").strip().splitlines()
    _check(len(rows) == 1, "pool requires exactly one visible GPU", 503)
    parts = rows[0].split(",")
    _check(len(parts) == 2 and all(p.strip().isdigit() for p in parts), "invalid GPU telemetry", 503)
    total, free = map(int, parts)
    _check(0 <= free <= total, "invalid GPU telemetry", 503)
    return total, free


def child_exec(parent, command):
    """Exec in the supervisor's process group; kill the child if the pool dies."""
    _check(sys.platform == "linux" and command, "worker children require Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    _check(libc.prctl(1, signal.SIGKILL, 0, 0, 0) == 0 and os.getppid() == parent,
           "child parent-death containment failed")
    os.execvpe(command[0], command, os.environ)


def _container_memory():
    """Fixed kernel counters, never caller-selected filesystem paths."""
    root = Path("/sys/fs/cgroup")
    try:
        values = {}
        for label, name in (("currentBytes", "memory.current"), ("peakBytes", "memory.peak"),
                            ("limitBytes", "memory.max"), ("swapLimitBytes", "memory.swap.max")):
            raw = (root / name).read_text()[:256].strip()
            values[label] = int(raw) if raw.isdecimal() else None
        events = dict(line.split() for line in (root / "memory.events").read_text()[:2048].splitlines())
        values.update(oom=int(events["oom"]), oomKill=int(events["oom_kill"]))
        return values
    except (OSError, ValueError, KeyError):
        return None


class WorkerPool:
    def __init__(self, config):
        self.config = config
        options = config.get("gpuTelemetry")
        self._nvml = NvmlSampler(options["deviceUuid"], reserve_mib=config["deviceReserveMiB"]) if options is not None else None
        self.host_policy = load_policy(config["hostPolicy"])
        self.host_policy["lease_path"] = config["hostSignal"]
        self.root = Path(config["root"]).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # shortcut: one pool owns this directory; multiple pools need separate GPU owners.
        import fcntl
        self._owner = (self.root / "owner.lock").open("a+b")
        fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _check(ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0,
               "orphan-child containment failed")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "prompts.sqlite3", check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS prompts (alias TEXT PRIMARY KEY,client TEXT UNIQUE,sequence INTEGER,"
                        "workflow TEXT,version TEXT,profile TEXT,mode TEXT,phase TEXT,lease TEXT,expires REAL,"
                        "incarnation TEXT,native TEXT,digest TEXT,history TEXT,run_id TEXT,retained_until REAL NOT NULL DEFAULT 0)")
        if "retained_until" not in {r[1] for r in self.db.execute("PRAGMA table_info(prompts)")}:
            self.db.execute("ALTER TABLE prompts ADD COLUMN retained_until REAL NOT NULL DEFAULT 0")
            self.db.execute("UPDATE prompts SET retained_until=? WHERE phase IN ('completed','failed','canceled')",
                            (time.time() + NATIVE_RETENTION_SECONDS,))
        self.run_id = runtime_status()["runId"]
        _check(bool(self.run_id), "supervisor runtime lease is required", 503)
        self.children = []
        self.mode = ""
        self.generation_used = False
        self.switching = False
        self._mode_generation = 0
        self.fault = ""
        self.metadata = {}
        self.last_prune = 0
        self.stop = threading.Event()
        self.workflows = {(w["id"], w["version"], w["profile"]): w["mode"] for w in config["workflows"]}
        # Same-run restart is ambiguous. A fresh supervisor run is a new contained process tree.
        _check(not self.db.execute("SELECT 1 FROM prompts WHERE run_id=? AND phase NOT IN ('completed','failed','canceled')",
                                   (self.run_id,)).fetchone(), "same-run pool restart requires recovery")
        for row in self.db.execute("SELECT * FROM prompts WHERE phase NOT IN ('completed','failed','canceled')").fetchall():
            self._finish(row, "canceled" if not row["digest"] else "failed")
        self._prune(force=True)

    def _prune(self, *, force=False):
        if not force and time.monotonic() - self.last_prune < 60:
            return
        with self.lock:
            self.db.execute("DELETE FROM prompts WHERE phase IN ('completed','failed','canceled') AND retained_until<=?",
                            (time.time(),))
            children = self.root / "children"
            if children.exists():
                active = {c["id"] for c in self.children}
                for directory in children.iterdir():
                    if (directory.name in active or not TOKEN.fullmatch(directory.name) or directory.is_symlink()
                            or not directory.is_dir() or directory.stat().st_mtime > time.time() - NATIVE_RETENTION_SECONDS
                            or self.db.execute("SELECT 1 FROM prompts WHERE incarnation=? LIMIT 1", (directory.name,)).fetchone()):
                        continue
                    _check(directory.resolve().is_relative_to(children.resolve()) and directory.resolve() != children.resolve(),
                           "native cleanup path escaped custody root")
                    shutil.rmtree(directory)
            self.last_prune = time.monotonic()

    def _rows(self):
        return self.db.execute("SELECT alias,client,sequence,workflow,version,profile,mode,phase,lease,expires,"
                               "incarnation,native,digest,run_id FROM prompts ORDER BY sequence, rowid").fetchall()

    def _host_ready(self, active_images):
        try:
            if signal_reason(self.host_policy):
                return False
            value = _read_signal(self.host_policy)
            free, sampled = value.get("commit_free_gib"), value.get("sampled_at")
            return (value.get("allow") is True and value.get("policy_sha256") == self.host_policy["sha256"]
                and type(sampled) in (int, float) and math.isfinite(sampled)
                and abs(time.time() - sampled) <= self.host_policy["max_age_seconds"]
                and type(free) in (int, float) and math.isfinite(free)
                and free >= self.host_policy["min_commit_gib"] + (active_images + 1) * self.config["imageCommitReserveGiB"])
        except (OSError, ValueError, TypeError):
            return False

    def _active(self):
        return [r for r in self._rows() if r["phase"] not in TERMINAL | {"waiting"}]

    def _child(self, row):
        child = next((c for c in self.children if c["id"] == row["incarnation"]), None)
        _check(child is not None and child["process"].poll() is None, "assigned child is unavailable", 503)
        return child

    def _finish(self, row, phase, history=None):
        if history is None and phase != "canceled":
            history = {"prompt": [row["sequence"], row["alias"], {}, {"client_id": row["client"]}],
                       "outputs": {}, "status": {"completed": False, "status_str": "error"}}
        self.db.execute("UPDATE prompts SET phase=?,history=?,retained_until=? WHERE alias=?",
                        (phase, _json(history).decode() if history else None,
                         time.time() + NATIVE_RETENTION_SECONDS, row["alias"]))

    def _rewrite_history(self, row, record):
        value = copy.deepcopy(record)
        value["prompt"] = [row["sequence"], row["alias"], {}, {"client_id": row["client"]}]
        for output in value.get("outputs", {}).values():
            for key in ("images", "gifs", "videos"):
                for item in output.get(key, []):
                    subfolder = item.get("subfolder", "")
                    _check(isinstance(subfolder, str), "invalid native output folder", 502)
                    item["subfolder"] = row["alias"] + "/" + subfolder
        # Native debug messages can include private prompts/tracebacks. They are not needed for custody.
        value["status"] = {k: value.get("status", {}).get(k) for k in ("completed", "status_str")}
        _check(len(_json(value)) <= MAX_METADATA_BYTES, "native history exceeds metadata limit", 502)
        return value

    def refresh(self):
        with self.lock:
            for row in self._rows():
                if row["phase"] in {"waiting", "prepared"} and row["expires"] < time.monotonic():
                    self._finish(row, "canceled")  # Definitely no /prompt was sent.
                elif row["phase"] in {"submitting", "active"}:
                    child = self._child(row)
                    native = row["native"]
                    if not native:
                        native = child["client"].find_prompt(row["client"].removeprefix("anvil-"))
                        if not native:
                            continue  # Unknown submission keeps its slot; never replay it.
                        self.db.execute("UPDATE prompts SET native=?,phase='active' WHERE alias=?", (native, row["alias"]))
                        self.generation_used = True
                    record = child["client"].request_json("GET", "/history/" + native).get(native)
                    if record:
                        status = record.get("status", {})
                        if status.get("completed") is True or status.get("status_str") in {"error", "failed"}:
                            self._finish(row, "completed" if status.get("completed") is True else "failed",
                                         self._rewrite_history(row, record))

    def _queues_idle(self):
        for child in self.children:
            queue = child["client"].queue()
            if queue["running"] or queue["pending"]:
                return False
        return True

    def prepare(self, value):
        try:
            return self._prepare(value)
        except GpuTelemetryDeferred:
            return {"ready": False}

    def _prepare(self, value):
        _check(isinstance(value, dict) and set(value) == {"jobId", "sequence", "workflow", "version", "profile"},
               "invalid preparation request", 400)
        job = value["jobId"]
        _check(isinstance(job, str) and TOKEN.fullmatch(job) and type(value["sequence"]) is int
               and 0 < value["sequence"] < 2**63, "invalid preparation identity", 400)
        key = (value["workflow"], value["version"], value["profile"])
        _check(all(isinstance(k, str) for k in key) and key in self.workflows, "workflow profile is not qualified")
        sample = self._sample_for_request(skip_switching=True)
        with self.lock:
            _check(not self.fault, "worker pool is quarantined", 503)
            row = self.db.execute("SELECT * FROM prompts WHERE client=?", ("anvil-" + job,)).fetchone()
            if row:
                if row["phase"] in {"waiting", "prepared"} and row["expires"] < time.monotonic():
                    self._finish(row, "canceled")
                    row = self.db.execute("SELECT * FROM prompts WHERE client=?", ("anvil-" + job,)).fetchone()
                _check(tuple(row[k] for k in ("workflow", "version", "profile", "sequence")) == (*key, value["sequence"]),
                       "preparation identity changed")
                if row["phase"] == "canceled" and not row["digest"]:
                    self.db.execute("UPDATE prompts SET phase='waiting',expires=?,lease=?,incarnation='',run_id=? WHERE alias=?",
                                    (time.monotonic() + 60, secrets.token_urlsafe(32), self.run_id, row["alias"]))
                elif row["phase"] != "waiting":
                    _check(row["phase"] == "prepared", "job has already been submitted")
                    if self._nvml is not None:
                        if sample is None or self.switching:
                            return {"ready": False}
                    self._verify_gpu_reserve(sample, default_sample=False)
                    self.db.execute("UPDATE prompts SET expires=? WHERE alias=?", (time.monotonic() + 60, row["alias"]))
                    if self._nvml is not None:
                        self._verify_gpu_reserve(sample, default_sample=False)
                    return {"ready": True, "lease": row["lease"]}
            else:
                _check(len(self._rows()) < 4096, "worker pool ledger is full", 503)
                alias, lease = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
                self.db.execute("INSERT INTO prompts(alias,client,sequence,workflow,version,profile,mode,phase,lease,expires,"
                                "incarnation,native,digest,history,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (alias, "anvil-" + job, value["sequence"], *key, self.workflows[key], "waiting", lease,
                                 time.monotonic() + 60, "", "", "", None, self.run_id))
            self.db.execute("UPDATE prompts SET expires=? WHERE client=?", (time.monotonic() + 60, "anvil-" + job))
            row = self.db.execute("SELECT * FROM prompts WHERE client=?", ("anvil-" + job,)).fetchone()
            waiting = [r for r in self._rows() if r["phase"] == "waiting"]
            if self.switching or self.mode != row["mode"] or waiting[0]["alias"] != row["alias"]:
                return {"ready": False}
            active = self._active()
            if any(r["phase"] != "active" for r in active):
                return {"ready": False}  # Older uploads/submissions must reach native enqueue first.
            if self.mode == "images" and not active and self.generation_used and not self._host_ready(1):
                return {"ready": False, "reason": "idle_worker_reclaim"}
            if self.mode == "images" and not self._host_ready(len(active)):
                return {"ready": False, "reason": "host_commit_headroom"}
            occupied = {r["incarnation"] for r in active}
            for child in self.children:
                if child["id"] in occupied or not child.get("ready"):
                    continue
                stats = child["client"].request_json("GET", "/system_stats")
                _check(stats["system"]["anvil_runtime"]["runId"] == self.run_id, "child runtime identity changed", 503)
                _check(child["process"].poll() is None, "child exited", 503)
                if self.mode == "images":
                    receipt = stats["system"].get("anvil_cuda_budget")
                    _check(isinstance(receipt, dict) and receipt.get("limitBytes") == self.config["imageBudgetMiB"] * 1048576
                           and receipt.get("allocator") == "native" and receipt.get("dynamicVram") is False,
                           "child allocator budget changed", 503)
                queue = child["client"].queue()
                if queue["running"] or queue["pending"]:
                    self.fault = "unowned_native_prompt"
                    return {"ready": False}
                _check(runtime_status()["acceptingNewWork"], "worker lifetime cannot reserve another job")
                if self._nvml is not None and sample is None:
                    return {"ready": False}
                self._verify_gpu_reserve(sample)
                self.db.execute("UPDATE prompts SET phase='prepared',incarnation=?,expires=? WHERE alias=?",
                                (child["id"], time.monotonic() + 60, row["alias"]))
                if self._nvml is not None:
                    try:
                        self._verify_gpu_reserve(sample)
                    except MediaError:
                        # No lease has escaped the pool lock. Restore the
                        # same FIFO row before deferring or refusing.
                        restored = self.db.execute("UPDATE prompts SET phase='waiting',incarnation='' "
                                                   "WHERE alias=? AND phase='prepared' AND digest=''", (row["alias"],))
                        _check(restored.rowcount == 1, "preparation rollback is unproven", 503)
                        raise
                return {"ready": True, "lease": row["lease"]}
            return {"ready": False}

    def _lease(self, token):
        _check(isinstance(token, str) and TOKEN.fullmatch(token), "execution lease is required", 400)
        row = self.db.execute("SELECT * FROM prompts WHERE lease=? AND run_id=?", (token, self.run_id)).fetchone()
        _check(row is not None and row["phase"] == "prepared" and row["expires"] >= time.monotonic(),
               "execution lease is unavailable")
        return row

    def upload(self, token, body, content_type):
        with self.lock:
            row = self._lease(token)
            self.db.execute("UPDATE prompts SET phase='uploading' WHERE alias=?", (row["alias"],))
            child = self._child(row)
        try:
            return child["client"].request_bytes("POST", "/upload/image", body=body,
                        headers={"Content-Type": content_type}, max_bytes=65536)
        finally:
            with self.lock:
                self.db.execute("UPDATE prompts SET phase='prepared',expires=? WHERE alias=? AND phase='uploading'",
                                (time.monotonic() + 60, row["alias"]))

    def submit(self, token, value):
        digest = hashlib.sha256(_json(value)).hexdigest()
        with self.lock:
            prior = self.db.execute("SELECT * FROM prompts WHERE lease=?", (token,)).fetchone()
            if prior and prior["digest"]:
                _check(prior["digest"] == digest and prior["native"], "submission outcome is uncertain or changed")
                return {"prompt_id": prior["alias"], "number": prior["sequence"], "node_errors": {}}
            def payload_matches(row):
                return (isinstance(value, dict) and value.get("client_id") == row["client"]
                        and isinstance(value.get("prompt"), dict))

            # A prepared lease can be retired by the drive fault before its
            # request arrives. Only an exact, formerly assigned, digest-free
            # lease from this run can prove no native enqueue.
            if (self._nvml is not None and self.fault and prior and prior["run_id"] == self.run_id
                    and prior["phase"] in {"failed", "canceled"} and prior["incarnation"]
                    and payload_matches(prior)):
                extra = {**value.get("extra_data", {}), "client_id": prior["client"]}
                require_admission(extra, busy=False)
                raise WorkerRuntimeRefused("worker_budget_unknown", extra["anvil_runtime"]["runId"], prior["client"])

            row = self._lease(token)
            _check(payload_matches(row), "submission does not match its lease", 400)
            extra = {**value.get("extra_data", {}), "client_id": row["client"]}
            require_admission(extra, busy=False)

            def refusal():
                return WorkerRuntimeRefused("worker_budget_unknown", extra["anvil_runtime"]["runId"], row["client"])

            def dispatch_gate():
                _check(not self.fault and not self.switching, "worker pool is quarantined or switching", 503)
                try:
                    with self._nvml.validate(None):
                        return
                except GpuTelemetryDeferred:
                    # A concurrent *healthy* publication may replace the
                    # snapshot during its liveness check; try once more,
                    # without issuing a new telemetry sample.
                    with self._nvml.validate(None):
                        return

            if self._nvml is None:
                child = self._child(row)
            else:
                try:
                    dispatch_gate()
                    child = self._child(row)
                except MediaError:
                    raise refusal() from None

            self.db.execute("UPDATE prompts SET phase='submitting',digest=? WHERE alias=?", (digest, row["alias"]))
            if self._nvml is not None:
                try:
                    # The fsync above may take time. Recheck before the native
                    # POST without blocking the provider's own time budget.
                    child = self._child(row)
                    dispatch_gate()
                except MediaError:
                    restored = self.db.execute("UPDATE prompts SET phase='prepared',digest='' "
                                               "WHERE alias=? AND phase='submitting' AND digest=?",
                                               (row["alias"], digest))
                    # If rollback failed or the process crashed during it,
                    # custody is still unknown; never send a refusal receipt.
                    _check(restored.rowcount == 1, "submission rollback is unproven", 503)
                    raise refusal() from None
            # Commit intent before I/O. Even a lost response can only be recovered on this child.
            try:
                response = child["client"].request_json("POST", "/prompt", value, max_bytes=65536)
            except MediaSubmissionRefused:
                self._finish(row, "failed")  # Exact native no-enqueue proof; no unknown reservation remains.
                return {"prompt_id": row["alias"], "number": row["sequence"], "node_errors": {}}
            except Exception:
                raise  # Keep the assignment quarantined until native history or child death proves its outcome.
            native = response.get("prompt_id") if isinstance(response, dict) else None
            _check(isinstance(native, str) and TOKEN.fullmatch(native), "invalid native prompt identity", 502)
            self.db.execute("UPDATE prompts SET native=?,phase='active' WHERE alias=?", (native, row["alias"]))
            self.generation_used = True
            return {"prompt_id": row["alias"], "number": row["sequence"], "node_errors": {}}

    def release(self, value):
        _check(isinstance(value, dict) and set(value) == {"jobId"}
               and isinstance(value["jobId"], str) and TOKEN.fullmatch(value["jobId"]), "invalid preparation release", 400)
        with self.lock:
            row = self.db.execute("SELECT * FROM prompts WHERE client=?", ("anvil-" + value["jobId"],)).fetchone()
            if row and row["phase"] in {"waiting", "prepared"}:
                self._finish(row, "canceled")
        return {}

    def queue(self):
        with self.lock:
            value = {"queue_running": [], "queue_pending": []}
            for row in self._rows():
                if row["phase"] not in TERMINAL:
                    value["queue_running" if row["phase"] in {"active", "submitting"} else "queue_pending"].append(
                        [row["sequence"], row["alias"], {}, {"client_id": row["client"]} if row["native"] else {}])
            return value

    def history(self, alias=""):
        with self.lock:
            if alias:
                row = self.db.execute("SELECT history FROM prompts WHERE alias=?", (alias,)).fetchone()
                return {alias: json.loads(row[0])} if row and row[0] else {}
            return {r["alias"]: {"prompt": [r["sequence"], r["alias"], {}, {"client_id": r["client"]}],
                                 "status": {"completed": r["phase"] == "completed", "status_str": r["phase"]}}
                    for r in self.db.execute("SELECT alias,sequence,client,phase FROM prompts WHERE history IS NOT NULL")}

    def delete(self, aliases):
        _check(isinstance(aliases, list) and len(aliases) <= 128
               and all(isinstance(a, str) and TOKEN.fullmatch(a) for a in aliases), "invalid queue deletion", 400)
        with self.lock:
            for alias in aliases:
                row = self.db.execute("SELECT * FROM prompts WHERE alias=?", (alias,)).fetchone()
                if row and row["phase"] in {"waiting", "prepared"}:
                    self._finish(row, "canceled")
                # Native prompts are already executing or uncertain; never broadcast an interrupt.
        return {}

    def view(self, query):
        values = parse_qs(query, strict_parsing=True, keep_blank_values=True)
        _check(set(values) == {"filename", "subfolder", "type"} and all(len(v) == 1 for v in values.values()),
               "invalid output coordinates", 400)
        filename, folder, kind = (values[k][0] for k in ("filename", "subfolder", "type"))
        _check(filename and len(filename) <= 512 and "/" not in filename and "\\" not in filename
               and kind in {"output", "temp"}, "invalid output coordinates", 400)
        _check("/" in folder and "\\" not in folder, "invalid output namespace", 400)
        alias, relative = folder.split("/", 1)
        _check(TOKEN.fullmatch(alias), "invalid output namespace", 400)
        # Only files vouched for by a terminal native history can cross the custody boundary.
        allowed = False
        with self.lock:
            row = self.db.execute("SELECT incarnation FROM prompts WHERE alias=?", (alias,)).fetchone()
            _check(row is not None, "output namespace is unavailable", 404)
            incarnation = row[0]
            for record in self.history(alias).values():
                for output in record.get("outputs", {}).values():
                    for key in ("images", "gifs", "videos"):
                        allowed |= any(i.get("filename") == filename and i.get("subfolder") == folder
                                       and i.get("type", "output") == kind for i in output.get(key, []))
        _check(allowed, "output is not in retained native history", 404)
        base = (self.root / "children" / incarnation / kind).resolve()
        path = (base / relative / filename).resolve()
        _check(base.is_relative_to(self.root) and path.is_relative_to(base) and path.is_file() and path.stat().st_size <= 1024**3,
               "output file is unavailable", 404)
        return path

    def diagnostics(self):
        try:
            sample = self._sample_for_request(skip_switching=True)
        except MediaError:
            sample = None  # Report diagnostics without inventing physical headroom.
        with self.lock:
            workers = []
            for child in self.children:
                item = {"id": child["id"], "mode": self.mode, "alive": child["process"].poll() is None,
                        "ready": child.get("ready", False)}
                if item["ready"] and item["alive"]:
                    q = child["client"].queue()
                    stats = child["client"].request_json("GET", "/system_stats")
                    item.update(running=q["running"], pending=q["pending"],
                                runtime=stats["system"]["anvil_runtime"], cudaBudget=stats["system"].get("anvil_cuda_budget"))
                workers.append(item)
            if self._nvml is None:
                total, free = self._sample_gpu()
            else:
                try:
                    total, free = self._fresh_sample(sample) if sample is not None and not self.fault else (None, None)
                except MediaError:
                    total, free = None, None
            return {"schema": FEATURE, "mode": self.mode, "switching": self.switching, "fault": self.fault,
                    "workers": workers, "gpuTotalMiB": total, "gpuFreeMiB": free,
                    "runtime": runtime_status(), "activeAssignments": len(self._active()),
                    "containerMemory": _container_memory()}

    def metadata_get(self, path, token):
        with self.lock:
            if token:
                row = self._lease(token)
                return self._child(row)["client"].request_json("GET", path)
            if path == "/system_stats":
                _check(not self.fault, "worker pool is quarantined", 503)
                _check("/system_stats" in self.metadata, "worker is starting", 503)
                result = copy.deepcopy(self.metadata[path])
                result["system"]["anvil_runtime"] = runtime_status(busy=bool(self.fault) or self.switching
                    or len(self._active()) >= len(self.children))
                result["system"]["anvil_pool"] = {"schema": FEATURE, "mode": self.mode, "switching": self.switching,
                                                   "quarantined": bool(self.fault)}
                result["system"].pop("anvil_cuda_budget", None)
                return result
            if path == "/features":
                return {**self.metadata.get(path, {}), FEATURE: True}
            if path not in self.metadata:
                _check(len(self.metadata) < 256, "metadata cache is full", 503)
                child = next((c for c in self.children if c.get("ready")), None)
                _check(child is not None, "worker is changing mode", 503)
                self.metadata[path] = child["client"].request_json("GET", path)
            return self.metadata[path]

    def _sample_gpu(self):
        return self._nvml.sample() if self._nvml is not None else gpu_memory()

    def _sample_for_request(self, *, skip_switching=False):
        if self._nvml is None:
            return None  # Leave the default provider's existing call sites unchanged.
        with self.lock:
            if skip_switching and (self.switching or self.fault):
                return None
            mode_generation = self._mode_generation
        started = time.monotonic()
        try:
            generation, reading = self._nvml.sample_with_revision()
        except GpuTelemetryContained:
            with self.lock:
                if skip_switching and not self.fault and self._mode_generation != mode_generation:
                    return None  # A planned transition, including one already completed.
            raise
        return started, generation, reading, mode_generation

    def _fresh_sample(self, sample):
        started, generation, reading, mode_generation = sample
        _check(time.monotonic() - started < DEADLINE_SECONDS and generation == self._nvml.epoch
               and mode_generation == self._mode_generation, "GPU telemetry sample is stale", 503)
        return reading

    def _verify_gpu_reserve(self, sample, *, default_sample=True):
        if sample is None:
            if default_sample:
                _check(self._sample_gpu()[1] >= self.config["deviceReserveMiB"], "GPU reserve is unavailable", 503)
        else:
            started, generation, reading, mode_generation = sample
            if mode_generation != self._mode_generation:
                raise GpuTelemetryDeferred()
            with self._nvml.validate(generation):
                # validate owns the epoch decision. A second separate epoch
                # observation can turn a healthy concurrent read into a 503.
                _check(time.monotonic() - started < DEADLINE_SECONDS, "GPU telemetry sample is stale", 503)
                _check(reading[1] >= self.config["deviceReserveMiB"], "GPU reserve is unavailable", 503)

    def _stop_children(self):
        # Synchronize the helper with orphan containment, never exempt an
        # unreaped PID from the pool's existing native-descendant sweep.
        with self._nvml.contain() if self._nvml is not None else nullcontext():
            self._stop_render_children()

    def _stop_render_children(self):
        for child in self.children:
            if child["process"].poll() is None:
                child["process"].terminate()
        for child in self.children:
            try:
                child["process"].wait(timeout=10)
            except subprocess.TimeoutExpired:
                child["process"].kill()
                child["process"].wait(timeout=5)
        # Subreaping keeps orphaned native helpers owned until positively terminated.
        deadline = time.monotonic() + 5
        pids = []
        while time.monotonic() < deadline:
            pids = []
            for directory in Path("/proc").iterdir():
                if not directory.name.isdecimal():
                    continue
                try:
                    stat = (directory / "stat").read_text().rsplit(")", 1)[1].split()
                    if int(stat[1]) == os.getpid():
                        pids.append(int(directory.name))
                except (OSError, ValueError, IndexError):
                    continue
            if not pids:
                break
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, os.WNOHANG)
                except (ProcessLookupError, ChildProcessError):
                    pass
            time.sleep(.05)
        _check(not pids, "native descendant termination is unproven", 503)
        self.children = []

    def _start_children(self, mode):
        count = 2 if mode == "images" else 1
        if self._nvml is not None:
            # Startup may block in process creation; no render child or active
            # assignment exists here. Four seconds bounds provider reads only.
            self._nvml.start()
        total, free = self._sample_gpu()
        if self._nvml is not None:
            _check(free >= self.config["deviceReserveMiB"], "physical GPU reserve was breached", 503)
        need = count * (self.config["imageBudgetMiB"] + self.config["contextReserveMiB"]) + self.config["deviceReserveMiB"]
        _check(mode != "images" or total >= need and free >= need, "device cannot reserve two isolated image workers", 503)
        children = []
        self.children = children
        for index in range(count):
            port = 18188 + index
            with socket.socket() as check:
                check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                check.bind(("127.0.0.1", port))  # Refuse a pre-existing unowned child.
            incarnation = secrets.token_hex(16)
            directory = self.root / "children" / incarnation
            for name in ("input", "output", "temp", "user"):
                (directory / name).mkdir(parents=True)
            command = [*self.config["command"], "--listen", "127.0.0.1", "--port", str(port)]
            for name in ("input", "output", "temp", "user"):
                command += ["--" + name + "-directory", str(directory / name)]
            environment = dict(os.environ)
            environment.pop(BUDGET_ENV, None)
            if mode == "images":
                environment[BUDGET_ENV] = str(self.config["imageBudgetMiB"])
                command += ["--disable-dynamic-vram", "--disable-cuda-malloc", "--lowvram", "--disable-async-offload"]
            process = subprocess.Popen([sys.executable, "-m", "anvil_serving.media.worker_pool", "_child", str(os.getpid()), *command],
                                       env=environment, stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            print(_json({"schema": FEATURE, "state": "child_started", "child": incarnation, "mode": mode}).decode(), flush=True)
            children.append({"id": incarnation, "process": process,
                             "client": ComfyUIClient(f"http://127.0.0.1:{port}", timeout=2), "ready": False})
        self.mode = mode
        self.generation_used = False
        deadline = time.monotonic() + 180
        while not self.stop.is_set() and time.monotonic() < deadline:
            for child in children:
                _check(child["process"].poll() is None, "child failed during startup", 503)
                if child["ready"]:
                    continue
                try:
                    stats = child["client"].request_json("GET", "/system_stats")
                    _check(stats["system"]["anvil_runtime"]["runId"] == self.run_id, "child reset the supervisor deadline", 503)
                    features = child["client"].request_json("GET", "/features")
                    _check(mode != "images" or BUDGET_FEATURE in features, "child CUDA budget feature is missing", 503)
                    child["ready"] = True
                    with self.lock:
                        self.metadata["/system_stats"] = stats
                        self.metadata["/features"] = features
                except MediaError as exc:
                    if exc.code != "backend_unavailable":
                        raise
            if all(c["ready"] for c in children):
                return
            self.stop.wait(.5)
        raise MediaError("worker_pool_start_timeout", "isolated children did not become ready", status=503)

    def drive(self):
        try:
            with self.lock:
                self.switching = True
                self._mode_generation += 1
            self._start_children("images")
            with self.lock:
                self.switching = False
            while not self.stop.wait(2):
                _, free = self._sample_gpu()
                _check(free >= self.config["deviceReserveMiB"], "physical GPU reserve was breached", 503)
                _check(all(c["process"].poll() is None for c in self.children), "an isolated child exited", 503)
                self._prune()
                try:
                    self.refresh()
                except MediaError as exc:
                    if exc.code == "backend_unavailable":
                        continue  # Preserve assignments; no new child or mode can replace an uncertain one.
                    raise
                with self.lock:
                    waiting = [r for r in self._rows() if r["phase"] == "waiting"]
                    mode = waiting[0]["mode"] if waiting else self.mode
                    reclaim = (bool(waiting) and self.mode == mode == "images" and self.generation_used
                               and not self._host_ready(1))
                    switch = (mode != self.mode or reclaim) and not self._active() and self._queues_idle()
                    self.switching = switch
                    if switch:
                        self._mode_generation += 1
                if switch:
                    self._stop_children()
                    self._start_children(mode)
                    with self.lock:
                        self.switching = False
        except Exception as exc:
            with self.lock:
                self.fault = exc.code if isinstance(exc, MediaError) else type(exc).__name__
            # A fault terminates the contained tree; no uncertain assignment releases a new slot.
            self._stop_children()
            with self.lock:
                for row in self._rows():
                    if row["phase"] not in TERMINAL:
                        self._finish(row, "failed")
            print(_json({"schema": FEATURE, "state": "quarantined", "reason": self.fault,
                         "errorType": type(exc).__name__, "errno": getattr(exc, "errno", None),
                         "message": str(exc)[:512]}).decode(), flush=True)
        finally:
            if self._nvml is not None:
                self._nvml.close()
            self._stop_children()

class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, pool):
        self.pool = pool
        self.permits = threading.BoundedSemaphore(12)
        super().__init__(address, _Handler)

    def process_request(self, request, address):
        if not self.permits.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.permits.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.permits.release()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _reply(self, status, payload, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else _json(payload)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _request(self):
        pool = self.server.pool
        path = urlsplit(self.path)
        _check(not path.scheme and not path.netloc and not path.fragment and len(self.path) <= 2048,
               "invalid request path", 400)
        route, lease = unquote(path.path), self.headers.get(HEADER, "")
        if self.command == "GET":
            if route == "/queue":
                return self._reply(200, pool.queue())
            if route == "/history" or route.startswith("/history/"):
                alias = route.removeprefix("/history").lstrip("/")
                records = pool.history(alias)
                return self._reply(200, records)
            if route == "/anvil/workers":
                return self._reply(200, pool.diagnostics())
            if route == "/view":
                file = pool.view(path.query)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(file.stat().st_size))
                self.end_headers()
                with file.open("rb") as stream:
                    while block := stream.read(65536):
                        self.wfile.write(block)
                return
            _check(route in {"/features", "/system_stats"} or re.fullmatch(r"/(object_info|models)/[A-Za-z0-9_-]{1,128}", route),
                   "unsupported metadata route", 404)
            return self._reply(200, pool.metadata_get(route, lease))
        _check(not path.query and self.headers.get("Transfer-Encoding") is None, "unsupported request framing", 400)
        lengths = self.headers.get_all("Content-Length", [])
        _check(len(lengths) == 1 and lengths[0].isdigit(), "content length is required", 400)
        maximum = MAX_UPLOAD_BYTES + 65536 if route == "/upload/image" else MAX_METADATA_BYTES
        length = int(lengths[0])
        _check(0 < length <= maximum, "request body exceeds policy", 413)
        body = self.rfile.read(length)
        _check(len(body) == length, "request body is incomplete", 400)
        if route == "/upload/image":
            content_type = self.headers.get("Content-Type", "")
            _check(content_type.startswith("multipart/form-data; boundary=") and len(content_type) < 256,
                   "invalid upload content type", 400)
            return self._reply(200, pool.upload(lease, body, content_type))
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise MediaError("invalid_json", "invalid JSON request", status=400) from exc
        if route == "/anvil/prepare":
            return self._reply(200, pool.prepare(value))
        if route == "/anvil/release":
            return self._reply(200, pool.release(value))
        if route == "/prompt":
            return self._reply(200, pool.submit(lease, value))
        if route == "/queue":
            _check(isinstance(value, dict) and set(value) == {"delete"}, "only exact queued deletion is supported", 400)
            return self._reply(200, pool.delete(value["delete"]))
        raise MediaError("worker_pool_refused", "unsupported mutation", status=409)

    def do_GET(self):
        try:
            self._request()
        except WorkerRuntimeRefused as exc:
            self._reply(409, exc.response())
        except MediaError as exc:
            self._reply(exc.status, exc.as_dict())
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self._reply(503, {"code": "worker_pool_unavailable"})

    do_POST = do_GET


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "_child":
        child_exec(int(arguments[1]), arguments[2:])
        return 1
    parser = argparse.ArgumentParser(prog="anvil-serving media worker-pool")
    parser.add_argument("action", choices=("serve",))
    parser.add_argument("--config", required=True)
    parser.add_argument("--bind", default="127.0.0.1", choices=("127.0.0.1", "0.0.0.0"))
    parser.add_argument("--port", default=8188, type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(arguments)
    _check(sys.platform == "linux", "worker pool requires Linux containment")
    config = load_config(args.config)
    if args.dry_run or not args.confirm:
        print(_json({"dryRun": True, "action": "serve", "configValid": True, "gpuStarted": False}).decode())
        return 0
    pool = WorkerPool(config)
    server = _Server((args.bind, args.port), pool)
    driver = threading.Thread(target=pool.drive, daemon=True)
    driver.start()
    def stop(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        pool.stop.set()
        driver.join(timeout=30)
        _check(not driver.is_alive(), "worker manager termination is unproven", 503)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
