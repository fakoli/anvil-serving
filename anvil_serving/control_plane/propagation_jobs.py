"""Owner propagation operations; fixed native adapters retain effect authority."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import threading
from typing import Callable, Mapping, Any
import uuid

from . import propagation as contract_api
from .controller.propagation_store import PropagationIntentStore, PropagationIntentError, logical_workflow_id
from .controller.propagation_job_store import PropagationJobError, JobStore
from .controller.propagation_supervisor import PropagationSupervisor
from .mcp.arguments import validate_schema_value


_MAX_SNAPSHOT = 4 * 1024 * 1024


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _stamp(now):
    if not isinstance(now, datetime) or now.tzinfo != timezone.utc:
        raise PropagationJobError("malformed_clock")
    return now.isoformat(timespec="seconds").replace("+00:00", "Z")


def _fresh(value, now, *, after=None):
    observed = contract_api._utc(value)
    if not timedelta(0) <= now - observed <= timedelta(minutes=5) or (after and observed <= after):
        raise PropagationJobError("stale_observation")
    return observed


@dataclass(frozen=True)
class PropagationProfile:
    """Installed owner callbacks; none of these values comes from an HTTP request."""
    profile_id: str
    profile_digest: str
    executor_issuer: str
    contract_lookup: Callable[[str], bytes]
    approval_lookup: Callable[[str], contract_api.ApprovedAuthority | None]
    active_identity: Callable[[], contract_api.ActiveIdentity]
    preview: Callable[[bytes], Mapping[str, Any]]
    observe: Callable[[bytes, Mapping[str, Any], str, str | None], Mapping[str, Any]]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    resume_workflow: Callable[[str, str, str, str], Mapping[str, Any]] | None = None
    cancel_workflow: Callable[[str, str, str, str], Mapping[str, Any]] | None = None
    recovery_evidence: Callable[[str, str], Mapping[str, Any]] | None = None
    workflow_status: Callable[[str, str], Mapping[str, Any]] | None = None


class PropagationService:
    def __init__(self, intents: PropagationIntentStore, jobs: JobStore,
                 supervisor: PropagationSupervisor, profile: PropagationProfile):
        # Admission and job reservation must serialize in one durable ledger.
        # Separate databases permit a newer generation to overtake an active job.
        if len({intents.path.resolve(), Path(jobs.path).resolve(), Path(supervisor.store.path).resolve()}) != 1:
            raise ValueError("propagation intent and job stores must share one database")
        contract_api._id(profile.profile_id)
        contract_api._digest(profile.profile_digest)
        contract_api._id(profile.executor_issuer)
        self.intents, self.jobs, self.supervisor, self.profile = intents, jobs, supervisor, profile
        # ponytail: one verification pass at a time; per-job locks if read volume grows.
        self._verification_lock = threading.Lock()
        with self.intents._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS propagation_previews (digest TEXT PRIMARY KEY, intent_id TEXT NOT NULL, payload BLOB NOT NULL, expires_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS propagation_snapshots (id TEXT PRIMARY KEY, principal TEXT NOT NULL, operation TEXT NOT NULL, identity TEXT NOT NULL, payload BLOB NOT NULL, expires_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS propagation_verification_passes (job_id TEXT PRIMARY KEY, verification_id TEXT NOT NULL UNIQUE, verified INTEGER NOT NULL DEFAULT 0)")

    def handle(self, operation, arguments, *, caller_id):
        """Translate all failures to bounded codes at the owner transport boundary."""
        try:
            contract_api._id(caller_id)
            if operation == "propagation.accept.v1":
                raw = self.profile.contract_lookup(arguments["approval_ref"])
                parsed = contract_api.parse_contract(raw)
                if parsed.value["approval_ref"] != arguments["approval_ref"]:
                    raise PropagationJobError("approval_mismatch")
                self._profile_binding(parsed)
                accepted = self.intents.admit(raw, approval_lookup=self.profile.approval_lookup,
                    active_identity=self.profile.active_identity(), caller_id=caller_id,
                    request_id=arguments["request_id"], now=self.profile.now())
                return {"intent_id": accepted.intent_id, "workflow_id": accepted.workflow_id,
                        "contract_digest": accepted.contract_digest}
            if operation == "propagation.profile.v1":
                installed = self.jobs.profiles.get(self.profile.profile_id)
                return {"profile_id": self.profile.profile_id,
                        "profile_digest": self.profile.profile_digest,
                        "installed": installed is not None and installed.digest == self.profile.profile_digest,
                        "observed_at": _stamp(self.profile.now())}
            if operation == "propagation.dispatch.pending.v1":
                return self.intents.pending(arguments["cursor"])
            if operation == "propagation.dispatch.record.v1":
                return {"recorded": self.intents.acknowledge(**arguments, now=self.profile.now())}
            if operation == "propagation.status.v1":
                identity = _hash({"intent_id": arguments["intent_id"]})
                if arguments["cursor"] is not None:
                    return self._page(operation, identity, caller_id, cursor=arguments["cursor"])
                return self._page(operation, identity, caller_id,
                                  payload=self.intent_status(arguments["intent_id"]))
            if operation in {"propagation.resume.v1", "propagation.cancel.v1"}:
                return self.control_workflow(operation, arguments, caller_id)
            if operation == "propagation.recovery.verify.v1":
                if arguments["profile_id"] != self.profile.profile_id:
                    raise PropagationJobError("profile_mismatch")
                if self.profile.recovery_evidence is None:
                    raise PropagationJobError("recovery_verifier_unavailable")
                result = json.loads(_json(self.profile.recovery_evidence(arguments["profile_id"], caller_id)))
                validate_schema_value(result, contract_api._result_schema(operation), "recovery result")
                if (result["profile_id"] != self.profile.profile_id
                        or result["state"] == "passed" and (
                            result["evidence_digest"] is None or result["verified_at"] is None)):
                    raise PropagationJobError("invalid_recovery_result")
                if result["evidence_digest"] is not None:
                    contract_api._digest(result["evidence_digest"])
                if result["verified_at"] is not None:
                    _fresh(result["verified_at"], self.profile.now())
                return result
            if operation == "fleet.propagation.submit.v1":
                return self.submit(**arguments)
            if operation == "fleet.propagation.current.v1":
                return self.current_authority(**arguments)
            if operation == "fleet.propagation.cancel.v1":
                job = self.supervisor.cancel(arguments["job_id"])
                return {"job_id": arguments["job_id"], "state": job["state"] if job["state"] in {"requested", "confirmed", "uncertain"} else self._cancel_state(job)}
            if operation not in {"fleet.propagation." + name + ".v1" for name in ("preview", "status", "verify", "convergence")}:
                raise PropagationJobError("unsupported_operation")
            identity = _hash({key: value for key, value in arguments.items() if key != "cursor"})
            if arguments["cursor"] is not None:
                return self._page(operation, identity, caller_id, cursor=arguments["cursor"])
            verb = operation.split(".")[2]
            if verb in {"verify", "convergence"}:
                if not self._verification_lock.acquire(blocking=False):
                    raise PropagationJobError("owner_busy")
                try:
                    payload = getattr(self, verb)(**{key: value for key, value in arguments.items() if key != "cursor"})
                finally:
                    self._verification_lock.release()
            else:
                payload = getattr(self, verb)(**{key: value for key, value in arguments.items() if key != "cursor"})
            return self._page(operation, identity, caller_id, payload=payload)
        except PropagationJobError:
            raise
        except (PropagationIntentError, contract_api.PropagationContractError) as exc:
            raise PropagationJobError(exc.code) from None
        except Exception:
            raise PropagationJobError("owner_operation_unavailable") from None

    @staticmethod
    def _cancel_state(job):
        return "confirmed" if job["state"] in {"applied", "failed", "cancelled"} else "uncertain" if job["state"] == "recovery_required" else "requested"

    def _profile_binding(self, contract):
        value = contract.value
        if (value["execution_profile_ref"], value["execution_profile_digest"]) != (self.profile.profile_id, self.profile.profile_digest):
            raise PropagationJobError("profile_mismatch")

    def current_authority(self, intent_id, job_id, contract_digest, generation, target_id, resource_id):
        """Read the current approved reservation at a native write boundary."""
        contract = self._contract(intent_id, current=True)
        value = contract.value
        if (contract.digest != contract_digest or type(generation) is not int
                or value["generation"] != generation):
            raise PropagationJobError("stale_generation")
        if not any(target["target_id"] == target_id and resource_id in target["resource_keys"]
                   and target["effects"] for target in value["targets"]):
            raise PropagationJobError("resource_binding_mismatch")
        self.supervisor.observe(job_id)
        job = self.jobs.lookup_internal(job_id)
        if (job["intent_id"] != intent_id or job["contract_digest"] != contract.digest
                or job["state"] != "executing" or resource_id not in job["resources"]
                or job["canonical_contract"] != contract.canonical):
            raise PropagationJobError("stale_generation")
        return {"current": True, "observed_at": _stamp(self.profile.now())}

    def _contract(self, intent_id, *, current=False):
        parsed = contract_api.parse_contract(self.intents.contract_bytes(intent_id))
        self._profile_binding(parsed)
        if current:
            self.intents.assert_current(intent_id)
            contract_api.admit_contract(parsed.canonical, self.profile.approval_lookup,
                                       self.profile.active_identity(), self.profile.now())
        return parsed

    @staticmethod
    def _context(contract):
        return {"contract_digest": contract.digest, "target_set_digest": _hash(contract.value["targets"]),
                "target_count": len(contract.value["targets"])}

    def preview(self, intent_id):
        contract = self._contract(intent_id, current=True)
        observed = json.loads(_json(self.profile.preview(contract.canonical)))
        if set(observed) != {"observed_at", "targets"}:
            raise PropagationJobError("invalid_preview")
        now = self.profile.now()
        stamp = _fresh(observed["observed_at"], now)
        expected = sorted(contract.value["targets"], key=lambda row: row["target_id"])
        rows = observed["targets"]
        if type(rows) is not list or len(rows) != len(expected):
            raise PropagationJobError("target_set_mismatch")
        for row, target in zip(rows, expected):
            validate_schema_value(row, contract_api._preview_row(), "target")
            if any(row[key] != target[key] for key in ("target_id", "installation_id", "profile_id", "runtime_id")) or row["permitted_effects"] != target["effects"]:
                raise PropagationJobError("target_identity_mismatch")
            if row["ready"] != (row["pending_reason"] is None):
                raise PropagationJobError("invalid_preview")
            if row["observed_at"] is not None and contract_api._utc(row["observed_at"]) > now:
                raise PropagationJobError("stale_observation")
            if row["ready"]:
                _fresh(row["observed_at"], now)
                contract_api._digest(row["observed_digest"])
        expires = min(stamp + timedelta(minutes=5), contract_api._utc(contract.value["deadline_at"]))
        payload = {**self._context(contract), **observed, "effect_set_digest": contract.value["effect_set_digest"],
                   "expires_at": _stamp(expires), "all_required_ready": all(row["ready"] for row in rows)}
        digest = _hash(payload) if payload["all_required_ready"] else None
        payload["preview_digest"] = digest
        if digest:
            with self.intents._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM propagation_previews WHERE expires_at < ?", (_stamp(now),))
                db.execute("INSERT OR IGNORE INTO propagation_previews VALUES(?,?,?,?)", (digest, intent_id, _json(payload), payload["expires_at"]))
                self.intents._commit_if_within_capacity(db)
        return payload

    def submit(self, intent_id, preview_digest, operation_id):
        contract = self._contract(intent_id)
        if operation_id != _hash(["effect/v1", contract.digest, "fleet", "apply"]):
            raise PropagationJobError("operation_identity_mismatch")
        existing = self.jobs.lookup_by_intent(intent_id)
        if existing is not None:
            if (existing["contract_digest"], existing["operation_id"]) != (contract.digest, operation_id):
                raise PropagationJobError("job_conflict")
            original = {"contract_digest": contract.digest, "operation_id": operation_id,
                        "job_id": existing["job_id"], "state": "existing"}
            if existing.get("launch_phase") != "launch_pending":
                return original
        try:
            self._contract(intent_id, current=True)
        except (PropagationIntentError, contract_api.PropagationContractError):
            if existing is not None:
                return original  # Resolve custody without authorizing a new launch.
            raise
        with self.intents._connection() as db:
            row = db.execute("SELECT * FROM propagation_previews WHERE digest=? AND intent_id=?", (preview_digest, intent_id)).fetchone()
        if row is None or contract_api._utc(row["expires_at"]) <= self.profile.now():
            if existing is not None:
                return original
            raise PropagationJobError("preview_expired")
        previous = json.loads(row["payload"])
        # A fresh complete preview catches changed source/target bytes at admission.
        fresh = self.preview(intent_id)
        def compared(result):
            return [{k: v for k, v in target.items() if k != "observed_at"} for target in result["targets"]]
        if not fresh["all_required_ready"] or compared(fresh) != compared(previous):
            if existing is not None:
                return original
            raise PropagationJobError("preview_changed")
        value = contract.value
        binding = {"intent_id": intent_id, "operation_id": operation_id,
                   "canonical_contract": contract.canonical, "preview_digest": fresh["preview_digest"],
                   "profile_id": self.profile.profile_id, "profile_digest": self.profile.profile_digest,
                   "resources": sorted({key for target in value["targets"] for key in target["resource_keys"]}),
                   "deadline": value["deadline_at"]}
        job, duplicate = self.jobs.submit(binding, now=self.profile.now())
        if job.get("launch_phase") == "launch_pending":
            try:
                self.supervisor.launch(job["job_id"])
            except PropagationJobError as exc:
                if exc.code != "launch_reconciliation_required":
                    raise
                # Another submit may own launch custody. Return the durable job;
                # its status distinguishes running from uncertain execution.
        return {"contract_digest": job["contract_digest"], "operation_id": job["operation_id"],
                "job_id": job["job_id"], "state": "existing" if duplicate else "created"}

    def status(self, job_id):
        job = self.supervisor.observe(job_id)
        if job["state"] == "recovery_required" and self.supervisor.reconcile is not None:
            original = self.jobs.lookup_internal(job_id)
            identity = {key: original[key] for key in ("pid", "start_ticks", "boot_id")}
            result = self.jobs.completed_child_result(job_id, identity)
            if (result is not None and result["quiescent"] is True
                    and self.supervisor.reconcile(original, result) == "applied"):
                job = self.jobs.reconcile_applied(job_id, _hash(result))
        contract = self._contract(job["intent_id"])
        observation = self._observation(contract, job, "status")
        return {**self._context(contract), "observed_at": observation["observed_at"],
                "job_id": job_id, "state": job["state"] if job["state"] in {"applied", "failed", "cancelled", "recovery_required"} else "running", "heartbeat_at": job["heartbeat_at"] or job["created_at"],
                "completed_at": job["completed_at"], "outcomes": observation["outcomes"],
                "receipt_refs": observation["receipts"]}

    def intent_status(self, intent_id):
        """Keep owner identity and target custody visible without Temporal."""
        contract = self._contract(intent_id)
        value = contract.value
        job = self.jobs.lookup_by_intent(intent_id)
        if job is None:
            targets = [{**{key: target[key] for key in (
                "target_id", "installation_id", "profile_id", "runtime_id")},
                "check_set_digest": _hash(target["checks"]), "outcome": "pending",
                "applied": False, "verified": False, "desired_revision": value["revision"],
                "applied_revision": None, "verified_revision": None,
                "last_contact_at": None, "observed_at": None, "age_seconds": None,
                "freshness": "unknown", "pending_reason": "not-started",
                "session_state": {key: "pending" for key in (
                    "files", "new_session", "existing_session")},
                "metric_coverage": "pending", "receipt_ref": None,
            } for target in sorted(value["targets"], key=lambda row: row["target_id"])]
            state, job_id = "pending", None
        else:
            observed = self.status(job["job_id"])
            targets = observed["outcomes"]
            with self.intents._connection() as db:
                verification = db.execute(
                    "SELECT verified FROM propagation_verification_passes WHERE job_id=?",
                    (job["job_id"],)).fetchone()
            state = observed["state"] if observed["state"] in {
                "failed", "cancelled", "recovery_required"} else (
                    "completed" if observed["state"] == "applied" and verification is not None
                    and verification["verified"] and all(
                        row["verified"] for row in targets) else "running")
            job_id = job["job_id"]
        workflow_id = logical_workflow_id(value["scope"], value["revision"])
        progress, progress_at, progress_age = "unavailable", None, None
        if self.profile.workflow_status is not None:
            try:
                projection = self.profile.workflow_status(workflow_id, contract.digest)
                if (type(projection) is not dict or set(projection) != {"state", "observed_at"}
                        or projection["state"] not in {"pending", "running", "completed", "failed",
                                                   "cancelled", "recovery_required"}):
                    raise ValueError()
                observed = contract_api._utc(projection["observed_at"])
                age = int((self.profile.now() - observed).total_seconds())
                if age < 0:
                    raise ValueError()
                progress_at, progress_age = projection["observed_at"], age
                if age <= 300:
                    progress = projection["state"]
            except Exception:
                pass  # Owner custody remains readable when Temporal is unavailable.
        return {**self._context(contract), "intent_id": intent_id,
                "workflow_id": workflow_id,
                "job_id": job_id, "workflow_progress": progress,
                "workflow_observed_at": progress_at, "workflow_age_seconds": progress_age,
                "observed_at": _stamp(self.profile.now()), "state": state,
                "targets": targets, "all_targets_verified": bool(job) and all(
                    row["verified"] for row in targets)}

    def control_workflow(self, operation, arguments, caller_id):
        resume = operation == "propagation.resume.v1"
        contract = self._contract(arguments["intent_id"], current=resume)
        if arguments["expected_digest"] != contract.digest:
            raise PropagationJobError("intent_conflict")
        callback = self.profile.resume_workflow if resume else self.profile.cancel_workflow
        if callback is None:
            raise PropagationJobError("workflow_service_unavailable")
        workflow_id = logical_workflow_id(contract.value["scope"], contract.value["revision"])
        result = json.loads(_json(callback(workflow_id, arguments["intent_id"], contract.digest, caller_id)))
        validate_schema_value(result, contract_api._result_schema(operation), "workflow result")
        if resume:
            contract_api._id(result["attempt_id"])
        return result

    def _verification(self, intent_id, job, kind, verification_id=None):
        if job["intent_id"] != intent_id or job["state"] != "applied" or not job["completed_at"]:
            raise PropagationJobError("job_not_applied")
        self.intents.assert_current(intent_id)
        contract = self._contract(intent_id)
        ended = contract_api._utc(job["completed_at"])
        # Wait BEFORE observation, never restamp a cached read or invent future UTC.
        stop = time.monotonic() + 2
        while contract_api._utc(_stamp(self.profile.now())) <= ended:
            if time.monotonic() >= stop:
                raise PropagationJobError("observation_clock_not_advanced")
            time.sleep(.01)
        with self.intents._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT verification_id, verified FROM propagation_verification_passes WHERE job_id=?", (job["job_id"],)).fetchone()
            if row is None:
                if kind != "verify":
                    raise PropagationJobError("verification_not_found")
                expected = _hash(["convergence/v1", contract.digest, 1])
                db.execute("INSERT INTO propagation_verification_passes(job_id,verification_id) VALUES(?,?)", (job["job_id"], expected))
            else:
                expected = row["verification_id"]
                if kind == "convergence" and not row["verified"]:
                    raise PropagationJobError("verification_incomplete")
            self.intents._commit_if_within_capacity(db)
        if kind == "convergence" and expected != verification_id:
            raise PropagationJobError("verification_identity_mismatch")
        observed = self._observation(contract, job, kind, verification_id=expected, after=ended)
        if observed["changed"] or observed["reloads"]:
            raise PropagationJobError("verification_failed")
        if kind == "verify" and all(row["verified"] for row in observed["outcomes"]):
            with self.intents._connection() as db:
                db.execute("UPDATE propagation_verification_passes SET verified=1 WHERE job_id=? AND verification_id=?", (job["job_id"], expected))
        return {**self._context(contract), **observed, "job_id": job["job_id"],
                "verification_id": expected, "kind": kind,
                "all_targets_verified": all(row["verified"] for row in observed["outcomes"])}

    def verify(self, intent_id, job_id):
        return self._verification(intent_id, self.supervisor.observe(job_id), "verify")

    def convergence(self, intent_id, verification_id):
        with self.intents._connection() as db:
            row = db.execute("SELECT job_id FROM propagation_verification_passes WHERE verification_id=?", (verification_id,)).fetchone()
        if row is None:
            raise PropagationJobError("verification_not_found")
        return self._verification(intent_id, self.supervisor.observe(row["job_id"]), "convergence", verification_id)

    def _observation(self, contract, job, kind, *, verification_id=None, after=None):
        observed = json.loads(_json(self.profile.observe(contract.canonical, job, kind, verification_id)))
        bindings = {"contract_digest": contract.digest, "job_id": job["job_id"],
                    "operation_id": job["operation_id"], "kind": kind, "verification_id": verification_id}
        for key, expected in bindings.items():
            if key not in observed or observed.pop(key) != expected:
                raise PropagationJobError("observation_identity_mismatch")
        if set(observed) != {"observed_at", "changed", "reloads", "outcomes", "checks", "receipts"} or len(_json(observed)) > _MAX_SNAPSHOT:
            raise PropagationJobError("invalid_observation")
        now = self.profile.now()
        _fresh(observed["observed_at"], now, after=after)
        if any(type(observed[key]) is not int or observed[key] < 0 for key in ("changed", "reloads")):
            raise PropagationJobError("invalid_observation")
        expected = sorted(contract.value["targets"], key=lambda row: row["target_id"])
        rows = observed["outcomes"]
        if type(rows) is not list or len(rows) != len(expected):
            raise PropagationJobError("target_set_mismatch")
        required = [(target["target_id"], check) for target in expected for check in target["checks"]]
        checks = observed["checks"]
        if type(checks) is not list or [(row.get("target_id"), row.get("check_id")) for row in checks] != required:
            raise PropagationJobError("check_set_mismatch")
        for check in checks:
            validate_schema_value(check, contract_api._check_row(), "check")
            if check["observed_at"] is not None and contract_api._utc(check["observed_at"]) > now:
                raise PropagationJobError("stale_observation")
            if check["outcome"] != "success" and check["pending_reason"] is None:
                raise PropagationJobError("check_outcome_mismatch")
            if check["outcome"] == "success":
                if kind != "status":
                    _fresh(check["observed_at"], now, after=after)
                elif contract_api._utc(check["observed_at"]) > now:
                    raise PropagationJobError("stale_observation")
                contract_api._id(check["evidence_ref"])
                contract_api._digest(check["evidence_digest"])
                if check["pending_reason"] is not None:
                    raise PropagationJobError("check_outcome_mismatch")
        receipts = observed["receipts"]
        if type(receipts) is not list or len(receipts) > len(expected):
            raise PropagationJobError("receipt_set_mismatch")
        by_target = {receipt["target_id"]: receipt for receipt in receipts}
        if len(by_target) != len(receipts) or set(by_target) - {target["target_id"] for target in expected}:
            raise PropagationJobError("receipt_set_mismatch")
        refs = []
        for row, target in zip(rows, expected):
            validate_schema_value(row, contract_api._outcome_row(), "outcome")
            if any(row[key] != target[key] for key in ("target_id", "installation_id", "profile_id", "runtime_id")) or row["check_set_digest"] != _hash(target["checks"]) or row["desired_revision"] != contract.value["revision"]:
                raise PropagationJobError("target_identity_mismatch")
            target_checks = [check for check in checks if check["target_id"] == target["target_id"]]
            if row["verified"] and (not row["applied"] or row["pending_reason"] is not None or any(check["outcome"] != "success" for check in target_checks)):
                raise PropagationJobError("verification_failed")
            if row["applied"] and row["applied_revision"] != contract.value["revision"] or row["verified"] and row["verified_revision"] != contract.value["revision"]:
                raise PropagationJobError("revision_mismatch")
            if row["verified"]:
                if row["outcome"] != "success" or row["pending_reason"] is not None:
                    raise PropagationJobError("outcome_mismatch")
                if kind != "status":
                    _fresh(row["observed_at"], now, after=after)
                    if row["freshness"] != "fresh":
                        raise PropagationJobError("stale_observation")
                if row["session_state"]["files"] != "accepted" or row["session_state"]["new_session"] not in {"accepted", "not-required"} or row["session_state"]["existing_session"] not in {"accepted", "not-required"}:
                    raise PropagationJobError("session_acceptance_pending")
            elif row["observed_at"] is not None and contract_api._utc(row["observed_at"]) > now:
                raise PropagationJobError("stale_observation")
            if row["outcome"] != "success" and row["pending_reason"] is None:
                raise PropagationJobError("outcome_mismatch")
            if row["observed_at"] is None:
                if row["age_seconds"] is not None or row["freshness"] != "unknown":
                    raise PropagationJobError("invalid_freshness")
            else:
                # The reader reports age at its snapshot, before transport and
                # validation. Fresh verification above still uses the owner clock.
                age = int((contract_api._utc(observed["observed_at"])
                           - contract_api._utc(row["observed_at"])).total_seconds())
                if age < 0 or row["age_seconds"] != age or row["freshness"] != ("fresh" if age <= 300 else "stale"):
                    raise PropagationJobError("invalid_freshness")
            if row["last_contact_at"] is not None and contract_api._utc(row["last_contact_at"]) > now:
                raise PropagationJobError("stale_observation")
            receipt = by_target.get(target["target_id"])
            if row["applied"] and receipt is None:
                raise PropagationJobError("receipt_set_mismatch")
            if receipt is not None:
                context = contract_api.ReceiptContext(contract.digest, contract.value["revision"], contract.value["generation"], job["job_id"], job["operation_id"], target["target_id"], _hash(target["checks"]), contract_api.ReceiptIdentity(target["installation_id"], target["profile_id"], target["runtime_id"]))
                receipt_time = contract_api._utc(receipt["observed_at"])
                if receipt_time > now:
                    raise PropagationJobError("stale_receipt")
                if kind != "status":
                    _fresh(receipt["observed_at"], now, after=after)
                # Status may retain historical authenticated owner receipts;
                # their age remains explicit and cannot pass fresh verification.
                receipt = contract_api.parse_receipt(receipt, context=context, authenticated_issuer=self.profile.executor_issuer, now=receipt_time if kind == "status" else now)
                if (receipt["applied"], receipt["verified"], receipt["outcome"]) != (row["applied"], row["verified"], row["outcome"]):
                    raise PropagationJobError("receipt_outcome_mismatch")
                digest = _hash(receipt)
                ref = {"receipt_id": "receipt-" + digest, "target_id": target["target_id"], "effect_id": job["operation_id"], "issuer": self.profile.executor_issuer, "receipt_digest": digest}
                if row["receipt_ref"] not in (None, ref):
                    raise PropagationJobError("receipt_identity_mismatch")
                row["receipt_ref"] = ref
                refs.append(ref)
        observed["receipts"] = refs
        return observed

    @staticmethod
    def _render_page(payload, snapshot, offset):
        collections = {key: value for key, value in payload.items() if isinstance(value, list)}
        flat = [(key, row) for key, rows in sorted(collections.items()) for row in rows]
        if offset and offset >= len(flat):
            raise PropagationJobError("invalid_cursor")
        result = {key: value for key, value in payload.items() if key not in collections}
        result.update({key: [] for key in collections})
        end = min(offset + contract_api.MAX_PAGE_ITEMS, len(flat))
        for key, row in flat[offset:end]:
            result[key].append(row)
        result.update(snapshot_id=snapshot, total_items=len(flat), next_cursor=f"{snapshot}:{end}" if end < len(flat) else None)
        if len(_json(result)) > contract_api.MAX_RECEIPT_BYTES:
            raise PropagationJobError("page_too_large")
        return result

    def _page(self, operation, identity, principal, *, payload=None, cursor=None):
        now = self.profile.now()
        if cursor is None:
            snapshot = uuid.uuid4().hex
            if len(_json(payload)) > _MAX_SNAPSHOT:
                raise PropagationJobError("snapshot_too_large")
            count = sum(len(value) for value in payload.values() if isinstance(value, list))
            # Prove every page is deliverable before retaining the observation.
            for position in range(0, max(count, 1), contract_api.MAX_PAGE_ITEMS):
                self._render_page(payload, snapshot, position)
            with self.intents._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM propagation_snapshots WHERE expires_at <= ?", (_stamp(now),))
                db.execute("INSERT INTO propagation_snapshots VALUES(?,?,?,?,?,?)", (snapshot, principal, operation, identity, _json(payload), _stamp(now + timedelta(minutes=5))))
                self.intents._commit_if_within_capacity(db)
            offset = 0
        else:
            try:
                snapshot, raw_offset = cursor.split(":")
                offset = int(raw_offset)
                if offset < 0 or str(offset) != raw_offset:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise PropagationJobError("invalid_cursor") from None
            with self.intents._connection() as db:
                row = db.execute("SELECT * FROM propagation_snapshots WHERE id=?", (snapshot,)).fetchone()
            if row is None or (row["principal"], row["operation"], row["identity"]) != (principal, operation, identity) or contract_api._utc(row["expires_at"]) <= now:
                raise PropagationJobError("snapshot_unavailable")
            payload = json.loads(row["payload"])
        return self._render_page(payload, snapshot, offset)
