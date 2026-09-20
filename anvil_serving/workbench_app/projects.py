"""Exact-checkout Anvil State reads and task claims through its supported CLI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..control_plane.mcp.runtime import _process_group_options, _terminate_process_tree
from ..observability.dashboard.contracts import ObservatoryError, digest, identifier, strict_json
from .task_artifacts import TaskArtifacts


_LOCAL_OWNER_ID = "local-owner"
_LOCAL_RUNTIME_ID = "local-runtime"
_MAX_CONTEXT_FILES = 200
_MAX_CONTEXT_BYTES = 512 * 1024
_MAX_CONTEXT_FILE_BYTES = 64 * 1024
_MAX_CONTEXT_DEPTH = 16
_MAX_CONTEXT_INSPECTED = 800
_MAX_CONTEXT_DIRECTORIES = 80
_CONTEXT_DEADLINE_SECONDS = 2.0
_SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".kdbx")


class BoundedCommandFailure(ObservatoryError):
    """A bounded command failure whose stdout is private transport data."""

    def __init__(self, stdout):
        super().__init__("project_source_unavailable", "Anvil could not complete this operation.", 409)
        self.stdout = stdout


_READ_ERROR_MESSAGES = {
    "projection_not_converged": ("project_projection_not_converged", "The project State projection is inconsistent. An operator must inspect State health before this plan can be read.", 409),
    "state_unavailable": ("project_state_unavailable", "The project State source is unavailable. Retry after it is restored.", 503),
    "schema_incompatible": ("project_state_incompatible", "The project State schema is incompatible with this reader.", 409),
    "limit_exceeded": ("project_source_too_large", "The persisted plan exceeds the configured display limit.", 413),
    "prd_not_found": ("not_found", "The requested project plan is unavailable.", 404),
    "content_unavailable": ("project_source_unavailable", "The persisted project plan is unavailable.", 409),
    "source_drift": ("project_source_unavailable", "The persisted project plan binding is inconsistent.", 409),
    "invalid_utf8": ("invalid_project_source", "The persisted project plan is not valid UTF-8 text.", 503),
}
_GENERIC_ERROR_MESSAGES = {
    "permission_denied": ("project_permission_denied", "You do not have permission to read this project plan.", 403),
    "unauthorized": ("project_permission_denied", "You do not have permission to read this project plan.", 403),
    "forbidden": ("project_permission_denied", "You do not have permission to read this project plan.", 403),
    "missing_capability": ("project_read_unsupported", "This configured Anvil source does not support plan reads.", 503),
    "capability_unavailable": ("project_read_unsupported", "This configured Anvil source does not support plan reads.", 503),
    "unknown_command": ("project_read_unsupported", "This configured Anvil source does not support plan reads.", 503),
}


def project_read_error(raw):
    """Translate only bounded, schema-known failures to public messages."""
    fallback = ObservatoryError("project_source_unavailable", "Anvil could not complete this read.", 409)
    if type(raw) is not bytes or len(raw) > 4 * 1024 * 1024:
        return fallback
    try:
        envelope = strict_json(raw)
    except ObservatoryError:
        return fallback
    if type(envelope) is not dict or envelope.get("ok") is not False:
        return fallback
    error = envelope.get("error")
    if type(error) is not dict or type(error.get("code")) is not str:
        return fallback
    mapped = (_READ_ERROR_MESSAGES if error.get("schema_id") == "anvil.state.read-error.v1"
              else _GENERIC_ERROR_MESSAGES).get(error["code"])
    return ObservatoryError(*mapped) if mapped else fallback


def _command_error(args, raw):
    read_only = (args[0] in {"status", "list", "show", "packet"}
                 or args[:2] in {("prd", "list"), ("prd", "show")})
    if read_only:
        return project_read_error(raw)
    return ObservatoryError("project_source_unavailable", "Anvil could not complete this operation. Check its current task and claim state before retrying.", 409)


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
            raise BoundedCommandFailure(bytes(output))
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
        try:
            raw = self.run([project["anvil_binary"], *args, "--cwd", project["checkout"]], cwd=project["checkout"])
        except BoundedCommandFailure as failure:
            raise _command_error(args, failure.stdout) from None
        # `packet --format json` emits a bounded file-notice before its JSON.
        if args[0] == "packet":
            raw = raw[raw.find(b"{"):]
        try:
            result = strict_json(raw)
        except ObservatoryError:
            raise ObservatoryError("invalid_project_source", "Anvil returned an unsupported response.", 503) from None
        if type(result) is not dict:
            raise ObservatoryError("invalid_project_source", "Anvil returned an unsupported response.", 503)
        if result.get("ok") is False:
            raise _command_error(args, raw)
        data = result.get("data", result)
        if type(data) is not dict:
            raise ObservatoryError("invalid_project_source", "Anvil returned an unsupported response.", 503)
        return data

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
                or len(data["content"].encode("utf-8")) > 2097152
                or type(data.get("source_digest")) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", data["source_digest"])
                or type(data.get("prd_revision")) is not int
                or data["prd_revision"] < 1):
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
                    if old.get("root_request") and old.get("root_binding", {}).get("multi_root"):
                        return self._recover_root_preparation(project, old)
                    raise ObservatoryError("start_uncertain", "The previous start needs reconciliation. Its claim will not be duplicated.", 409)
                return old
            if not project.get("runner_root"):
                raise ObservatoryError("runner_unavailable", "This project has no isolated runner storage configured.", 409)
            root_binding = self._freeze_task_roots(project, body)
            detail = self.task_detail(project, task_id)
            if not detail["execution"]["ready"]:
                raise ObservatoryError("task_not_ready", detail["execution"]["reason"], 409)
            actor = "workbench-" + hashlib.sha256(owner.encode()).hexdigest()[:16]
            row = {"id": key, "project_id": project["id"], "task_id": task_id, "owner": owner, "actor": actor, "request_digest": request_digest,
                   "packet_digest": self.packet_contract_digest(detail["packet"], detail["prd"]), "status": "claiming", "created_at": time.time(), "provider_id": body["provider_id"], "model_id": body["model_id"],
                   "thinking_level": body["thinking_level"], "root_binding": root_binding,
                   "task_verification_commands": detail["packet"].get("task", {}).get("verification", {}).get("commands", []),
                   "task_declared_paths": detail["packet"].get("task", {}).get("likely_files", []),
                   "prd_id": detail["prd"].get("id")}
            self.store.put("task-binding", owner, key, row)
            # Anvil creates its own isolated claim worktree; the shared checkout is never switched.
            try:
                self._materialize_context_snapshots(row, project["runner_root"])
                row["root_binding_digest"] = digest(row["root_binding"])
                self.store.put("task-binding", owner, key, row)
                if root_binding.get("multi_root"):
                    request_path = self._root_request(row, project["runner_root"])
                    lookup = self.cli(project, "roots", "request-digest", task_id, "--request-file", str(request_path), "--actor", actor, "--json")
                    row["root_request"] = {"path": str(request_path), **lookup}
                    self.store.put("task-binding", owner, key, row)
                    claimed = self.cli(project, "roots", "claim", task_id, "--request-file", str(request_path), "--actor", actor, "--lease", "240", "--json")
                    self._record_root_claim(row, claimed)
                    self._provision_root_claim(project, row)
                else:
                    claimed = self.cli(project, "claim", task_id, "--worktree", "--branch", "workbench/" + uuid.uuid4().hex,
                                       "--actor", actor, "--lease", "240", "--json")
                    claim = claimed["claim"]
                    row.update(lease_id=claim["id"], claim_worktree=claimed.get("worktree"), claim_branch=claimed.get("branch"),
                               lease_expires_at=claim["lease_expires_at"], status="provisioning")
                self.store.put("task-binding", owner, key, row)
                claim_path = row["claim_worktree"]
                if not claim_path or not Path(claim_path).is_absolute():
                    raise ObservatoryError("runner_unavailable", "Anvil did not return an isolated claim workspace.", 409)
                if not root_binding.get("multi_root"):
                    root = Path(project["runner_root"])
                    runner_checkout, verification_checkout, baseline, artifact_root = self.artifacts.provision(claim_path, str(root), key)
                    row.update(runner_checkout=runner_checkout, verification_checkout=verification_checkout, baseline_sha=baseline, artifact_root=artifact_root)
                self.store.put("task-binding", owner, key, row)
                return self._finalize_preparation(project, row)
            except Exception:
                # A lost claim response is reconciled through State before
                # compensation. A root-set may already own every repository or
                # have one private clone completed, so retain it unchanged for
                # the exact original-request retry; never release or re-claim.
                if row.get("root_request") and root_binding.get("multi_root"):
                    row.update(status="provisioning", error="The retained root-set request needs reconciliation.")
                    self.store.put("task-binding", owner, key, row)
                    raise ObservatoryError("preparation_failed", "The isolated task workspace could not be prepared. Review its retained cleanup state before retrying.", 409) from None
                try:
                    self._release_preparation(project, row)
                except Exception:
                    row.update(status="cleanup_required", error="Claim cleanup needs reconciliation through Anvil.")
                self.store.put("task-binding", owner, key, row)
                raise ObservatoryError("preparation_failed", "The isolated task workspace could not be prepared. Review its retained cleanup state before retrying.", 409) from None

    def _freeze_task_roots(self, project, body=None):
        """Materialize the immutable, single-writer task root set before claim.

        T03c deliberately accepts only the legacy State checkout as the writable
        claim root.  Secondary roots become private read-only context snapshots;
        their shared trees and Git metadata are never container mounts.
        """
        roots = project.get("roots") or [{
            "id": "primary", "label": project["label"], "owner_id": _LOCAL_OWNER_ID,
            "runtime_id": _LOCAL_RUNTIME_ID, "task_access": "read-write", "path": project["checkout"],
        }]
        if not isinstance(roots, list) or not roots:
            raise ObservatoryError("project_root_invalid", "This project has no declared task roots.", 409)
        try:
            body = body or {}
            primary_id = identifier(body.get("primary_root_id", project.get("primary_root_id", "primary")))
            selected_writable = body.get("writable_root_ids", ())
            if type(selected_writable) not in (list, tuple) or any(type(item) is not str for item in selected_writable) or len(set(selected_writable)) != len(selected_writable):
                raise ObservatoryError("project_root_invalid", "Writable root selection is invalid.", 409)
        except ObservatoryError:
            raise ObservatoryError("project_root_invalid", "This project primary root is invalid.", 409)
        declared = []
        seen_ids, canonical_paths = set(), []
        checkout, _ = self._canonical_root(project["checkout"])
        for item in roots:
            if not isinstance(item, dict):
                raise ObservatoryError("project_root_invalid", "This project root declaration is invalid.", 409)
            try:
                root_id = identifier(item.get("id"))
            except ObservatoryError:
                root_id = ""
            if not root_id or root_id.casefold() in {value.casefold() for value in seen_ids}:
                raise ObservatoryError("project_root_invalid", "This project root declaration is invalid.", 409)
            seen_ids.add(root_id)
            if item.get("owner_id") != _LOCAL_OWNER_ID or item.get("runtime_id") != _LOCAL_RUNTIME_ID:
                raise ObservatoryError("project_root_unsupported", "This task root owner is not available for isolated execution.", 409)
            access = item.get("task_access")
            if access not in {"read-only", "read-write"}:
                raise ObservatoryError("project_root_invalid", "This project root access declaration is invalid.", 409)
            canonical, root_identity = self._canonical_root(item.get("path"))
            if any(self._paths_overlap(canonical, previous) for previous in canonical_paths):
                raise ObservatoryError("project_root_invalid", "Declared task roots overlap or alias one another.", 409)
            canonical_paths.append(canonical)
            declared.append({"id": root_id, "owner_id": item["owner_id"], "runtime_id": item["runtime_id"],
                             "task_access": access, "repository_id": item.get("repository_id"), "verification_commands": list(item.get("verification_commands", [])),
                             "expected_files": list(item.get("expected_files", [])), "canonical_path": str(canonical), "source_identity": root_identity})
        primary = next((item for item in declared if item["id"] == primary_id), None)
        claim_root = next((item for item in declared if Path(item["canonical_path"]) == checkout), None)
        if primary is None or claim_root is None:
            raise ObservatoryError("project_root_invalid", "The project task root set is incomplete.", 409)
        if any(root_id not in seen_ids for root_id in selected_writable) or (primary_id != claim_root["id"] and primary_id not in selected_writable):
            raise ObservatoryError("project_root_invalid", "Writable root selection does not match the declared primary root.", 409)
        if primary["task_access"] != "read-write":
            raise ObservatoryError("primary_root_unsupported", "The selected primary root is not writable for this task.", 409)
        if claim_root["task_access"] != "read-write":
            raise ObservatoryError("claim_root_unsupported", "The canonical State checkout must remain this task's writable claim root.", 409)
        for item in declared:
            item["source_path"] = item["canonical_path"]
            selected = item["id"] == claim_root["id"] or item["id"] in selected_writable
            if selected:
                if item["task_access"] != "read-write" or (selected_writable and not item["repository_id"]):
                    raise ObservatoryError("secondary_write_unsupported", "Writable roots require a declared owner repository identity.", 409)
                if item["id"] != claim_root["id"] and (not item["expected_files"] or not item["verification_commands"]):
                    raise ObservatoryError("secondary_write_unsupported", "Writable secondary roots need declared file scope and verification commands.", 409)
                item["mount"] = "workspace"
                continue
            item["mount"] = "context-read-only"
        for item in declared:
            item.pop("canonical_path")
        return {
            "version": 1,
            "primary_root_id": primary_id,
            "claim_root_id": claim_root["id"],
            "cwd_root_id": primary_id,
            "roots": declared,
            "multi_root": len([item for item in declared if item["mount"] == "workspace"]) > 1,
        }

    def _root_request(self, row, runner_root):
        roots = []
        for item in row["root_binding"]["roots"]:
            if item["mount"] != "workspace":
                continue
            commands = row["task_verification_commands"] if item["id"] == row["root_binding"]["claim_root_id"] else item["verification_commands"]
            roots.append({"root_id": item["id"], "repository_id": item["repository_id"], "path": item["source_path"],
                          "expected_files": row["task_declared_paths"] if item["id"] == row["root_binding"]["claim_root_id"] else item["expected_files"],
                          "verification_commands": commands})
        request = {"schema": "anvil.root-set-request/v1", "request_id": row["id"], "primary_root_id": row["root_binding"]["claim_root_id"], "roots": roots}
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        parent = Path(runner_root) / ".workbench-root-requests"
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink():
            raise ObservatoryError("runner_unavailable", "Private root request storage is unavailable.", 409)
        name = digest({"owner": row["owner"], "project_id": row["project_id"], "request_id": row["id"]}) + ".json"
        path = parent / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            try:
                descriptor = os.open(path, read_flags)
                info = os.fstat(descriptor)
                existing = os.read(descriptor, len(encoded) + 1)
            except OSError as error:
                raise ObservatoryError("task_root_binding_lost", "The retained root request is unavailable.", 409) from error
            finally:
                try:
                    os.close(descriptor)
                except (OSError, UnboundLocalError):
                    pass
            if not stat.S_ISREG(info.st_mode) or existing != encoded:
                raise ObservatoryError("task_root_binding_lost", "The retained root request does not match this start.", 409)
            return path
        try:
            offset = 0
            while offset < len(encoded):
                offset += os.write(descriptor, encoded[offset:])
            os.fsync(descriptor)
        except OSError as error:
            raise ObservatoryError("runner_unavailable", "Private root request storage is unavailable.", 409) from error
        finally:
            os.close(descriptor)
        return path

    def _root_request_identity(self, project, row):
        request = row.get("root_request")
        if not isinstance(request, dict) or not isinstance(request.get("path"), str):
            raise ObservatoryError("task_root_binding_lost", "The original root-set request is unavailable.", 409)
        path = Path(request["path"])
        if not path.is_file():
            raise ObservatoryError("task_root_binding_lost", "The original root-set request is unavailable.", 409)
        derived = self.cli(project, "roots", "request-digest", row["task_id"], "--request-file", str(path),
                           "--actor", row["actor"], "--json")
        if (derived.get("request_id") != row["id"]
                or derived.get("request_id") != request.get("request_id")
                or derived.get("request_digest") != request.get("request_digest")):
            raise ObservatoryError("task_root_binding_lost", "The original root-set request no longer matches this task binding.", 409)
        return derived

    def _root_facts(self, row, response):
        if not isinstance(response, dict) or response.get("status") != "ready" or response.get("request_id") != row["id"]:
            raise ObservatoryError("task_root_binding_lost", "The owner returned a different root-set claim.", 409)
        request = row.get("root_request") or {}
        if response.get("request_digest") != request.get("request_digest"):
            raise ObservatoryError("task_root_binding_lost", "The owner returned a different root-set claim.", 409)
        roots = response.get("roots")
        selected = [item for item in row["root_binding"]["roots"] if item["mount"] == "workspace"]
        expected = {(item["id"], item["repository_id"]): item for item in selected}
        if not isinstance(roots, list) or len(roots) != len(expected):
            raise ObservatoryError("task_root_binding_lost", "The owner root-set facts are incomplete.", 409)
        facts = {}
        worktrees = []
        required = ("root_id", "repository_id", "canonical_root", "claim_worktree", "branch", "baseline_sha", "verification_commands", "state")
        for fact in roots:
            if not isinstance(fact, dict) or any(field not in fact for field in required):
                raise ObservatoryError("task_root_binding_lost", "The owner root-set facts are incomplete.", 409)
            pair = (fact["root_id"], fact["repository_id"])
            declared = expected.get(pair)
            if (declared is None or pair in facts or fact["state"] != "prepared"
                    or not isinstance(fact["claim_worktree"], str) or not Path(fact["claim_worktree"]).is_absolute()
                    or not isinstance(fact["canonical_root"], str) or not Path(fact["canonical_root"]).is_absolute()
                    or not isinstance(fact["branch"], str) or not fact["branch"]):
                raise ObservatoryError("task_root_binding_lost", "The owner root-set facts do not match this task binding.", 409)
            canonical, worktree = Path(fact["canonical_root"]).resolve(), Path(fact["claim_worktree"]).resolve()
            if (canonical != Path(declared["source_path"])
                    or any(self._paths_overlap(worktree, Path(item["source_path"])) for item in selected)
                    or any(self._paths_overlap(worktree, prior) for prior in worktrees)):
                raise ObservatoryError("task_root_binding_lost", "The owner root workspaces do not match their declared repositories.", 409)
            worktrees.append(worktree)
            commands = row["task_verification_commands"] if fact["root_id"] == row["root_binding"]["claim_root_id"] else declared["verification_commands"]
            if fact["verification_commands"] != commands or not isinstance(fact["baseline_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", fact["baseline_sha"]):
                raise ObservatoryError("task_root_binding_lost", "The owner root-set facts do not match this task binding.", 409)
            facts[pair] = {field: fact[field] for field in required}
        if set(facts) != set(expected):
            raise ObservatoryError("task_root_binding_lost", "The owner root-set facts do not match this task binding.", 409)
        return facts

    def _record_root_claim(self, row, claimed):
        facts = self._root_facts(row, claimed)
        if (not isinstance(claimed.get("claim_id"), str) or not isinstance(claimed.get("lease_expires_at"), str)
                or (row.get("lease_id") is not None and row["lease_id"] != claimed["claim_id"])):
            raise ObservatoryError("task_root_binding_lost", "The owner claim identity is unavailable.", 409)
        existing = {item.get("root_id"): item for item in row.get("root_bindings", []) if isinstance(item, dict)}
        rows = []
        for root in row["root_binding"]["roots"]:
            if root["mount"] != "workspace":
                continue
            fact = facts[(root["id"], root["repository_id"])]
            retained = existing.get(root["id"], {})
            for field, value in fact.items():
                if field in retained and retained[field] != value:
                    raise ObservatoryError("task_root_binding_lost", "The retained root facts no longer match the owner claim.", 409)
            rows.append(dict(retained) | fact)
        row.update(lease_id=claimed["claim_id"], lease_expires_at=claimed["lease_expires_at"],
                   root_claim=claimed, root_bindings=rows,
                   status="provisioning" if row.get("status") in {None, "claiming"} else row["status"])
        self.store.put("task-binding", row["owner"], row["id"], row)

    def _provision_root_claim(self, project, row):
        for root in row["root_bindings"]:
            fields = ("runner_checkout", "verification_checkout", "artifact_root")
            base = Path(project["runner_root"]) / root["root_id"]
            name = row["id"] + "-" + root["root_id"]
            expected = (base / name, base / (name + "-verify"), base / ".workbench-artifacts" / name)
            if any(root.get(field) is not None and Path(root[field]).resolve() != path.resolve()
                   for field, path in zip(fields, expected)):
                raise ObservatoryError("task_root_binding_lost", "A retained private root path no longer matches this task binding.", 409)
            if all(isinstance(root.get(field), str) and Path(root[field]).is_dir() for field in fields):
                continue
            runner_checkout, verification_checkout, baseline, artifact_root = self.artifacts.provision(
                root["claim_worktree"], str(Path(project["runner_root"]) / root["root_id"]), row["id"] + "-" + root["root_id"])
            if baseline != root["baseline_sha"]:
                raise ObservatoryError("task_root_binding_lost", "The private clone no longer matches its owner baseline.", 409)
            root.update(runner_checkout=runner_checkout, verification_checkout=verification_checkout, artifact_root=artifact_root)
            self.store.put("task-binding", row["owner"], row["id"], row)
        primary = next((item for item in row["root_bindings"] if item["root_id"] == row["root_binding"]["primary_root_id"]), None)
        if primary is None:
            raise ObservatoryError("task_root_binding_lost", "The selected task root is unavailable.", 409)
        row.update(claim_worktree=primary["claim_worktree"], runner_checkout=primary["runner_checkout"],
                   verification_checkout=primary["verification_checkout"], baseline_sha=primary["baseline_sha"],
                   artifact_root=primary["artifact_root"])
        self.store.put("task-binding", row["owner"], row["id"], row)

    def _finalize_preparation(self, project, row):
        packet = self.cli(project, "packet", row["task_id"], "--format", "json")
        fresh_prds = self.cli(project, "prd", "list", "--json")["prds"]
        fresh_prd = next((item for item in fresh_prds if item["id"] == row.get("prd_id")), None)
        if fresh_prd is None or self.packet_contract_digest(packet, fresh_prd) != row["packet_digest"]:
            raise ObservatoryError("packet_changed", "The task or PRD contract changed while the claim was prepared. The claim was released.", 409)
        commands = packet.get("task", {}).get("verification", {}).get("commands", [])
        paths = packet.get("task", {}).get("likely_files", [])
        if commands != row["task_verification_commands"] or paths != row["task_declared_paths"]:
            raise ObservatoryError("packet_changed", "The task work packet changed while the claim was prepared. The claim was released.", 409)
        if row.get("root_bindings"):
            scopes = {item["id"]: item["expected_files"] for item in row["root_binding"]["roots"]}
            for item in row["root_bindings"]:
                item.update(declared_paths=paths if item["root_id"] == row["root_binding"]["claim_root_id"] else scopes[item["root_id"]],
                            packet_digest=row["packet_digest"])
        binding_digest = (digest({"binding": row["root_binding"], "roots": row["root_bindings"]})
                          if row.get("root_bindings") else digest(row["root_binding"]))
        row.update(verification_commands=commands, declared_paths=paths, root_binding_digest=binding_digest, status="ready", error=None)
        self.store.put("task-binding", row["owner"], row["id"], row)
        return row

    def _recover_root_preparation(self, project, row):
        """Reconcile exactly one retained request; never issue another claim."""
        try:
            lookup = self._root_request_identity(project, row)
            claimed = self.cli(project, "roots", "reconcile", "--request-id", lookup["request_id"],
                               "--request-digest", lookup["request_digest"], "--actor", row["actor"], "--json")
            self._record_root_claim(row, claimed)
            self._provision_root_claim(project, row)
            return self._finalize_preparation(project, row)
        except ObservatoryError:
            row.update(status="cleanup_required", error="The retained root-set request needs owner reconciliation.")
            self.store.put("task-binding", row["owner"], row["id"], row)
            raise ObservatoryError("start_uncertain", "The previous start needs reconciliation. Its claim will not be duplicated.", 409) from None

    @staticmethod
    def _paths_overlap(first, second):
        try:
            common = os.path.commonpath((str(first), str(second)))
        except ValueError:
            return False
        return common in {str(first), str(second)}

    @staticmethod
    def _context_name(name):
        lowered = name.casefold()
        return (
            bool(name) and "/" not in name and "\\" not in name
            and not any(ord(char) < 32 or ord(char) == 127 for char in name)
            and lowered not in {".git", ".anvil", ".ssh", "credentials", "credential", "secrets", "secret", "id_rsa", "id_ed25519"}
            and not lowered.startswith(".env") and "secret" not in lowered
            and "credential" not in lowered and not lowered.endswith(_SECRET_SUFFIXES)
        )

    def _canonical_root(self, value):
        if type(value) is not str or not Path(value).is_absolute():
            raise ObservatoryError("project_root_invalid", "This project root is not an absolute server declaration.", 409)
        path = Path(value)
        try:
            with self._root_fd(path):
                canonical = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ObservatoryError("project_root_unavailable", "This project root is unavailable for isolated execution.", 409) from None
        if canonical == Path(canonical.anchor):
            raise ObservatoryError("project_root_invalid", "A filesystem root cannot be a task root.", 409)
        with self._root_fd(canonical) as descriptor:
            metadata = os.fstat(descriptor)
        return canonical, {"device": metadata.st_dev, "inode": metadata.st_ino}

    @contextmanager
    def _root_fd(self, path):
        if not all(hasattr(os, item) for item in ("O_DIRECTORY", "O_NOFOLLOW")):
            raise ObservatoryError("project_root_unsupported", "This platform cannot safely prepare task roots.", 409)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(path.anchor, flags)
        try:
            for component in path.parts[1:]:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("declared root is not a directory")
            yield descriptor
        finally:
            os.close(descriptor)

    def _materialize_context_snapshots(self, row, runner_root):
        roots = [item for item in row["root_binding"]["roots"] if item["mount"] == "context-read-only"]
        if roots:
            destination = Path(runner_root) / ".workbench-context" / row["id"]
            snapshots = self._snapshot_context_roots(roots, destination)
            for item in roots:
                item["context_snapshot"] = snapshots[item["id"]]

    def _snapshot_context_roots(self, roots, destination):
        if not roots:
            return {}
        if destination.exists() or destination.is_symlink():
            raise ObservatoryError("context_snapshot_unavailable", "The private task context destination is unavailable.", 409)
        staging = destination.parent / ("." + destination.name + "." + uuid.uuid4().hex + ".staging")
        state = {"files": 0, "bytes": 0, "inspected": 0, "directories": 0,
                 "truncated": False, "deadline": time.monotonic() + _CONTEXT_DEADLINE_SECONDS}
        result = {}
        try:
            staging.mkdir(parents=True, mode=0o700)
            self._context_owner(staging)
            for root in roots:
                target = staging / root["id"]
                target.mkdir(mode=0o700)
                self._context_owner(target)
                manifest = []
                with self._root_fd(Path(root["source_path"])) as descriptor:
                    metadata = os.fstat(descriptor)
                    if {"device": metadata.st_dev, "inode": metadata.st_ino} != root["source_identity"]:
                        raise OSError("declared context root changed before snapshot")
                    self._copy_context_tree(descriptor, target, (), state, manifest)
                result[root["id"]] = {"path": str(destination / root["id"]), "files": len(manifest),
                                      "bytes": sum(item["bytes"] for item in manifest),
                                      "truncated": state["truncated"],
                                      "digest": digest({"root_id": root["id"], "files": manifest,
                                                        "truncated": state["truncated"]})}
            self._seal_context_snapshot(staging)
            os.replace(staging, destination)
            return result
        except (OSError, UnicodeError, ValueError):
            if staging.exists() and not staging.is_symlink():
                self._discard_context_snapshot(staging)
            raise ObservatoryError("context_snapshot_unavailable", "The secondary task context could not be prepared safely.", 409) from None

    @staticmethod
    def _seal_context_snapshot(staging):
        for directory, _children, _files in os.walk(staging, topdown=False, followlinks=False):
            path = Path(directory)
            if not path.is_symlink():
                os.chmod(path, 0o500)

    @staticmethod
    def _discard_context_snapshot(staging):
        for directory, _children, _files in os.walk(staging, topdown=False, followlinks=False):
            path = Path(directory)
            if not path.is_symlink():
                try:
                    os.chmod(path, 0o700)
                except OSError:
                    pass
        shutil.rmtree(staging)

    def _context_owner(self, path):
        expected = self.config.get("pi", {}).get("uid")
        if expected is None:
            return
        if path.is_symlink():
            raise OSError("context snapshot symlink")
        if path.stat().st_uid != expected:
            if os.geteuid() != 0:
                raise OSError("context snapshot ownership")
            os.chown(path, expected, -1)

    def _copy_context_tree(self, descriptor, destination, prefix, state, manifest):
        if len(prefix) >= _MAX_CONTEXT_DEPTH or state["truncated"]:
            state["truncated"] = True
            return
        names = []
        scan_descriptor = os.dup(descriptor)
        try:
            with os.scandir(scan_descriptor) as entries:
                for entry in entries:
                    if time.monotonic() >= state["deadline"] or state["inspected"] >= _MAX_CONTEXT_INSPECTED:
                        state["truncated"] = True
                        break
                    state["inspected"] += 1
                    names.append(entry.name)
        finally:
            try:
                os.close(scan_descriptor)
            except OSError:
                pass
        for name in sorted(names):
            if time.monotonic() >= state["deadline"]:
                state["truncated"] = True
                return
            if not self._context_name(name):
                continue
            try:
                metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError:
                continue
            relative = "/".join((*prefix, name))
            if stat.S_ISDIR(metadata.st_mode):
                if state["directories"] >= _MAX_CONTEXT_DIRECTORIES:
                    state["truncated"] = True
                    return
                try:
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=descriptor)
                except OSError:
                    continue
                try:
                    actual = os.fstat(child)
                    if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (metadata.st_dev, metadata.st_ino):
                        continue
                    state["directories"] += 1
                    target = destination / name
                    target.mkdir(mode=0o700)
                    self._context_owner(target)
                    self._copy_context_tree(child, target, (*prefix, name), state, manifest)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            if metadata.st_size > _MAX_CONTEXT_FILE_BYTES or state["files"] >= _MAX_CONTEXT_FILES:
                state["truncated"] = True
                continue
            if state["bytes"] + metadata.st_size > _MAX_CONTEXT_BYTES:
                state["truncated"] = True
                continue
            try:
                source = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            except OSError:
                continue
            try:
                actual = os.fstat(source)
                if (not stat.S_ISREG(actual.st_mode)
                        or (actual.st_dev, actual.st_ino) != (metadata.st_dev, metadata.st_ino)
                        or actual.st_size > _MAX_CONTEXT_FILE_BYTES
                        or state["bytes"] + actual.st_size > _MAX_CONTEXT_BYTES):
                    state["truncated"] = True
                    continue
                remaining = min(_MAX_CONTEXT_FILE_BYTES, _MAX_CONTEXT_BYTES - state["bytes"])
                data = bytearray()
                while len(data) <= remaining:
                    if time.monotonic() >= state["deadline"]:
                        state["truncated"] = True
                        return
                    block = os.read(source, min(65536, remaining + 1 - len(data)))
                    if not block:
                        break
                    data.extend(block)
                content = bytes(data)
            finally:
                os.close(source)
            if len(content) > remaining or b"\0" in content:
                if len(content) > remaining:
                    state["truncated"] = True
                continue
            try:
                content.decode("utf-8", "strict")
            except UnicodeDecodeError:
                continue
            target = destination / name
            target.write_bytes(content)
            os.chmod(target, 0o400)
            self._context_owner(target)
            state["files"] += 1
            state["bytes"] += len(content)
            manifest.append({"path": relative, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})

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
        if row.get("root_request"):
            # The canonical reduction is complete before this explicit stop
            # confirmation frees the owner-global whole-repository reservation.
            lookup = self._root_request_identity(project, row)
            terminal = ("--cancel-if-no-claim",) if not claims else ("--confirm-runner-stopped",)
            self.cli(project, "roots", "reconcile", "--request-id", lookup["request_id"],
                     "--request-digest", lookup["request_digest"], "--actor", row["actor"], *terminal, "--json")
        # Only a clean, owned worktree can be removed. Dirty work is retained.
        if not row.get("root_request") and row.get("claim_worktree") and Path(row["claim_worktree"]).is_dir():
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
        row = self.binding_for_pi(binding)
        if row.get("root_request"):
            # The owner validates the immutable request/facts against the live
            # canonical claim while holding its global reservation lock.
            lookup = self._root_request_identity(project, row)
            claimed = self.cli(project, "roots", "reconcile", "--request-id", lookup["request_id"],
                               "--request-digest", lookup["request_digest"], "--actor", row["actor"], "--json")
            self._record_root_claim(row, claimed)
            self._frozen_roots(row)
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
        if expiry - datetime.now(timezone.utc) < timedelta(minutes=60) and time.time() - row.get("last_renew_attempt", 0) > 60:
            row["last_renew_attempt"] = time.time()
            renewal = self.cli(project, "renew", binding.lease_id, "--actor", expected_actor, "--lease", "240", "--json")
            row["lease_renewed"] = renewal.get("renewed") is True
            row["lease_expires_at"] = renewal.get("claim", claim)["lease_expires_at"]
            self.store.put("task-binding", row["owner"], row["id"], row)

    def pi_binding(self, row):
        from .pi_sessions import PiTaskBinding
        return PiTaskBinding(principal_id=row["owner"], project_id=row["project_id"], task_id=row["task_id"],
                             lease_id=row["lease_id"], runner_id=self.config["pi"]["id"], provider_id=row["provider_id"],
                             rootset_digest=row.get("root_binding_digest", ""))

    def binding_for_pi(self, binding):
        for row in self.store.list("task-binding", binding.principal_id):
            if row.get("lease_id") == binding.lease_id and row["task_id"] == binding.task_id and row["project_id"] == binding.project_id:
                if binding.rootset_digest != row.get("root_binding_digest", ""):
                    raise ObservatoryError("task_root_binding_lost", "The frozen task root binding no longer matches this runner session.", 409)
                self._frozen_roots(row)
                return row
        raise ObservatoryError("not_found", "This task runner binding is unavailable.", 404)

    @staticmethod
    def _frozen_roots(row):
        """Return immutable prepared roots or fail before a runner can write."""
        roots = row.get("root_bindings")
        if not roots:
            return ()
        if not isinstance(roots, list) or not all(isinstance(item, dict) for item in roots):
            raise ObservatoryError("task_root_binding_lost", "The frozen task root binding is unavailable.", 409)
        root_ids = [item.get("root_id") for item in roots]
        required = {"root_id", "repository_id", "baseline_sha", "claim_worktree", "runner_checkout", "verification_checkout", "artifact_root", "verification_commands", "declared_paths", "packet_digest"}
        if len(set(root_ids)) != len(root_ids) or any(not required.issubset(item) for item in roots):
            raise ObservatoryError("task_root_binding_lost", "The frozen task root binding is unavailable.", 409)
        expected = digest({"binding": row.get("root_binding"), "roots": roots})
        if row.get("root_binding_digest") != expected:
            raise ObservatoryError("task_root_binding_lost", "The frozen task root binding no longer matches this runner session.", 409)
        return tuple(roots)

    def evidence(self, session, binding_id):
        row = self.binding(session, binding_id)
        if row.get("root_bindings"):
            review = row.get("root_review")
            verification = row.get("root_verification")
            return {"binding_id": binding_id, "source": "isolated-sandbox", "observed_at": time.time(),
                    "acceptance": "independent_review_required", "packet_digest": row["packet_digest"],
                    "status": row.get("status"), "worktree_cleanup": row.get("worktree_cleanup"),
                    "rootset_digest": row.get("root_binding_digest"), "review": review,
                    "verification": verification, "transfer": row.get("root_transfer"),
                    "submission": row.get("submission"),
                    "binding": asdict(self.pi_binding(row)) if row.get("lease_id") else None}
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
        if row.get("root_bindings"):
            self._frozen_roots(row)
            if row.get("root_review") and (row.get("status") != "verification_failed" or any(item.get("state") != "verification_failed" for item in row.get("root_transfer", {}).values())):
                if row.get("root_review", {}).get("rootset_digest") != row.get("root_binding_digest"):
                    raise ObservatoryError("task_root_binding_lost", "The reviewed root set no longer matches this runner session.", 409)
                return row["root_review"]
            if row.get("status") not in {"ready", "verification_failed"}:
                raise ObservatoryError("evidence_unavailable", "This task binding is not available for evidence review.", 409)
            review = self.artifacts.capture_roots(row)
            review["rootset_digest"] = row["root_binding_digest"]
            row.update(root_review=review, root_transfer={}, root_verification=None,
                       status="evidence_reviewed", verification=None, submission=None)
            self.store.put("task-binding", row["owner"], row["id"], row)
            return review
        if row.get("status") not in {"ready", "evidence_reviewed", "verified", "verification_failed"}:
            raise ObservatoryError("evidence_unavailable", "This task binding is not available for evidence review.", 409)
        preview = self.artifacts.capture(row)
        row.update(artifact_digest=preview["artifact_digest"], status="evidence_reviewed", verification=None, submission=None)
        self.store.put("task-binding", row["owner"], row["id"], row)
        return preview

    def verify_evidence(self, session, binding_id, artifact_digest):
        row = self.binding(session, binding_id, execute=True)
        if row.get("root_bindings"):
            return self._verify_root_evidence(row, artifact_digest)
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
        if row.get("root_bindings"):
            return self._submit_root_evidence(row, artifact_digest)
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

    def _root_submission_material(self, row, manifest_digest):
        """Build one private owner manifest from exact reviewed root evidence."""
        roots = {item["root_id"]: item for item in self._frozen_roots(row)}
        review = row.get("root_review")
        verification = row.get("root_verification")
        if (
            not isinstance(review, dict)
            or not isinstance(verification, dict)
            or not isinstance(review.get("roots"), list)
            or review.get("manifest_digest") != manifest_digest
            or digest(review.get("roots")) != manifest_digest
            or verification.get("manifest_digest") != manifest_digest
            or review.get("rootset_digest") != row.get("root_binding_digest")
            or verification.get("rootset_digest") != row.get("root_binding_digest")
            or verification.get("passed") is not True
            or verification.get("transferred") is not True
        ):
            raise ObservatoryError(
                "evidence_unverified",
                "Verify every frozen root transfer for this reviewed manifest before submission.",
                409,
            )
        reviewed = {item.get("root_id"): item for item in review.get("roots", []) if isinstance(item, dict)}
        verified = {item.get("root_id"): item.get("evidence") for item in verification.get("roots", []) if isinstance(item, dict)}
        if set(reviewed) != set(roots) or set(verified) != set(roots):
            raise ObservatoryError("artifact_stale", "The reviewed root manifest does not match this task binding.", 409)
        entries = []
        for root_id in sorted(roots):
            root, review_entry, evidence = roots[root_id], reviewed[root_id], verified[root_id]
            if (
                not isinstance(review_entry, dict)
                or not isinstance(evidence, dict)
                or review_entry.get("baseline_sha") != root["baseline_sha"]
                or evidence.get("baseline_sha") != root["baseline_sha"]
                or review_entry.get("artifact_digest") != evidence.get("artifact_digest")
                or evidence.get("passed") is not True
                or evidence.get("transferred") is not True
            ):
                raise ObservatoryError("evidence_tampered", "The retained per-root verification does not match its reviewed patch.", 409)
            files = review_entry.get("files")
            if (
                not isinstance(files, list)
                or any(not isinstance(item, dict) or type(item.get("path")) is not str for item in files)
            ):
                raise ObservatoryError("evidence_tampered", "The retained root file evidence is unavailable.", 409)
            artifact_digest = evidence.get("artifact_digest")
            if type(artifact_digest) is not str:
                raise ObservatoryError("evidence_tampered", "The retained root patch is unavailable.", 409)
            artifact_row = row | root
            try:
                preview = self.artifacts.load(artifact_row, artifact_digest)
                patch = (Path(root["artifact_root"]) / (artifact_digest + ".patch")).read_bytes()
                retained = self._read_verification(artifact_row, artifact_digest)
            except (OSError, ObservatoryError):
                raise ObservatoryError("evidence_tampered", "The retained root evidence is unavailable.", 409) from None
            if (
                hashlib.sha256(patch).hexdigest() != artifact_digest
                or preview.get("baseline_sha") != root["baseline_sha"]
                or preview.get("packet_digest") != row["packet_digest"]
                or preview.get("files") != files
                or retained.get("artifact_digest") != artifact_digest
                or retained.get("baseline_sha") != root["baseline_sha"]
                or retained.get("packet_digest") != row["packet_digest"]
                or retained.get("files") != files
                or retained.get("passed") is not True
                or retained.get("transferred") is not True
            ):
                raise ObservatoryError("evidence_tampered", "The retained per-root evidence does not match its reviewed patch.", 409)
            command_rows = retained.get("commands")
            commands = root.get("verification_commands")
            if (
                not isinstance(commands, list)
                or not commands
                or not isinstance(command_rows, list)
                or any(not isinstance(item, dict) for item in command_rows)
                or [item.get("command") for item in command_rows] != commands
                or any(item.get("exit_code") != 0 for item in command_rows)
            ):
                raise ObservatoryError("verification_contract_invalid", "Per-root verification commands do not match the frozen owner policy.", 409)
            verification_digest = digest({
                "root_id": root_id, "baseline_sha": root["baseline_sha"],
                "artifact_digest": artifact_digest,
                "commands": [{"command": item["command"], "exit_code": item["exit_code"]} for item in command_rows],
                "files": files,
            })
            entries.append({"root_id": root_id, "baseline_sha": root["baseline_sha"],
                            "artifact_digest": artifact_digest, "verification_digest": verification_digest,
                            "commands": commands, "files": [item["path"] for item in files]})
        serving_manifest_digest = digest({
            "root_binding_digest": row["root_binding_digest"],
            "review_manifest_digest": manifest_digest,
            "roots": entries,
        })
        return {
            "schema": "anvil.root-set-evidence/v1",
            "submission_id": digest({"owner": row["owner"], "binding_id": row["id"], "serving_manifest_digest": serving_manifest_digest}),
            "serving_manifest_digest": serving_manifest_digest,
            "roots": entries,
        }

    def _root_submission_file(self, row, manifest):
        """Persist a byte-identical private manifest without browser filenames."""
        runner_root = Path(self.configured(row["project_id"])["runner_root"])
        directory = runner_root / ".workbench-root-evidence"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise ObservatoryError("runner_unavailable", "Private evidence storage is unavailable.", 409)
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        name = digest({"owner": row["owner"], "project_id": row["project_id"], "submission_id": manifest["submission_id"]}) + ".json"
        path = directory / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
                info = os.fstat(descriptor)
                retained = os.read(descriptor, len(encoded) + 1)
            except OSError as error:
                raise ObservatoryError("evidence_tampered", "The retained owner evidence manifest is unavailable.", 409) from error
            finally:
                try:
                    os.close(descriptor)
                except (OSError, UnboundLocalError):
                    pass
            if not stat.S_ISREG(info.st_mode) or retained != encoded:
                raise ObservatoryError("evidence_tampered", "The retained owner evidence manifest does not match this task.", 409)
            return path
        try:
            offset = 0
            while offset < len(encoded):
                offset += os.write(descriptor, encoded[offset:])
            os.fsync(descriptor)
        except OSError as error:
            raise ObservatoryError("runner_unavailable", "Private evidence storage is unavailable.", 409) from error
        finally:
            os.close(descriptor)
        return path

    def _submit_root_evidence(self, row, manifest_digest):
        """Submit/recover one reviewed root set through the canonical owner CLI."""
        self.artifacts.ensure_quiescent(row)
        manifest = self._root_submission_material(row, manifest_digest)
        manifest_path = self._root_submission_file(row, manifest)
        project = self.configured(row["project_id"])
        lookup = self._root_request_identity(project, row)
        submission = row.get("submission") if isinstance(row.get("submission"), dict) else None
        if submission and submission.get("serving_manifest_digest") != manifest["serving_manifest_digest"]:
            raise ObservatoryError("submission_recovery_required", "A different per-root evidence submission is retained for this task.", 409)
        if submission and row.get("status") == "submitted":
            return submission
        if submission and row.get("status") == "submitted_release_pending":
            # Evidence is already canonical.  Only the conservative owner
            # reservation release remains, so retry that exact reconciliation
            # without reading or submitting a second evidence manifest.
            return self._record_root_submission(
                project, row, manifest, submission.get("state"), lookup
            )
        status_args = ("roots", "evidence-status", row["task_id"], "--request-file", row["root_request"]["path"],
                       "--manifest-file", str(manifest_path), "--actor", row["actor"], "--json")
        if submission:
            recovered = self.cli(project, *status_args)
            if recovered.get("status") == "submitted":
                return self._record_root_submission(project, row, manifest, recovered, lookup)
            if recovered.get("status") != "not_submitted":
                raise ObservatoryError("submission_recovery_required", "The owner evidence outcome needs reconciliation.", 409)
        row.update(status="submission_uncertain", submission={
            "status": "outcome_unknown", "serving_manifest_digest": manifest["serving_manifest_digest"],
            "submission_id": manifest["submission_id"], "acceptance": "independent_review_required",
        })
        self.store.put("task-binding", row["owner"], row["id"], row)
        result = self.cli(project, "roots", "submit-evidence", row["task_id"], "--request-file", row["root_request"]["path"],
                          "--manifest-file", str(manifest_path), "--actor", row["actor"], "--json")
        return self._record_root_submission(project, row, manifest, result, lookup)

    def _record_root_submission(self, project, row, manifest, result, lookup):
        if (
            not isinstance(result, dict)
            or result.get("status") != "submitted"
            or result.get("submission_id") != manifest["submission_id"]
            or type(result.get("evidence_id")) is not str
        ):
            raise ObservatoryError("submission_recovery_required", "The owner evidence result does not match this task binding.", 409)
        submission = {"status": "submitted", "state": result,
                      "serving_manifest_digest": manifest["serving_manifest_digest"],
                      "submission_id": manifest["submission_id"],
                      "acceptance": "independent_review_required"}
        row.update(status="submitted_release_pending", submission=submission)
        self.store.put("task-binding", row["owner"], row["id"], row)
        # State terminal reduction precedes this explicit owner-global release.
        self.cli(project, "roots", "reconcile", "--request-id", lookup["request_id"],
                 "--request-digest", lookup["request_digest"], "--actor", row["actor"],
                 "--confirm-runner-stopped", "--json")
        row.update(status="submitted")
        self.store.put("task-binding", row["owner"], row["id"], row)
        return submission

    def _verify_root_evidence(self, row, manifest_digest):
        self._frozen_roots(row)
        review = row.get("root_review")
        if (not isinstance(review, dict) or review.get("manifest_digest") != manifest_digest
                or digest(review.get("roots")) != manifest_digest
                or review.get("rootset_digest") != row.get("root_binding_digest")):
            raise ObservatoryError("artifact_stale", "The reviewed root manifest does not match this task binding.", 409)
        previous = row.get("root_verification")
        if isinstance(previous, dict) and previous.get("manifest_digest") == manifest_digest:
            return previous
        entries = review.get("roots")
        roots = {item["root_id"]: row | item for item in self._frozen_roots(row)}
        if not isinstance(entries, list) or len(entries) != len(roots):
            raise ObservatoryError("artifact_stale", "The reviewed root manifest does not match this task binding.", 409)
        by_id = {item.get("root_id"): item for item in entries if isinstance(item, dict)}
        if set(by_id) != set(roots):
            raise ObservatoryError("artifact_stale", "The reviewed root manifest does not match this task binding.", 409)
        progress = row.setdefault("root_transfer", {})
        results = []
        for root_id, root in roots.items():
            entry = by_id[root_id]
            if entry.get("baseline_sha") != root["baseline_sha"] or not isinstance(entry.get("artifact_digest"), str):
                raise ObservatoryError("artifact_stale", "The reviewed root manifest does not match this task binding.", 409)
            item = progress.get(root_id)
            if item and (item.get("artifact_digest") != entry["artifact_digest"]
                         or item.get("rootset_digest") != row["root_binding_digest"]):
                raise ObservatoryError("transfer_recovery_required", "A different root patch has an unfinished transfer intent.", 409)
            if item and item.get("state") == "transferred":
                if self.artifacts.transfer_state(root, entry["artifact_digest"]) != "exact":
                    raise ObservatoryError("transfer_recovery_required", "A transferred root no longer matches its reviewed patch.", 409)
                evidence = self._read_verification(root, entry["artifact_digest"])
            else:
                if item:
                    state = self.artifacts.transfer_state(root, entry["artifact_digest"])
                    if state == "other":
                        raise ObservatoryError("transfer_recovery_required", "A root changed during transfer recovery.", 409)
                    already_transferred = state == "exact"
                else:
                    progress[root_id] = {"artifact_digest": entry["artifact_digest"],
                                         "rootset_digest": row["root_binding_digest"], "state": "intent",
                                         "recorded_at": time.time()}
                    self.store.put("task-binding", row["owner"], row["id"], row)
                    already_transferred = False
                try:
                    evidence = self.artifacts.verify(root, entry["artifact_digest"], already_transferred=already_transferred)
                except ObservatoryError:
                    row.update(status="transfer_recovery_required")
                    self.store.put("task-binding", row["owner"], row["id"], row)
                    raise
                progress[root_id].update(state="transferred" if evidence["transferred"] else "verification_failed",
                                         verified_at=time.time())
                self.store.put("task-binding", row["owner"], row["id"], row)
            results.append({"root_id": root_id, "evidence": evidence})
        passed = all(item["evidence"].get("passed") is True for item in results)
        transferred = all(item["evidence"].get("transferred") is True for item in results)
        result = {"manifest_digest": manifest_digest, "rootset_digest": row["root_binding_digest"],
                  "roots": results, "passed": passed, "transferred": transferred,
                  "partial_transfer": any(item["evidence"].get("transferred") for item in results) and not transferred}
        row.update(root_verification=result, status="verified" if passed and transferred else "verification_failed")
        self.store.put("task-binding", row["owner"], row["id"], row)
        return result

    def _read_verification(self, row, artifact_digest):
        path = Path(row["artifact_root"]) / (artifact_digest + ".verification.json")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ObservatoryError("evidence_unverified", "The isolated verification record is unavailable.", 409) from None
