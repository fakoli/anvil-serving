"""Pinned mini-SWE retry observations, executed inside its existing Python environment.

The adapter observes the configured HTTPX send seam and mini-SWE's Tenacity
callbacks.  It deliberately never changes client transport, retry, timeout, or
request configuration, and writes allowlisted metadata only.
"""

from __future__ import annotations

import argparse
import contextvars
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import re
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime


MINI_REVISION = "a83fcae82d2a08f0ee0c688f9d137b3566c097f8"
SOURCE_HASHES = {
    "src/minisweagent/agents/default.py": "e8ef8aa365942d739c2ec5cb0879f60f377d2dc2de8ec670aaedf3bafb45a4c2",
    "src/minisweagent/run/benchmarks/utils/common.py": "618776d688f11018827ea8eb48ab20839d2d0ec182b4bd844a08af48937df398",
    "src/minisweagent/models/litellm_model.py": "6df4483bcdc9620b2f9d7f8bb1027a7fb9729b739976cbcf1a9d18fb58c48227",
    "src/minisweagent/models/utils/retry.py": "fb9dd9d9ca0253a2b2ac86907fea4c34fe75c55af6d443781e1ab02229a1ac18",
}
EVENT_SCHEMA = "anvil-serving.swe-agent-observation/v1"
MAX_EVENTS = 2_000
MAX_BYTES = 1_048_576
SUPPORTED_DEPENDENCIES = {"litellm": "1.102.0", "openai": "2.54.0", "httpx": "0.28.1", "tenacity": "9.1.4"}
_context = contextvars.ContextVar("swe_agent_observation", default=None)


def _stamp():
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"), time.monotonic()


