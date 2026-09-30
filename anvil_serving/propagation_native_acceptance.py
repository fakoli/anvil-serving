"""Owned native fixture lifecycle around one approved catalog transaction.

This fixed procedure is called by the installed owner, never by readback. It
keeps Pi's pre-effect process alive, uses native durable resume for the other
clients, and persists only closed redacted evidence. Completed-job retries read
existing receipts rather than invoking this lifecycle again.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import time
import uuid

from .control_plane.propagation import ReceiptIdentity, _utc
from .propagation_pi_acceptance import (
    PiAcceptanceError, PiQuiescenceError, PiFixtureAcceptance, PiFixtureProfile, PiWebFixtureAcceptance, PiWebFixtureProfile, _write_private_json,
    write_acceptance_receipt,
)
from .propagation_sessions import NativeSessionCheck, native_session_state
from .routed_eval import evaluate_hermes, evaluate_openclaw


class NativeAcceptanceError(RuntimeError):
    """Bounded acceptance failed without exposing client output or account data."""


class NativeQuiescenceError(RuntimeError):
    """Owned fixture cleanup is unproven; never classify this as pending evidence."""


def _stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trusted_admin_groups(runtime):
    groups = runtime.get("trusted_admin_group_ids", [])
    if "trusted_admin_group_ids" in runtime and (
            sys.platform != "darwin" or type(groups) is not list or not groups
            or any(type(gid) is not int or gid <= 0 for gid in groups)
            or groups != sorted(set(groups))
            or not set(groups) <= {*os.getgroups(), os.getegid()}):
        raise NativeAcceptanceError("native administrator group approval differs")
    return frozenset(groups)


def _custody(path, *, directory=False, digest=None, trusted_admin_groups=frozenset()):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise NativeAcceptanceError("native runtime path is not canonical")
    for current in (path, *path.parents):
        info = current.lstat()
        writable = (info.st_mode & 0o002
            or info.st_mode & 0o020 and info.st_gid not in trusted_admin_groups)
        if (stat.S_ISLNK(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or writable and not (current == Path('/tmp') and info.st_mode & stat.S_ISVTX)
                or current != path and not stat.S_ISDIR(info.st_mode)):
            raise NativeAcceptanceError("native runtime custody differs")
    info = path.stat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise NativeAcceptanceError("native runtime directory is unavailable")
    elif not stat.S_ISREG(info.st_mode):
        raise NativeAcceptanceError("native runtime file is unavailable")
    if digest is not None and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise NativeAcceptanceError("native executable differs from its pin")
    return path


def validate_native_runtime(target):
    """Static preview only: no process, turn, fixture creation, or credential read."""
    sessions = target.get("native_sessions", [])
    if not sessions:
        return
    runtime = target.get("native_runtime")
    web = type(runtime) is dict and runtime.get("kind") == "pi_web"
    fields = {"kind", "fixture_root", "port", "acceptance_token_file", "bridge_artifact_digest", "loaded_catalog_digest"} if web else {"executable", "fixture_root", "path"}
    if target["client"] == "pi" and not web:
        fields |= {"agent_dir", "loaded_catalog_digest"}
    if (target["client"] not in {"pi", "hermes", "openclaw"} or type(runtime) is not dict
            or set(runtime) not in (fields, fields | {"trusted_admin_group_ids"})
            or web and "trusted_admin_group_ids" in runtime):
        raise NativeAcceptanceError("unsupported native runtime declaration")
    trusted_groups = _trusted_admin_groups(runtime)
    if web:
        if target["client"] != "pi" or type(runtime["port"]) is not int or not 1024 <= runtime["port"] <= 65535:
            raise NativeAcceptanceError("invalid Pi Web transport")
        from .control_plane.propagation import _digest
        _digest(runtime["bridge_artifact_digest"])
        _digest(runtime["loaded_catalog_digest"])
        token_path = _custody(runtime["acceptance_token_file"])
        if token_path.stat().st_mode & 0o077:
            raise NativeAcceptanceError("Pi Web acceptance token is not private")
    elif type(runtime["path"]) is not list or not runtime["path"] or any(
            type(value) is not str or not Path(value).is_absolute() or ".." in Path(value).parts
            for value in runtime["path"]):
        raise NativeAcceptanceError("native runtime PATH is not declared")
    for value in runtime.get("path", []):
        _custody(Path(value).resolve(), directory=True, trusted_admin_groups=trusted_groups)
    root = _custody(runtime["fixture_root"], directory=True)
    if root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise NativeAcceptanceError("native fixture root must be private")
    fixture_ids = {}
    for item in sessions:
        prior = fixture_ids.setdefault(item["fixture_file"], item["fixture_id"])
        if prior != item["fixture_id"]:
            raise NativeAcceptanceError("shared native fixture identity differs")
        if web:
            if item["executable_digest"] != runtime["bridge_artifact_digest"]:
                raise NativeAcceptanceError("Pi Web bridge identity differs")
        else:
            _custody(runtime["executable"], digest=item["executable_digest"], trusted_admin_groups=trusted_groups)
        for key in ("fixture_file", "receipt_file"):
            path = Path(item[key])
            if path.parent != root or path.name in {"", ".", ".."}:
                raise NativeAcceptanceError("native evidence is outside its fixture root")
    if target["client"] == "pi" and not web:
        _custody(runtime["agent_dir"], directory=True)
    elif target["client"] == "hermes":
        if runtime["executable"] != target["hermes"]["bin"]:
            raise NativeAcceptanceError("Hermes runtime differs from catalog writer")
        _custody(target["hermes"]["home"], directory=True)


def _cleanup_binding(profile, contract, job, job_digest):
    from .control_plane.propagation import _digest, _id
    _digest(job_digest)
    for key in ("job_id", "operation_id", "intent_id"):
        _id(job[key])
    targets = sorted((row for row in profile["targets"] if row.get("native_sessions")),
                     key=lambda row: row["target_id"])
    if not targets or job["contract_digest"] != contract.digest:
        raise NativeQuiescenceError("native cleanup identity differs")
    record = {"schema": "native-session-cleanup/v1", "quiescent": True,
        "job_id": job["job_id"], "operation_id": job["operation_id"], "intent_id": job["intent_id"],
        "job_digest": job_digest, "contract_digest": contract.digest,
        "profile_identity_digest": profile["expected_identity_digest"],
        "fixtures": sorted([[row["target_id"], item["kind"], item["fixture_id"]]
            for row in targets for item in row["native_sessions"]])}
    path = Path(targets[0]["native_runtime"]["fixture_root"]) / ("cleanup-" + job_digest + ".json")
    return path, record


def verify_native_cleanup(profile, contract, job, job_digest):
    """Read historical cleanup proof only; never recreate fixtures on retry."""
    from .control_plane.mcp.auth_file import read_private_auth_file
    path, expected = _cleanup_binding(profile, contract, job, job_digest)
    try:
        actual = json.loads(read_private_auth_file(path, max_bytes=16384))
        if actual != expected:
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise NativeQuiescenceError("original native fixture cleanup is unproven") from None


class NativeFixtureLifecycle:
    """Fixed preturn/apply/postturn boundary; caller supplies its owned runner.

    ``run(argv, timeout_seconds, environment)`` must terminate its owned process
    group before returning or raising. It is installed owner code, never input.
    The caller invokes prepare before effects, complete after the closed journal,
    and close in finally. Static preview uses validate_native_runtime alone.
    """

    def __init__(self, profile, contract, *, run, pi=None, web=None, budget_seconds=540):
        self.profile, self.contract, self.run = profile, contract, run
        self.pi = pi or PiFixtureAcceptance()
        self.web = web or PiWebFixtureAcceptance()
        self.web_handles = set()
        self.targets = [row for row in profile["targets"] if row.get("native_sessions")]
        self.deadline = time.monotonic() + min(budget_seconds,
            max(0, (_utc(contract.value["deadline_at"]) - datetime.now(timezone.utc)).total_seconds()))
        self.prepared = {}
        self.unquiescent = False
        self.cleanup_reserve = 4 + 10 * sum(row.get("native_runtime", {}).get("kind") == "pi_web" for row in self.targets)

    def require_effect_budget(self, seconds):
        if type(seconds) is not int or seconds <= 0:
            raise ValueError("invalid effect budget")
        if self.deadline - time.monotonic() < seconds + self.cleanup_reserve:
            raise NativeAcceptanceError("insufficient native effect budget")

    def _timeout(self):
        remaining = self.deadline - time.monotonic() - self.cleanup_reserve
        if remaining <= 1:
            raise NativeAcceptanceError("native acceptance budget exhausted")
        return max(1, int(min(120, remaining)))

    def _environment(self, target):
        env = {key: os.environ[key] for key in ("HOME", "TMPDIR", "LANG") if key in os.environ}
        env["PATH"] = os.pathsep.join(dict.fromkeys(str(Path(value).resolve())
            for value in [*target["native_runtime"]["path"], "/usr/bin", "/bin"]))
        if target["client"] == "pi":
            env["PI_CODING_AGENT_DIR"] = target["native_runtime"]["agent_dir"]
        elif target["client"] == "hermes":
            env["HERMES_HOME"] = target["hermes"]["home"]
        else:
            env["OPENCLAW_CONFIG_PATH"] = target["paths"]["openclaw"]
        return env

    def _web_profile(self, target):
        from .control_plane.mcp.auth_file import read_private_auth_file
        runtime = target["native_runtime"]
        token = read_private_auth_file(runtime["acceptance_token_file"], max_bytes=512).decode().strip()
        return PiWebFixtureProfile(runtime["port"], token, runtime["bridge_artifact_digest"], runtime["loaded_catalog_digest"])

    def _pi_profile(self, target):
        validate_native_runtime(target)
        runtime = target["native_runtime"]
        return PiFixtureProfile(Path(runtime["executable"]), target["native_sessions"][0]["executable_digest"],
            Path(runtime["agent_dir"]), Path(runtime["fixture_root"]),
            runtime["loaded_catalog_digest"], self._environment(target))

    def _turn(self, target, item, *, session_id=None, previous_marker=None, require_loaded=False):
        timeout = self._timeout()
        marker = secrets.token_hex(24)
        root = Path(target["native_runtime"]["fixture_root"])
        probe = root / ("probe-" + secrets.token_hex(16))
        descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, marker.encode())
        finally:
            os.close(descriptor)
        def runner(argv, timeout):
            validate_native_runtime(target)
            if target["client"] == "hermes":
                argv = (argv[0], "-p", target["hermes"]["profile"], *argv[1:])
            return self.run(argv, min(timeout, self._timeout()), self._environment(target))
        arguments = dict(alias=item["model_id"], provider=item["provider"], marker=marker,
            probe_path=str(probe), timeout_seconds=timeout, runner=runner,
            executable=target["native_runtime"]["executable"], configured_route=True,
            session_id=session_id, previous_marker=previous_marker)
        try:
            if target["client"] == "hermes":
                result = evaluate_hermes(**arguments, expected_observed_provider=item["provider"])
            else:
                result = evaluate_openclaw(**arguments, run_id="fixture-" + secrets.token_hex(16),
                    expected_context_tokens=item["context_tokens"])
            checks = result["checks"]
            if target["client"] == "openclaw" and (
                    not all(checks.get(key) is True for key in (
                        "process_succeeded", "output_bounded", "json_valid", "status_ok", "session_identified"))
                    or session_id is not None and checks.get("session_preserved") is not True
                    or result["observed"].get("stop_reason") != "stop"):
                self.unquiescent = True
                raise NativeQuiescenceError("native gateway turn completion is unproven")
            accepted = (result["passed"] if require_loaded or target["client"] != "openclaw"
                else all(value for key, value in checks.items() if key != "context_exact"))
            if accepted is not True:
                raise NativeAcceptanceError("native fixture turn did not verify")
            return result["observed"], marker
        finally:
            probe.unlink(missing_ok=True)

    def prepare(self):
        if self.prepared:
            raise NativeAcceptanceError("native fixtures already prepared")
        for target in self.targets:
            validate_native_runtime(target)
            if any(Path(item[key]).exists() or Path(item[key]).is_symlink()
                   for item in target["native_sessions"] for key in ("fixture_file", "receipt_file")):
                raise NativeAcceptanceError("native fixture evidence already exists")
        try:
            for target in self.targets:
                item = next((item for item in target["native_sessions"]
                    if item["kind"] == "existing_session"), target["native_sessions"][0])
                if target["client"] == "pi":
                    if target["native_runtime"].get("kind") == "pi_web":
                        operation_id = "fixture-" + hashlib.sha256((self.contract.digest + target["target_id"]).encode()).hexdigest()
                        handle = self.web.prepare_existing(self._web_profile(target), operation_id,
                            "propagation-" + str(uuid.uuid4()), timeout_seconds=self._timeout())
                        self.web_handles.add(target["target_id"])
                    else:
                        handle = self.pi.prepare_existing(self._pi_profile(target), str(uuid.uuid4()),
                        timeout_seconds=self._timeout())
                    self.prepared[target["target_id"]] = (handle, None)
                    native_id, marker, generation, prepared_at = (handle.native_session_id,
                        handle.history_marker, handle.process_generation, handle.prepared_at)
                else:
                    observed, marker = self._turn(target, item)
                    native_id, generation, prepared_at = observed["session_id"], None, _stamp()
                    self.prepared[target["target_id"]] = (None, (native_id, marker, prepared_at))
                for session in target["native_sessions"]:
                    record = {"schema": "native-session-fixture/v1", "fixture_id": session["fixture_id"],
                        "target_id": target["target_id"], "contract_digest": self.contract.digest,
                        "executable_digest": session["executable_digest"], "prepared_at": prepared_at,
                        "native_session_id": native_id, "process_generation": generation, "history_marker": marker}
                    _write_private_json(Path(session["fixture_file"]), record)
        except BaseException as exc:
            if isinstance(exc, (PiQuiescenceError, NativeQuiescenceError)):
                self.unquiescent = True
            self.close()
            raise

    def complete(self, completed_at, physical_catalogs):
        states = {}
        try:
            for target in self.targets:
                handle, prior = self.prepared[target["target_id"]]
                native_id, marker, prepared_at = ((handle.native_session_id, handle.history_marker, handle.prepared_at)
                    if handle is not None else prior)
                for item in sorted(target["native_sessions"], key=lambda item: item["kind"]):
                    check = NativeSessionCheck(target["target_id"], ReceiptIdentity(target["installation_id"],
                        target["profile_id"], target["runtime_id"]), self.contract.digest,
                        self.profile["expected_identity_digest"], item["executable_digest"],
                        physical_catalogs[target["target_id"]], item["fixture_id"], native_id,
                        prepared_at, completed_at, item["kind"], item["provider"], item["model_id"],
                        item["context_tokens"], item["max_output_tokens"],
                        handle.process_generation if handle is not None else None)
                    self._timeout()
                    if target["target_id"] in self.web_handles:
                        receipt = (self.web.complete_existing(check, handle, timeout_seconds=self._timeout())
                            if item["kind"] == "existing_session" else self.web.accept_new(check, handle.profile,
                                handle.operation_id, "propagation-" + str(uuid.uuid4()), timeout_seconds=self._timeout()))
                    elif handle is not None:
                        receipt = (self.pi.complete_existing(check, handle, timeout_seconds=self._timeout()) if item["kind"] == "existing_session"
                            else self.pi.accept_new(check, self._pi_profile(target), str(uuid.uuid4()),
                                timeout_seconds=self._timeout()))
                    else:
                        existing = item["kind"] == "existing_session"
                        started = _stamp()
                        observed, _ = self._turn(target, item, session_id=native_id if existing else None,
                            previous_marker=marker if existing else None, require_loaded=not existing)
                        receipt = {"schema": "native-session-acceptance/v1", "check_digest": check.digest,
                            "started_at": started, "completed_at": _stamp(), "native_session_id": observed["session_id"],
                            "continuity_kind": "durable_resume" if existing else "loaded_fixture",
                            "configured_route": True, "provider": observed["provider"], "model": observed["model"],
                            "context_tokens": observed["context_tokens"], "max_output_tokens": observed["max_output_tokens"],
                            "turn_completed": True, "tool_probe_passed": True, "history_probe_passed": existing,
                            "fallback_used": observed["fallback_used"], "physical_catalog_digest": check.physical_catalog_digest,
                            "process_generation": None}
                    write_acceptance_receipt(Path(item["receipt_file"]), receipt)
                    states[(target["target_id"], item["kind"])] = native_session_state(check, receipt, now=datetime.now(timezone.utc))
            return states
        except PiQuiescenceError:
            self.unquiescent = True
            raise
        except PiAcceptanceError as exc:
            # Pi guarantees ordinary evidence failures are separate from hidden
            # new-process/Web cleanup failures. The finally still closes every
            # retained handle; any cleanup error overrides this pending outcome.
            raise NativeAcceptanceError("native Pi fixture evidence did not verify") from exc
        finally:
            self.close()

    def record_cleanup(self, job, job_digest):
        """Persist exact historical proof only after all owned work is closed."""
        self.close()
        if set(self.prepared) != {row["target_id"] for row in self.targets}:
            raise NativeQuiescenceError("native fixture preparation is incomplete")
        path, record = _cleanup_binding(self.profile, self.contract, job, job_digest)
        _write_private_json(path, record)

    def close(self):
        failed = False
        for target_id, (handle, _) in self.prepared.items():
            if handle is not None:
                try:
                    if target_id in self.web_handles:
                        self.web.close(handle, timeout_seconds=10)
                    else:
                        handle.close()
                except Exception:
                    failed = True
        if failed or self.unquiescent:
            raise NativeQuiescenceError("native fixture cleanup did not complete")
