"""Linux-only supervised execution for one immutable propagation profile."""
from __future__ import annotations

from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Mapping

from ...connect._qualification_supervisor import Children, ProcFailure
from .propagation_job_store import ExecutionProfile, JobStore, PropagationJobError, _MAX_CHILD_RESULT, _MAX_NATIVE_EFFECTS

_MAX_PARENT_RESULT = 4096
_CHILD_TIMEOUT = 2.0
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _identity(pid: int) -> dict[str, Any] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        tail = raw[raw.rfind(")") + 2:].split()
        if len(tail) < 20 or tail[0] == "Z":
            return None
        return {"pid": pid, "start_ticks": tail[19],
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()}
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ESRCH}:
            return None
        raise PropagationJobError("process_observation_failed") from exc


def _write(value: Mapping[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(raw) > _MAX_PARENT_RESULT:
        raw = b'{"native_effects":[],"outcome":"uncertain","quiescent":false}'
    os.write(sys.stdout.fileno(), raw + b"\n")


def _write_reference(reference: Mapping[str, Any]) -> None:
    _write(reference)


def _profile_result(raw: bytes) -> dict[str, Any]:
    if len(raw) > _MAX_CHILD_RESULT:
        return {"outcome": "uncertain", "native_effects": [], "quiescent": False}
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {"outcome": "uncertain", "native_effects": [], "quiescent": False}
    if (not isinstance(value, dict) or set(value) != {"outcome", "native_effects", "quiescent"}
            or type(value["outcome"]) is not str or value["outcome"] not in {"applied", "failed", "cancelled", "uncertain"}
            or type(value["native_effects"]) is not list or type(value["quiescent"]) is not bool):
        return {"outcome": "uncertain", "native_effects": [], "quiescent": False}
    # Result rows are retained public-safe. An adapter supplies only effect ids,
    # closed states, and opaque receipt references; console output never escapes.
    allowed_states = {"applied", "failed", "cancelled", "uncertain"}
    valid_effects = all(isinstance(item, dict) and set(item) == {"effect_id", "state", "receipt_ref"}
                        and all(isinstance(item[field], str) and _OPAQUE.fullmatch(item[field]) is not None for field in ("effect_id", "receipt_ref"))
                        and type(item["state"]) is str and item["state"] in allowed_states for item in value["native_effects"])
    if len(value["native_effects"]) > _MAX_NATIVE_EFFECTS or not valid_effects:
        return {"outcome": "uncertain", "native_effects": [], "quiescent": False}
    return value


def _result_reference(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw.decode("ascii", "strict"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    if (not isinstance(value, dict) or set(value) != {"job_id", "contract_digest", "child_pid", "child_start_ticks", "child_boot_id", "result_digest"}
            or type(value["child_pid"]) is not int or value["child_pid"] < 1
            or any(type(value[key]) is not str for key in {"job_id", "contract_digest", "child_start_ticks", "child_boot_id", "result_digest"})):
        return None
    return value


def _profile_deadline(job: Mapping[str, Any], budget_seconds: int) -> float:
    try:
        deadline = datetime.strptime(job["deadline_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise PropagationJobError("storage_unavailable") from exc
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise PropagationJobError("deadline_expired")
    return time.monotonic() + min(remaining, budget_seconds)


def _write_config(handle: Any, raw: bytes, deadline: float, cancelled: Callable[[], bool],
                  heartbeat: Callable[[], None], heartbeat_seconds: int) -> None:
    """A profile that never reads stdin cannot consume the execution budget."""
    descriptor = handle.fileno()
    os.set_blocking(descriptor, False)
    offset = 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_WRITE)
            next_heartbeat = time.monotonic()
            while offset < len(raw):
                if cancelled():
                    raise PropagationJobError("cancellation_requested")
                if time.monotonic() >= next_heartbeat:
                    heartbeat()
                    next_heartbeat = time.monotonic() + heartbeat_seconds
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if not selector.select(min(0.05, remaining)):
                    continue
                offset += os.write(descriptor, raw[offset:])
    finally:
        handle.close()


def _run_profile(profile: ExecutionProfile, job: Mapping[str, Any], cancelled: Callable[[], bool], children: Children,
                 heartbeat: Callable[[], None], prepare_child: Callable[[], list[dict[str, str]]],
                 register_child: Callable[[Mapping[str, Any]], None]) -> dict[str, Any]:
    """Execute fixed argv with bounded output, then prove its group is gone."""
    uncertain_effects: list[dict[str, str]] = []
    profile.verify_executable()
    if cancelled():
        return {"outcome": "cancelled", "native_effects": [], "quiescent": True}
    try:
        uncertain_effects = prepare_child()
    except PropagationJobError:
        # Cancellation may be committed after the first flag check but before
        # the pre-Popen child custody transaction. No native profile exists in
        # that branch, so the supervisor can report its own empty custody.
        if cancelled():
            return {"outcome": "cancelled", "native_effects": [], "quiescent": True}
        raise
    config = {"job_id": job["job_id"], "operation_id": job["operation_id"],
              "intent_id": job["intent_id"], "contract_digest": job["contract_digest"],
              "profile_id": profile.profile_id, "profile_digest": profile.digest,
              "resources": job["resources"], "deadline_at": job["deadline_at"],
              "canonical_contract": json.loads(job["canonical_contract"].decode("ascii")),
              "planned_effects": uncertain_effects}
    try:
        process = subprocess.Popen(profile.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, cwd=profile.cwd, env=dict(profile.environment or {}),
                                   close_fds=True, start_new_session=True)
    except OSError as exc:
        raise PropagationJobError("profile_unavailable") from exc
    assert process.stdin is not None and process.stdout is not None
    try:
        descriptor = os.pidfd_open(process.pid, 0)
    except OSError:
        process.kill()
        process.wait(timeout=_CHILD_TIMEOUT)
        return {"outcome": "uncertain", "native_effects": uncertain_effects, "quiescent": False}
    try:
        child_identity = _identity(process.pid)
        if child_identity is None:
            raise PropagationJobError("launch_reconciliation_required")
        register_child(child_identity)
        deadline = _profile_deadline(job, profile.budget_seconds)
        _write_config(process.stdin, json.dumps(config, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii"),
                      deadline, cancelled, heartbeat, profile.heartbeat_seconds)
        output = bytearray()
        next_heartbeat = time.monotonic()
        interrupted = False
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                if time.monotonic() >= next_heartbeat:
                    heartbeat()
                    next_heartbeat = time.monotonic() + profile.heartbeat_seconds
                if cancelled() or time.monotonic() >= deadline or len(output) > profile.output_limit:
                    interrupted = True
                    try:
                        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    break
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
        if interrupted:
            try:
                process.wait(timeout=_CHILD_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=_CHILD_TIMEOUT)
            empty, _, limited = children.cleanup(_CHILD_TIMEOUT)
            return {"outcome": "cancelled" if cancelled() else "uncertain", "native_effects": uncertain_effects, "quiescent": empty and not limited}
        code = process.wait(timeout=_CHILD_TIMEOUT)
        result = _profile_result(bytes(output))
        if result["outcome"] == "uncertain" and not result["native_effects"]:
            result["native_effects"] = uncertain_effects
        active, exceeded = children.scan()
        empty, _, limited = children.cleanup(_CHILD_TIMEOUT)
        if active or exceeded or not empty or limited:
            return {"outcome": "uncertain", "native_effects": uncertain_effects, "quiescent": False}
        if code != 0:
            return {"outcome": "failed" if result["outcome"] == "failed" else "uncertain",
                    "native_effects": result["native_effects"], "quiescent": True}
        # Child output cannot attest that the native process tree is gone. The
        # bounded scan and cleanup above are the supervisor-owned fact.
        return {**result, "quiescent": True}
    except (OSError, subprocess.SubprocessError, TypeError, ValueError, PropagationJobError):
        try:
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=_CHILD_TIMEOUT)
        except subprocess.SubprocessError:
            pass
        try:
            empty, _, limited = children.cleanup(_CHILD_TIMEOUT)
        except (ProcFailure, RuntimeError):
            empty, limited = False, True
        return {"outcome": "uncertain", "native_effects": uncertain_effects, "quiescent": empty and not limited}
    finally:
        os.close(descriptor)
        process.stdout.close()


def _child(argv: list[str]) -> int:
    """The product-owned fixed entrypoint. The gate token is read only via pipe."""
    if sys.platform != "linux" or len(argv) != 3 or argv[0] != "--child":
        return 2
    _, path, job_id = argv
    token = os.read(sys.stdin.fileno(), 128)
    if not token or len(token) > 128:
        return 3
    cancelled = False

    def on_cancel(_signal: int, _frame: object) -> None:
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGTERM, on_cancel)
    try:
        store = JobStore(path)
        identity = _identity(os.getpid())
        if identity is None:
            return 4
        job = store.begin_execution(job_id, token.decode("ascii"), identity)
        profile = ExecutionProfile.from_private_value(job["profile"])
        children = Children()
        try:
            def cancellation_requested() -> bool:
                return cancelled or store.lookup_internal(job_id)["state"] == "cancellation_requested"

            if cancellation_requested():
                result = {"outcome": "cancelled", "native_effects": [], "quiescent": True}
                store.record_pre_profile_cancellation(job_id, identity)
                _write(result)
                return 0
            result = _run_profile(profile, job, cancellation_requested, children,
                                  lambda: store.heartbeat(job_id, identity),
                                  lambda: store.prepare_profile_child(job_id, identity),
                                  lambda child_identity: store.register_profile_child(job_id, child_identity))
            if result == {"outcome": "cancelled", "native_effects": [], "quiescent": True}:
                # The durable cancellation gate refused before profile Popen, so
                # this native supervisor can persist the empty-custody outcome.
                # If a profile child was already reserved, preserve that custody
                # through the ordinary child-result path below instead.
                try:
                    store.record_pre_profile_cancellation(job_id, identity)
                except PropagationJobError:
                    pass
                else:
                    _write(result)
                    return 0
            try:
                _write_reference(store.child_result_reference(job_id, store.record_child_result(job_id, result)))
            except PropagationJobError:
                _write(result)
            return 0
        finally:
            try:
                children.cleanup(_CHILD_TIMEOUT)
            except (ProcFailure, RuntimeError):
                pass
            children.close()
    except PropagationJobError as exc:
        if exc.code == "cancellation_requested":
            # The durable gate can reject before begin_execution creates a
            # profile child. Preserve that exact no-child cancellation for a
            # fresh controller, which cannot recover this process's stdout.
            store.record_pre_profile_cancellation(job_id, identity)
            _write({"outcome": "cancelled", "native_effects": [], "quiescent": True})
            return 0
        _write({"outcome": "uncertain", "native_effects": [], "quiescent": False})
        return 5
    except UnicodeDecodeError:
        _write({"outcome": "uncertain", "native_effects": [], "quiescent": False})
        return 5


class PropagationSupervisor:
    """Parent-side custody and observation. It never relaunches an owned job."""

    def __init__(self, store: JobStore, *, reconcile: Callable[[Mapping[str, Any], Mapping[str, Any]], str] | None = None) -> None:
        if sys.platform != "linux":
            raise PropagationJobError("supervision_unavailable")
        self.store, self.reconcile = store, reconcile
        self._children: dict[str, subprocess.Popen[bytes]] = {}
        self._pidfds: dict[str, int] = {}

    def _release_pidfd(self, job_id: str) -> None:
        descriptor = self._pidfds.pop(job_id, None)
        if descriptor is not None:
            os.close(descriptor)

    def launch(self, job_id: str) -> dict[str, Any]:
        token = self.store.prepare_launch(job_id)
        process: subprocess.Popen[bytes] | None = None
        descriptor: int | None = None
        try:
            process = subprocess.Popen((sys.executable, "-m", __name__, "--child", self.store.path, job_id),
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       close_fds=True, start_new_session=True)
            identity = _identity(process.pid)
            if identity is None:
                raise PropagationJobError("launch_reconciliation_required")
            descriptor = os.pidfd_open(process.pid, 0)
            self.store.register_launch(job_id, token, identity)
            assert process.stdin is not None
            process.stdin.write(token.encode("ascii"))
            process.stdin.close()
            process.stdin = None
            self._children[job_id] = process
            self._pidfds[job_id] = descriptor
            return self.store.lookup(job_id)
        except (OSError, PropagationJobError):
            if process is not None:
                try:
                    process.kill()
                    process.wait(timeout=_CHILD_TIMEOUT)
                except (OSError, subprocess.SubprocessError):
                    pass
            retained = self._pidfds.pop(job_id, None)
            if retained is not None:
                os.close(retained)
            elif descriptor is not None:
                os.close(descriptor)
            self.store.recovery_required(job_id)
            raise PropagationJobError("launch_reconciliation_required") from None

    def _reconcile_completed_result(self, job_id: str, job: Mapping[str, Any]) -> dict[str, Any] | None:
        """Resolve a durable child result using the current durable job phase."""
        identity = {"pid": job["pid"], "start_ticks": job["start_ticks"], "boot_id": job["boot_id"]}
        try:
            result = self.store.completed_child_result(job_id, identity)
        except PropagationJobError:
            result = None
        if result is None:
            return None
        if self.reconcile is None or job["state"] not in {"executing", "cancellation_requested"}:
            return None
        try:
            self.store.record_result(job_id, identity, result, self.reconcile(job, result))
        except Exception:
            self.store.recovery_required(job_id)
        self._release_pidfd(job_id)
        return self.store.lookup(job_id)

    def _cancel_recovery_or_completion(self, job_id: str) -> dict[str, str]:
        """Prefer an owner-reconciled completion over a permanent cancel failure."""
        refreshed = self.store.lookup_internal(job_id)
        if refreshed["state"] in {"applied", "failed", "cancelled"}:
            self._release_pidfd(job_id)
            return {"job_id": job_id, "state": "confirmed"}
        completed = self._reconcile_completed_result(job_id, refreshed)
        if completed is not None and completed["state"] in {"applied", "failed", "cancelled"}:
            return {"job_id": job_id, "state": "confirmed"}
        self.store.recovery_required(job_id)
        self._release_pidfd(job_id)
        return {"job_id": job_id, "state": "uncertain"}

    def observe(self, job_id: str) -> dict[str, Any]:
        job = self.store.lookup_internal(job_id)
        process = self._children.get(job_id)
        if process is None:
            completed = self._reconcile_completed_result(job_id, job)
            if completed is not None:
                return completed
            if job["state"] in {"registered", "executing", "cancellation_requested"}:
                identity = {"pid": job["pid"], "start_ticks": job["start_ticks"], "boot_id": job["boot_id"]}
                observed = _identity(job["pid"])
                if observed is None or observed != identity:
                    # A child can commit its durable result and exit between the
                    # first result lookup and native identity observation. Refresh
                    # both the job phase and result before making that failure
                    # permanent, so an old registered snapshot cannot win that race.
                    refreshed = self.store.lookup_internal(job_id)
                    completed = self._reconcile_completed_result(job_id, refreshed)
                    if completed is not None:
                        return completed
                    self.store.recovery_required(job_id)
                    self._release_pidfd(job_id)
                    return self.store.lookup(job_id)
            if job["state"] == "launch_custody":
                self.store.recovery_required(job_id)
                self._release_pidfd(job_id)
            return self.store.lookup(job_id)
        if process.poll() is None:
            identity = _identity(process.pid)
            if identity is None or (identity["pid"], identity["start_ticks"], identity["boot_id"]) != (job["pid"], job["start_ticks"], job["boot_id"]):
                if process.poll() is None:
                    self._children.pop(job_id, None)
                    self.store.recovery_required(job_id)
                    return self.store.lookup(job_id)
                # The child can exit between the first poll and /proc identity
                # observation. Resolve its durable result below before failing.
            else:
                return self.store.lookup(job_id)
        self._children.pop(job_id, None)
        descriptor = self._pidfds.pop(job_id, None)
        if descriptor is not None:
            os.close(descriptor)
        identity = {"pid": job["pid"], "start_ticks": job["start_ticks"], "boot_id": job["boot_id"]}
        try:
            stdout, _ = process.communicate(timeout=_CHILD_TIMEOUT)
        except subprocess.SubprocessError:
            self.store.recovery_required(job_id)
            return self.store.lookup(job_id)
        raw_result = stdout.rstrip(b"\n")
        reference = _result_reference(raw_result)
        try:
            if reference is not None and (reference["job_id"], reference["contract_digest"]) == (job_id, job["contract_digest"]):
                result = self.store.load_child_result(job_id, identity, {"pid": reference["child_pid"], "start_ticks": reference["child_start_ticks"], "boot_id": reference["child_boot_id"]}, reference["result_digest"])
            else:
                result = _profile_result(raw_result)
        except PropagationJobError:
            self.store.recovery_required(job_id)
            return self.store.lookup(job_id)
        reconciliation = "uncertain"
        if self.reconcile is not None:
            try:
                reconciliation = self.reconcile(job, result)
            except Exception:
                reconciliation = "uncertain"
        try:
            self.store.record_result(job_id, identity, result, reconciliation)
        except PropagationJobError:
            self.store.recovery_required(job_id)
        return self.store.lookup(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.store.request_cancel(job_id)
        if job["state"] in {"applied", "failed", "cancelled"}:
            self._release_pidfd(job_id)
            return {"job_id": job_id, "state": "confirmed"}
        if job["state"] == "recovery_required":
            self._release_pidfd(job_id)
            return {"job_id": job_id, "state": "uncertain"}
        process = self._children.get(job_id)
        pid = process.pid if process is not None else job["pid"]
        identity = _identity(pid)
        if identity is None or (identity["pid"], identity["start_ticks"], identity["boot_id"]) != (job["pid"], job["start_ticks"], job["boot_id"]):
            return self._cancel_recovery_or_completion(job_id)
        descriptor = self._pidfds.get(job_id)
        if descriptor is None:
            try:
                descriptor = os.pidfd_open(pid, 0)
            except OSError:
                return self._cancel_recovery_or_completion(job_id)
            # A PID can exit and be reused between the first identity read and
            # pidfd_open. Retain this descriptor only after a second exact read.
            verified = _identity(pid)
            if verified is None or verified != identity:
                os.close(descriptor)
                return self._cancel_recovery_or_completion(job_id)
            self._pidfds[job_id] = descriptor
        try:
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        except ProcessLookupError:
            self._release_pidfd(job_id)
            return {"job_id": job_id, "state": "uncertain"}
        self._release_pidfd(job_id)
        return {"job_id": job_id, "state": "requested"}


if __name__ == "__main__":
    raise SystemExit(_child(sys.argv[1:]))