class EventRecorder:
    """Append-only bounded JSONL writer; a full buffer stays explicitly partial."""

    def __init__(self, path, *, max_events=MAX_EVENTS, max_bytes=MAX_BYTES):
        self.path = Path(path)
        self.max_events, self.max_bytes = max_events, max_bytes
        self.events = self.bytes = self.omitted_events = self.omitted_bytes = self.logical_requests = 0
        self.write_failed = False
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")

    def record(self, event, **fields):
        started_at, _ = _stamp()
        with self._lock:
            if self.write_failed:
                return False
            value = {"schema": EVENT_SCHEMA, "event": event, "seq": self.events + 1,
                     "utc_observed_at": started_at, **fields}
            encoded = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
            # Reserve one line for the terminal coverage record.
            if self.events >= self.max_events - 1 or self.bytes + len(encoded) > self.max_bytes - 4096:
                self.omitted_events += 1
                self.omitted_bytes += len(encoded)
                return False
            try:
                self._file.write(encoded.decode())
                self._file.flush()
            except OSError:
                self.write_failed = True
                return False
            self.events += 1
            self.bytes += len(encoded)
            return True

    def next_logical_request_id(self):
        with self._lock:
            self.logical_requests += 1
            return f"logical-{self.logical_requests:08d}"

    def close(self, *, outcome):
        if self.write_failed:
            try:
                self._file.close()
            except OSError:
                pass
            return
        value = {"schema": EVENT_SCHEMA, "event": "adapter_finished", "seq": self.events + 1,
                 "utc_observed_at": _stamp()[0], "status": "truncated" if self.omitted_events else "complete",
                 "outcome": outcome, "event_count": self.events, "byte_count": self.bytes,
                 "omitted_events": self.omitted_events, "omitted_bytes": self.omitted_bytes,
                 "limits": {"max_events": self.max_events, "max_bytes": self.max_bytes}}
        try:
            self._file.write(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
            self._file.close()
        except OSError:
            self.write_failed = True


def verify_source(root, *, runner=subprocess.run):
    root = Path(root).resolve(strict=True)
    for args in (["rev-parse", "HEAD"], ["status", "--porcelain", "--untracked-files=no"]):
        result = runner(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
        expected = MINI_REVISION if args[0] == "rev-parse" else ""
        if result.returncode or result.stdout.strip() != expected:
            raise RuntimeError("mini-SWE repository identity changed")
    for relative, expected in SOURCE_HASHES.items():
        path = root / relative
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise RuntimeError("mini-SWE source escaped its pinned checkout")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("mini-SWE instrumentation seam changed")
    return root


def verify_imports(root):
    names = ("minisweagent.agents.default", "minisweagent.models.litellm_model",
             "minisweagent.models.utils.retry")
    if any(name in sys.modules for name in names):
        raise RuntimeError("mini-SWE was imported before instrumentation")
    expected = {
        "minisweagent.agents.default": root / "src/minisweagent/agents/default.py",
        "minisweagent.models.litellm_model": root / "src/minisweagent/models/litellm_model.py",
        "minisweagent.models.utils.retry": root / "src/minisweagent/models/utils/retry.py",
    }
    for name, path in expected.items():
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None or Path(spec.origin).resolve() != path:
            raise RuntimeError("mini-SWE import is outside its pinned checkout")


def _ids(headers):
    # These are the only response headers retained; arbitrary headers may carry secrets.
    if headers is None:
        return {}
    names = {"x-request-id": "http_request_id", "openai-request-id": "openai_request_id",
             "x-anvil-request-id": "router_request_id"}
    return {target: value for source, target in names.items() if source in headers
            if re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value := str(headers[source]))}


def _dependency_versions():
    result = {}
    for name in ("litellm", "httpx", "openai", "tenacity"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def verify_dependencies():
    versions = _dependency_versions()
    if versions != SUPPORTED_DEPENDENCIES:
        raise RuntimeError("unsupported pinned client dependency versions")
    import openai
    base = openai._base_client.BaseClient
    if not callable(getattr(base, "_calculate_retry_timeout", None)) or not callable(getattr(base, "_should_retry", None)):
        raise RuntimeError("unsupported OpenAI retry observation seam")
    return versions


def _response_ids(message):
    """Pick known upstream identifiers from mini-SWE's persisted response metadata."""
    try:
        extra = message.get("extra") if isinstance(message, dict) else None
        response = extra.get("response") if isinstance(extra, dict) else None
        value = response.get("id") if isinstance(response, dict) else None
        return {"response_id": value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value) else None}
    except BaseException:
        # Metadata access must never replace the original result or exception.
        return {"response_id": None}


def _exception_response_ids(exc):
    try:
        messages = getattr(exc, "messages", None)
        return _response_ids(messages[0]) if isinstance(messages, list) and messages else {"response_id": None}
    except BaseException:
        return {"response_id": None}


def install_observers(*, default_agent, litellm_model, httpx_client, recorder, openai_base=None):
    """Install exact pinned seams and return a restoration callback for tests/errors."""
    original_query = default_agent.query
    original_retry = litellm_model.retry
    original_send = httpx_client.send
    original_calculate = getattr(openai_base, "_calculate_retry_timeout", None)
    original_should = getattr(openai_base, "_should_retry", None)

    def agent_query(self, *args, **kwargs):
        started_at, started_mono = _stamp()
        context = {"logical_request_id": recorder.next_logical_request_id(),
                   "task_instance_id": getattr(self, "instance_id", None),
                   "step_ordinal": self.n_calls + 1, "agent_attempt_ordinal": None,
                   "sdk_transport_attempt_ordinal": 0, "started_at": started_at,
                   "started_mono": started_mono}
        token = _context.set(context)
        recorder.record("logical_started", logical_request_id=context["logical_request_id"],
                        task_instance_id=context["task_instance_id"], step_ordinal=context["step_ordinal"],
                        utc_started_at=started_at)
        outcome = "success"
        error_type = None
        try:
            message = original_query(self, *args, **kwargs)
            recorder.record("model_response", logical_request_id=context["logical_request_id"],
                            task_instance_id=context["task_instance_id"], step_ordinal=context["step_ordinal"],
                            ids=_response_ids(message))
            return message
        except BaseException as exc:
            outcome, error_type = "error", type(exc).__name__
            ids = _exception_response_ids(exc)
            if ids["response_id"] is not None:
                recorder.record("model_response", logical_request_id=context["logical_request_id"],
                                task_instance_id=context["task_instance_id"], step_ordinal=context["step_ordinal"], ids=ids)
            raise
        finally:
            ended_at, ended_mono = _stamp()
            recorder.record("logical_finished", logical_request_id=context["logical_request_id"],
                            task_instance_id=context["task_instance_id"], step_ordinal=context["step_ordinal"],
                            utc_started_at=context["started_at"], utc_ended_at=ended_at,
                            monotonic_duration_s=round(ended_mono - context["started_mono"], 6),
                            outcome=outcome, error_type=error_type)
            _context.reset(token)

    def observed_retry(*args, **kwargs):
        retrying = original_retry(*args, **kwargs)
        original_before, original_before_sleep = retrying.before, retrying.before_sleep

        def before(state):
            context = _context.get()
            if context is not None:
                context["agent_attempt_ordinal"] = state.attempt_number
                recorder.record("agent_attempt_started", logical_request_id=context["logical_request_id"],
                                task_instance_id=context["task_instance_id"], step_ordinal=context["step_ordinal"],
                                agent_attempt_ordinal=state.attempt_number)
            if original_before is not None:
                original_before(state)

        def before_sleep(state):
            context = _context.get()
            if context is not None:
                error = state.outcome.exception() if state.outcome else None
                recorder.record("agent_retry_planned", logical_request_id=context["logical_request_id"],
                                task_instance_id=context["task_instance_id"], step_ordinal=context["step_ordinal"],
                                agent_attempt_ordinal=state.attempt_number,
                                retry_reason_type=type(error).__name__ if error else None,
                                planned_backoff_s=round(float(state.next_action.sleep), 6) if state.next_action else None)
            if original_before_sleep is not None:
                original_before_sleep(state)

        retrying.before, retrying.before_sleep = before, before_sleep
        return retrying

    def observed_send(self, request, *args, **kwargs):
        context = _context.get()
        if context is not None:
            context["sdk_retry_reason_type"] = None
        started_at, started_mono = _stamp()
        if context is not None:
            context["sdk_transport_attempt_ordinal"] += 1
            ordinal = context["sdk_transport_attempt_ordinal"]
        else:
            ordinal = None
        try:
            response = original_send(self, request, *args, **kwargs)
        except BaseException as exc:
            ended_at, ended_mono = _stamp()
            if context is not None:
                context["sdk_retry_reason_type"] = type(exc).__name__
                recorder.record("transport_error", logical_request_id=context["logical_request_id"],
                                task_instance_id=context["task_instance_id"], step_ordinal=context["step_ordinal"],
                                agent_attempt_ordinal=context["agent_attempt_ordinal"],
                                sdk_transport_attempt_ordinal=ordinal, utc_started_at=started_at,
                                utc_ended_at=ended_at, monotonic_duration_s=round(ended_mono - started_mono, 6),
                                error_type=type(exc).__name__)
            raise
        ended_at, ended_mono = _stamp()
        if context is not None:
            recorder.record("transport_response", logical_request_id=context["logical_request_id"],
                            task_instance_id=context["task_instance_id"], step_ordinal=context["step_ordinal"],
                            agent_attempt_ordinal=context["agent_attempt_ordinal"],
                            sdk_transport_attempt_ordinal=ordinal, utc_started_at=started_at, utc_ended_at=ended_at,
                            monotonic_duration_s=round(ended_mono - started_mono, 6), http_status=int(response.status_code),
                            ids=_ids(getattr(response, "headers", None)))
        return response

    def observed_should_retry(self, response, *args, **kwargs):
        decision = original_should(self, response, *args, **kwargs)
        context = _context.get()
        if context is not None:
            context["sdk_retry_reason_type"] = f"HTTP_{int(response.status_code)}" if decision else None
        return decision

    def observed_calculate_retry_timeout(self, *args, **kwargs):
        delay = original_calculate(self, *args, **kwargs)
        context = _context.get()
        if context is not None:
            recorder.record("sdk_retry_planned", logical_request_id=context["logical_request_id"],
                            task_instance_id=context["task_instance_id"], step_ordinal=context["step_ordinal"],
                            agent_attempt_ordinal=context["agent_attempt_ordinal"],
                            sdk_transport_attempt_ordinal=context["sdk_transport_attempt_ordinal"],
                            retry_reason_type=context.get("sdk_retry_reason_type"),
                            planned_backoff_s=round(float(delay), 6))
        return delay

    default_agent.query, litellm_model.retry, httpx_client.send = agent_query, observed_retry, observed_send
    if openai_base is not None:
        openai_base._should_retry = observed_should_retry
        openai_base._calculate_retry_timeout = observed_calculate_retry_timeout

    def restore():
        default_agent.query, litellm_model.retry, httpx_client.send = original_query, original_retry, original_send
        if openai_base is not None:
            openai_base._should_retry, openai_base._calculate_retry_timeout = original_should, original_calculate
    return restore


_EVENT_FIELDS = {
    "adapter_ready": {"mini_revision", "source_hashes", "adapter_sha256", "dependencies", "transport_seam", "retry_seam", "clock"},
    "logical_started": {"logical_request_id", "task_instance_id", "step_ordinal", "utc_started_at"},
    "agent_attempt_started": {"logical_request_id", "task_instance_id", "step_ordinal", "agent_attempt_ordinal"},
    "transport_response": {"logical_request_id", "task_instance_id", "step_ordinal", "agent_attempt_ordinal", "sdk_transport_attempt_ordinal", "utc_started_at", "utc_ended_at", "monotonic_duration_s", "http_status", "ids"},
    "transport_error": {"logical_request_id", "task_instance_id", "step_ordinal", "agent_attempt_ordinal", "sdk_transport_attempt_ordinal", "utc_started_at", "utc_ended_at", "monotonic_duration_s", "error_type"},
    "agent_retry_planned": {"logical_request_id", "task_instance_id", "step_ordinal", "agent_attempt_ordinal", "retry_reason_type", "planned_backoff_s"},
    "sdk_retry_planned": {"logical_request_id", "task_instance_id", "step_ordinal", "agent_attempt_ordinal", "sdk_transport_attempt_ordinal", "retry_reason_type", "planned_backoff_s"},
    "model_response": {"logical_request_id", "task_instance_id", "step_ordinal", "ids"},
    "logical_finished": {"logical_request_id", "task_instance_id", "step_ordinal", "utc_started_at", "utc_ended_at", "monotonic_duration_s", "outcome", "error_type"},
    "adapter_finished": {"status", "outcome", "event_count", "byte_count", "omitted_events", "omitted_bytes", "limits"},
}
_COMMON_FIELDS = {"schema", "event", "seq", "utc_observed_at"}
_ID_FIELDS = {"http_request_id", "openai_request_id", "router_request_id", "response_id"}


def _bounded_value(value):
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return len(value) <= 256
    if isinstance(value, int):
        return abs(value) <= 10**12
    if isinstance(value, float):
        return math.isfinite(value) and abs(value) <= 10**12
    return False


def _utc_timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", value):
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


def _valid_event(value, expected_seq):
    if (not isinstance(value, dict) or value.get("schema") != EVENT_SCHEMA
            or type(value.get("seq")) is not int or value.get("seq") != expected_seq):
        return False
    event = value.get("event")
    if not isinstance(event, str) or event not in _EVENT_FIELDS or set(value) != _COMMON_FIELDS | _EVENT_FIELDS[event]:
        return False
    if not all(_bounded_value(item) for key, item in value.items() if key not in {"ids", "source_hashes", "dependencies", "clock", "limits"}):
        return False
    ids = value.get("ids")
    if ids is not None and (not isinstance(ids, dict) or set(ids) - _ID_FIELDS
                            or not all(item is None or isinstance(item, str)
                                       and re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", item) for item in ids.values())):
        return False
    if event == "adapter_ready":
        clock = value.get("clock")
        return (value.get("mini_revision") == MINI_REVISION and isinstance(value.get("adapter_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", value["adapter_sha256"])
                and value.get("source_hashes") == SOURCE_HASHES and value.get("dependencies") == SUPPORTED_DEPENDENCIES
                and value.get("transport_seam") == "httpx.Client.send" and value.get("retry_seam") == "minisweagent.models.litellm_model.retry"
                and isinstance(clock, dict) and set(clock) == {"wall", "wall_precision_s", "wall_resolution_s", "duration", "duration_resolution_s", "synchronized"}
                and clock.get("wall") == "UTC system clock" and clock.get("duration") == "monotonic"
                and clock.get("synchronized") is None
                and all(type(clock.get(field)) in {int, float} and math.isfinite(clock[field]) and clock[field] > 0
                        for field in {"wall_precision_s", "wall_resolution_s", "duration_resolution_s"}))
    if event == "adapter_finished":
        limits = value.get("limits")
        return (limits == {"max_events": MAX_EVENTS, "max_bytes": MAX_BYTES}
                and value.get("status") in {"complete", "truncated"}
                and value.get("outcome") in {"completed", "failed", "instrumentation_failed"}
                and all(type(value[field]) is int and value[field] >= 0
                        for field in {"event_count", "byte_count", "omitted_events", "omitted_bytes"})
                and (value["status"] != "complete" or value["omitted_events"] == value["omitted_bytes"] == 0))
    for field in {"step_ordinal", "agent_attempt_ordinal", "sdk_transport_attempt_ordinal"}:
        if field in value and value[field] is not None and (type(value[field]) is not int or value[field] < (0 if field == "sdk_transport_attempt_ordinal" else 1)):
            return False
    if "http_status" in value and (type(value["http_status"]) is not int or not 100 <= value["http_status"] <= 599):
        return False
    for field in {"monotonic_duration_s", "planned_backoff_s"}:
        if field in value and (type(value[field]) not in {int, float} or not math.isfinite(value[field]) or value[field] < 0):
            return False
    for field in {"logical_request_id", "task_instance_id"}:
        if field in value and value[field] is not None and (not isinstance(value[field], str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value[field])):
            return False
    for field in {"utc_observed_at", "utc_started_at", "utc_ended_at"}:
        if field in value and not _utc_timestamp(value[field]):
            return False
    for field in {"error_type", "retry_reason_type"}:
        if field in value and value[field] is not None and (not isinstance(value[field], str) or not re.fullmatch(r"(?:HTTP_[1-5][0-9]{2}|[A-Za-z][A-Za-z0-9_.]{0,127})", value[field])):
            return False
    if event == "logical_finished" and value.get("outcome") not in {"success", "error"}:
        return False
    return True


def read_attempt_evidence(path, instance_id=None, *, expected_adapter_sha256=None):
    """Read bounded adapter output for the SWE sidecar without importing harness deps."""
    source = Path(path)
    if expected_adapter_sha256 is not None and (not isinstance(expected_adapter_sha256, str)
                                                or not re.fullmatch(r"[0-9a-f]{64}", expected_adapter_sha256)):
        raise ValueError("expected adapter digest must be SHA-256")
    if source.is_symlink() or not source.is_file():
        return {"schema": EVENT_SCHEMA, "status": "missing", "path": str(source), "events": []}
    size = source.stat().st_size
    if size > MAX_BYTES + 8192:
        return {"schema": EVENT_SCHEMA, "status": "invalid", "path": str(source), "bytes": size, "events": []}
    with source.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 8193)
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_BYTES + 8192:
        return {"schema": EVENT_SCHEMA, "status": "invalid", "path": str(source), "sha256": digest,
                "bytes": len(raw), "events": []}
    events = []
    all_events = []
    preterminal_bytes = 0
    try:
        for expected_seq, line in enumerate(raw.splitlines(), 1):
            if expected_seq > MAX_EVENTS:
                raise ValueError
            value = json.loads(line)
            if not _valid_event(value, expected_seq):
                raise ValueError
            all_events.append(value)
            if value["event"] != "adapter_finished":
                preterminal_bytes += len(line) + 1
            if instance_id is None or value.get("task_instance_id") == instance_id or value["event"] in {"adapter_ready", "adapter_finished"}:
                events.append(value)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError):
        return {"schema": EVENT_SCHEMA, "status": "invalid", "path": str(source), "sha256": digest,
                "bytes": len(raw), "events": []}
    ready = bool(all_events and all_events[0].get("event") == "adapter_ready")
    terminal = all_events[-1] if all_events and all_events[-1].get("event") == "adapter_finished" else None
    final = terminal if events and events[-1].get("event") == "adapter_finished" else None
    if terminal and (not ready or (expected_adapter_sha256 is not None and all_events[0]["adapter_sha256"] != expected_adapter_sha256)
                     or terminal["event_count"] != terminal["seq"] - 1 or terminal["byte_count"] != preterminal_bytes):
        final = None
    return {"schema": EVENT_SCHEMA, "status": final.get("status", "incomplete") if final else "incomplete",
            "path": str(source), "sha256": digest, "bytes": len(raw), "event_count": len(events), "events": events,
            "omitted_events": final.get("omitted_events", 0) if final else None,
            "omitted_bytes": final.get("omitted_bytes", 0) if final else None}


def terminal_outcome(exc):
    """Typer uses SystemExit(0) for a normal completed command."""
    return "completed" if isinstance(exc, SystemExit) and exc.code in (None, 0) else "failed"


def run_agent(root, argv, *, adapter_sha256, events, run_module=runpy.run_module):
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if own_hash != adapter_sha256:
        raise RuntimeError("SWE agent observation adapter changed after planning")
    root = verify_source(root)
    original_path = sys.path[:]
    adapter_directory = Path(__file__).resolve().parent
    sys.path[:] = [str(root / "src"), *(entry for entry in original_path if Path(entry).resolve() != adapter_directory)]
    recorder = EventRecorder(events)
    restore = None
    try:
        verify_imports(root)
        from minisweagent.agents.default import DefaultAgent
        import minisweagent.models.litellm_model as litellm_model
        import httpx
        import openai
        dependencies = verify_dependencies()
        recorder.record("adapter_ready", mini_revision=MINI_REVISION, source_hashes=SOURCE_HASHES,
                        adapter_sha256=own_hash, dependencies=dependencies,
                        transport_seam="httpx.Client.send", retry_seam="minisweagent.models.litellm_model.retry",
                        clock={"wall": "UTC system clock", "wall_precision_s": 0.000001,
                               "wall_resolution_s": time.get_clock_info("time").resolution,
                               "duration": "monotonic", "duration_resolution_s": time.get_clock_info("monotonic").resolution,
                               "synchronized": None})
        restore = install_observers(default_agent=DefaultAgent, litellm_model=litellm_model,
                                    httpx_client=httpx.Client, recorder=recorder,
                                    openai_base=openai._base_client.BaseClient)
        original_argv = sys.argv
        try:
            sys.argv = ["minisweagent.run.benchmarks.swebench", *argv]
            result = run_module("minisweagent.run.benchmarks.swebench", run_name="__main__")
            recorder.close(outcome="completed")
            return result
        except BaseException as exc:
            recorder.close(outcome=terminal_outcome(exc))
            raise
        finally:
            sys.argv = original_argv
    finally:
        if restore is not None:
            restore()
        if not recorder._file.closed:
            recorder.close(outcome="instrumentation_failed")
        sys.path[:] = original_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mini-root", required=True)
    parser.add_argument("--adapter-sha256", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("agent_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.agent_args or args.agent_args[0] != "--":
        parser.error("mini-SWE arguments must follow --")
    run_agent(args.mini_root, args.agent_args[1:], adapter_sha256=args.adapter_sha256, events=args.events)


if __name__ == "__main__":
    main()
