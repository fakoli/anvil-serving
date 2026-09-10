"""Exact-checkout Anvil State reads and task claims through its supported CLI."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..control_plane.mcp.runtime import _process_group_options, _terminate_process_tree
from ..observability.dashboard.contracts import ObservatoryError, digest, identifier
from .task_artifacts import TaskArtifacts


def run_bounded(argv, *, cwd, timeout=30, limit=4 * 1024 * 1024):
    """Drain bounded output without a shell, unbounded pipes, or ambient credentials."""
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "SYSTEMROOT") if key in os.environ}
    env.update(NO_COLOR="1", TERM="dumb")
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_process_group_options())
    output = bytearray()
    done = threading.Event()
    failures = []
    deadline = time.monotonic() + timeout

    def drain():
        try:
            while block := process.stdout.read1(min(65536, limit + 1)):
                remaining = limit - len(output)
                if len(block) > remaining:
                    raise ValueError("command output bound")
                output.extend(block)
        except (OSError, ValueError) as error:
            failures.append(error)
        finally:
            done.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        if not done.wait(max(0, deadline - time.monotonic())):
            raise TimeoutError()
        if failures:
            raise failures[0]
        try:
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise TimeoutError() from None
        if code:
            raise ObservatoryError("project_source_unavailable", "Anvil could not complete this operation. Check the project's State health and current claims.", 409)
        return bytes(output)
    finally:
        if process.poll() is None or reader.is_alive():
            _terminate_process_tree(process)
        reader.join(timeout=2)
        if not reader.is_alive():
            process.stdout.close()


class Projects:
    def __init__(self, config, store, access, *, run=run_bounded, artifacts=None):
        self.config, self.store, self.access, self.run = config, store, access, run
        self.artifacts = artifacts or TaskArtifacts()
        self.lock = threading.RLock()

    def configured(self, project_id):
        project = next((p for p in self.config.get("projects", []) if p["id"] == project_id), None)
        if not project:
            raise ObservatoryError("not_found", "This project is unavailable.", 404)
        return project

    def project(self, session, project_id, *, execute=False):
        project = self.configured(identifier(project_id))
        self.access.permit(session, project["resource_id"], "project.execute" if execute else None)
        return project

    def cli(self, project, *args):
        raw = self.run([project["anvil_binary"], *args, "--cwd", project["checkout"]], cwd=project["checkout"])
        # `packet --format json` emits a bounded file-notice before its JSON.
        if args[0] == "packet":
            raw = raw[raw.find(b"{"):]
        try:
            result = json.loads(raw)
        except (ValueError, TypeError):
            raise ObservatoryError("invalid_project_source", "Anvil returned an unsupported response.", 503) from None
        if result.get("ok") is False:
            raise ObservatoryError("project_source_unavailable", "Anvil State needs attention before this operation can proceed.", 409)
        return result.get("data", result)

    @staticmethod
    def public_project(project):
        return {key: project[key] for key in ("id", "label", "resource_id")}

    @staticmethod
    def packet_contract_digest(packet, prd):
        """Freeze the task and PRD contract, excluding mutable claim metadata."""
        task = dict(packet.get("task") or {})
        for key in ("status", "claims", "updated_at", "created_at"):
            task.pop(key, None)
        return digest({"task": task, "prd": prd})

    def read(self, session, project_id):
        project = self.project(session, project_id)
        status = self.cli(project, "status", "--json")
        prds = self.cli(project, "prd", "list", "--json")["prds"]
        tasks = self.cli(project, "list", "--open", "--json")["tasks"]
        keys = ("id", "title", "status", "priority", "feature_id", "dependencies", "acceptance_criteria")
        return {"project": self.public_project(project), "status": {key: status[key] for key in ("initialized", "project_name", "prd_status", "task_counts", "active_claims") if key in status},
                "prds": prds, "tasks": [{key: task[key] for key in keys if key in task} for task in tasks[:200]], "truncated": len(tasks) > 200}

    def task(self, session, project_id, task_id):
        project = self.project(session, project_id)
        return self.task_detail(project, identifier(task_id))

    def prd(self, session, project_id, prd_id):
        project = self.project(session, project_id)
        data = self.cli(project, "prd", "show", identifier(prd_id), "--json", "--limit", "2097152")
        if (data.get("schema_id") != "anvil.state.prd-content.v1"
                or type(data.get("content")) is not str
                or len(data["content"].encode("utf-8")) > 2097152):
            raise ObservatoryError("invalid_project_source", "Anvil returned an unsupported plan document.", 503)
        return {key: data[key] for key in ("content", "source_digest", "prd_revision") if key in data}

    def task_detail(self, project, task_id):
        data = self.cli(project, "show", task_id, "--json")
        task = data["task"]
        prd_id = task_id.rsplit(":", 1)[0] if ":" in task_id else "default"
        prds = self.cli(project, "prd", "list", "--json")["prds"]
        prd = next((p for p in prds if p["id"] == prd_id), {})
        packet = self.cli(project, "packet", task_id, "--format", "json")
        reason = None
        if prd.get("status") not in {"reviewed", "approved"}:
            reason = "The owning PRD must be reviewed or approved."
        elif task.get("status") != "ready":
            reason = "Only a ready task can acquire a new execution lease."
        elif packet.get("dependencies_open"):
            reason = "Complete this task's dependencies first."
        elif data.get("active_claims"):
            reason = "This task already has an active claim."
        return {"task": task, "active_claims": data.get("active_claims", []), "prd": prd,
                "packet": packet, "execution": {"ready": reason is None, "reason": reason}}

    def prepare(self, session, body):
        project = self.project(session, body["project_id"], execute=True)
        task_id = identifier(body["task_id"])
        key = identifier(body["request_id"])
        owner = session.principal.identity
        request_digest = digest(body)
        with self.lock:
            try:
                old = self.store.get("task-binding", owner, key)
            except ObservatoryError as error:
                if error.status != 404:
                    raise
                old = None
            if old:
                if old["request_digest"] != request_digest:
                    raise ObservatoryError("request_conflict", "This start key belongs to a different task request.", 409)
                if old["status"] != "ready":
                    raise ObservatoryError("start_uncertain", "The previous start needs reconciliation. Its claim will not be duplicated.", 409)
                return old
            if not project.get("runner_root"):
                raise ObservatoryError("runner_unavailable", "This project has no isolated runner storage configured.", 409)
            detail = self.task_detail(project, task_id)
            if not detail["execution"]["ready"]:
                raise ObservatoryError("task_not_ready", detail["execution"]["reason"], 409)
            actor = "workbench-" + hashlib.sha256(owner.encode()).hexdigest()[:16]
            row = {"id": key, "project_id": project["id"], "task_id": task_id, "owner": owner, "actor": actor, "request_digest": request_digest,
                   "packet_digest": self.packet_contract_digest(detail["packet"], detail["prd"]), "status": "claiming", "created_at": time.time(), "provider_id": body["provider_id"], "model_id": body["model_id"],
                   "thinking_level": body["thinking_level"]}
            self.store.put("task-binding", owner, key, row)
            # Anvil creates its own isolated claim worktree; the shared checkout is never switched.
            try:
                claimed = self.cli(project, "claim", task_id, "--worktree", "--branch", "workbench/" + uuid.uuid4().hex,
                                   "--actor", actor, "--lease", "240", "--json")
                claim = claimed["claim"]
                row.update(lease_id=claim["id"], claim_worktree=claimed.get("worktree"), claim_branch=claimed.get("branch"),
                           lease_expires_at=claim["lease_expires_at"], status="provisioning")
                self.store.put("task-binding", owner, key, row)
                claim_path = row["claim_worktree"]
                if not claim_path or not Path(claim_path).is_absolute():
                    raise ObservatoryError("runner_unavailable", "Anvil did not return an isolated claim workspace.", 409)
                root = Path(project["runner_root"])
                runner_checkout, verification_checkout, baseline, artifact_root = self.artifacts.provision(claim_path, str(root), key)
                row.update(runner_checkout=runner_checkout, verification_checkout=verification_checkout, baseline_sha=baseline, artifact_root=artifact_root)
                self.store.put("task-binding", owner, key, row)
                packet = self.cli(project, "packet", task_id, "--format", "json")
                fresh_prds = self.cli(project, "prd", "list", "--json")["prds"]
                fresh_prd = next((item for item in fresh_prds if item["id"] == detail["prd"].get("id")), None)
                if fresh_prd is None or self.packet_contract_digest(packet, fresh_prd) != row["packet_digest"]:
                    raise ObservatoryError("packet_changed", "The task or PRD contract changed while the claim was prepared. The claim was released.", 409)
                row.update(verification_commands=packet.get("task", {}).get("verification", {}).get("commands", []),
                           declared_paths=packet.get("task", {}).get("likely_files", []), status="ready")
                self.store.put("task-binding", owner, key, row)
                return row
            except Exception:
                # A lost claim response is reconciled through State before
                # compensation. No database edits and no blind second claim.
                try:
                    self._release_preparation(project, row)
                except Exception:
                    row.update(status="cleanup_required", error="Claim cleanup needs reconciliation through Anvil.")
                self.store.put("task-binding", owner, key, row)
                raise ObservatoryError("preparation_failed", "The isolated task workspace could not be prepared. Review its retained cleanup state before retrying.", 409) from None

    def _release_preparation(self, project, row):
        data = self.cli(project, "show", row["task_id"], "--json")
        claims = [c for c in data.get("active_claims", []) if c.get("claimed_by") == row["actor"]]
        if row.get("lease_id"):
            claims = [c for c in claims if c["id"] == row["lease_id"]]
        if len(claims) > 1:
            raise ObservatoryError("claim_ambiguous", "Resolve the current task claim through Anvil.", 409)
        if claims:
            claim = claims[0]
            row.update(lease_id=claim["id"], claim_worktree=claim.get("worktree_path"))
            self.store.put("task-binding", row["owner"], row["id"], row)
            self.cli(project, "release", claim["id"], "--actor", row["actor"], "--reason", "Workbench workspace preparation or execution closed; artifacts retained", "--json")
        # Only a clean, owned worktree can be removed. Dirty work is retained.
        if row.get("claim_worktree") and Path(row["claim_worktree"]).is_dir():
            try:
                self.run(["git", "worktree", "remove", "--", row["claim_worktree"]], cwd=project["checkout"])
                row["worktree_cleanup"] = "removed_clean_worktree"
            except Exception:
                row["worktree_cleanup"] = "retained_for_review"
        row.update(status="released", error=None)

    def release(self, session, key):
        row = self.binding(session, key, execute=True)
        with self.lock:
            # A mounted Pi workspace must be stopped before lease cleanup can
            # remove or retain its paired review worktree.
            self.artifacts.ensure_quiescent(row)
            self._release_preparation(self.configured(row["project_id"]), row)
            self.store.put("task-binding", row["owner"], row["id"], row)
        return {"binding_id": row["id"], "status": row["status"], "worktree_cleanup": row.get("worktree_cleanup", "not_created")}

    def binding(self, session, key, *, execute=False):
        row = self.store.get("task-binding", session.principal.identity, identifier(key))
        self.project(session, row["project_id"], execute=execute)
        return row

    def validate_pi_binding(self, binding):
        """Server timer and Pi routes verify the exact live lease independently of browser state."""
        project = self.configured(binding.project_id)
        data = self.cli(project, "show", binding.task_id, "--json")
        expected_actor = "workbench-" + hashlib.sha256(binding.principal_id.encode()).hexdigest()[:16]
        claim = next((c for c in data.get("active_claims", []) if c.get("id") == binding.lease_id and c.get("claimed_by") == expected_actor), None)
        if not claim:
            raise ObservatoryError("lease_lost", "The task lease is no longer owned by this session.", 409)
        expiry = datetime.fromisoformat(claim["lease_expires_at"].replace("Z", "+00:00"))
        if expiry <= datetime.now(timezone.utc):
            raise ObservatoryError("lease_expired", "The task lease expired. Recover ownership before continuing.", 409)
        # Anvil renewal is progress-gated. A no-op is never described as a
        # renewed lease; the original expiry remains authoritative.
        row = self.binding_for_pi(binding)
        if expiry - datetime.now(timezone.utc) < timedelta(minutes=60) and time.time() - row.get("last_renew_attempt", 0) > 60:
            row["last_renew_attempt"] = time.time()
            renewal = self.cli(project, "renew", binding.lease_id, "--actor", expected_actor, "--lease", "240", "--json")
            row["lease_renewed"] = renewal.get("renewed") is True
            row["lease_expires_at"] = renewal.get("claim", claim)["lease_expires_at"]
            self.store.put("task-binding", row["owner"], row["id"], row)

    def pi_binding(self, row):
        from .pi_sessions import PiTaskBinding
        return PiTaskBinding(principal_id=row["owner"], project_id=row["project_id"], task_id=row["task_id"],
                             lease_id=row["lease_id"], runner_id=self.config["pi"]["id"], provider_id=row["provider_id"])

    def binding_for_pi(self, binding):
        for row in self.store.list("task-binding", binding.principal_id):
            if row.get("lease_id") == binding.lease_id and row["task_id"] == binding.task_id and row["project_id"] == binding.project_id:
                return row
        raise ObservatoryError("not_found", "This task runner binding is unavailable.", 404)

    def evidence(self, session, binding_id):
        row = self.binding(session, binding_id)
        artifact = None
        verification = row.get("verification")
        if row.get("artifact_digest") and row.get("artifact_root"):
            artifact = self.artifacts.load(row, row["artifact_digest"])
        if verification and row.get("artifact_root"):
            verification = self._read_verification(row, verification["artifact_digest"])
        return {"binding_id": binding_id, "source": "isolated-sandbox", "observed_at": time.time(), "artifact": artifact,
                "acceptance": "independent_review_required", "packet_digest": row["packet_digest"], "baseline_sha": row.get("baseline_sha"),
                "status": row.get("status"), "worktree_cleanup": row.get("worktree_cleanup"), "verification": verification,
                "submission": row.get("submission"), "binding": asdict(self.pi_binding(row)) if row.get("lease_id") else None}

    def review_evidence(self, session, binding_id):
        row = self.binding(session, binding_id, execute=True)
        if row.get("status") not in {"ready", "evidence_reviewed", "verified", "verification_failed"}:
            raise ObservatoryError("evidence_unavailable", "This task binding is not available for evidence review.", 409)
        preview = self.artifacts.capture(row)
        row.update(artifact_digest=preview["artifact_digest"], status="evidence_reviewed", verification=None, submission=None)
        self.store.put("task-binding", row["owner"], row["id"], row)
        return preview

    def verify_evidence(self, session, binding_id, artifact_digest):
        row = self.binding(session, binding_id, execute=True)
        if row.get("artifact_digest") != artifact_digest:
            raise ObservatoryError("artifact_stale", "Verify the exact reviewed patch shown in the evidence preview.", 409)
        if (row.get("verification") or {}).get("artifact_digest") == artifact_digest:
            return self._read_verification(row, artifact_digest)
        intent = row.get("transfer") or {}
        already_transferred = False
        if intent:
            if intent.get("artifact_digest") != artifact_digest:
                raise ObservatoryError("transfer_recovery_required", "A different reviewed patch has an unfinished transfer intent. Reconcile it before continuing.", 409)
            state = self.artifacts.transfer_state(row, artifact_digest)
            if state == "other":
                raise ObservatoryError("transfer_recovery_required", "The claimed workspace differs from both baseline and the reviewed patch. Recover it outside Workbench.", 409)
            already_transferred = state == "exact"
        else:
            row["transfer"] = {"artifact_digest": artifact_digest, "status": "intent", "recorded_at": time.time()}
            self.store.put("task-binding", row["owner"], row["id"], row)
        evidence = self.artifacts.verify(row, artifact_digest, already_transferred=already_transferred)
        row.update(status="verified" if evidence["passed"] else "verification_failed", verification={"artifact_digest": artifact_digest, "passed": evidence["passed"],
                   "commands": [{key: value for key, value in item.items() if key not in {"stdout", "stderr"}} for item in evidence["commands"]]}, transfer=None)
        self.store.put("task-binding", row["owner"], row["id"], row)
        return evidence

    def submit_evidence(self, session, binding_id, artifact_digest):
        row = self.binding(session, binding_id, execute=True)
        verification = row.get("verification") or {}
        if verification.get("artifact_digest") != artifact_digest:
            raise ObservatoryError("evidence_unverified", "Run the frozen task verification for this reviewed patch before submission.", 409)
        if verification.get("passed") is not True:
            raise ObservatoryError("evidence_failed", "The frozen task verification failed; retain the evidence and correct the task before submission.", 409)
        if row.get("submission"):
            if row["submission"].get("status") == "outcome_unknown":
                raise ObservatoryError("submission_recovery_required", "Evidence submission outcome is unknown. Reconcile the Anvil task record before retrying; Workbench will not submit it twice.", 409)
            return row["submission"]
        preview = self.artifacts.load(row, artifact_digest)
        output = Path(row["artifact_root"]) / (artifact_digest + ".verification.json")
        if not output.is_file():
            raise ObservatoryError("evidence_unverified", "The isolated verification record is unavailable.", 409)
        retained = self._read_verification(row, artifact_digest)
        if retained.get("artifact_digest") != artifact_digest or retained.get("packet_digest") != row["packet_digest"] or retained.get("baseline_sha") != row["baseline_sha"] or retained.get("passed") is not True:
            raise ObservatoryError("evidence_tampered", "The retained verification record does not match this reviewed task evidence.", 409)
        commands = tuple(row.get("verification_commands", ()))
        if not commands:
            raise ObservatoryError("verification_contract_invalid", "This task has no declared verification commands to submit.", 409)
        project = self.configured(row["project_id"])
        argv = ["submit", row["task_id"]]
        for command in commands:
            argv.extend(("--commands", command))
        for item in preview["files"]:
            argv.extend(("--files-changed", item["path"]))
        argv.extend(("--output-file", str(output), "--actor", row["actor"], "--json"))
        # Persist before dispatch because a timeout or process interruption can
        # happen after Anvil records evidence.  There is no safe CLI evidence
        # lookup keyed by this private artifact digest, so retries fail closed.
        row.update(status="submission_uncertain", submission={"artifact_digest": artifact_digest, "status": "outcome_unknown",
                   "acceptance": "independent_review_required"})
        self.store.put("task-binding", row["owner"], row["id"], row)
        result = self.cli(project, *argv)
        row.update(status="submitted", submission={"artifact_digest": artifact_digest, "state": result,
                   "acceptance": "independent_review_required"})
        self.store.put("task-binding", row["owner"], row["id"], row)
        return row["submission"]

    def _read_verification(self, row, artifact_digest):
        path = Path(row["artifact_root"]) / (artifact_digest + ".verification.json")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ObservatoryError("evidence_unverified", "The isolated verification record is unavailable.", 409) from None
