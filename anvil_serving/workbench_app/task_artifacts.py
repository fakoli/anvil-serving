"""Bounded task-change evidence captured only by a pinned isolated runner.

This module deliberately has no host ``git`` implementation.  A Pi-controlled
checkout is untrusted after an agent writes to it: its Git configuration,
filters, hooks, and fsmonitor settings must never be interpreted by the
Workbench process.  Deployments supply a sandbox adapter which performs Git
operations in a pinned, networkless container.  Tests supply a small fake.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Mapping

from ..observability.dashboard.contracts import ObservatoryError


MAX_PATCH_BYTES = 2 * 1024 * 1024
MAX_PATHS = 400
MAX_OUTPUT_BYTES = 32 * 1024
MAX_COMMANDS = 32
MAX_ARTIFACT_JSON_BYTES = 3 * 1024 * 1024


class TaskArtifactSandbox:
    """Pinned-container boundary for all Git and command execution.

    Implementations must use a read-only source mount for ``capture`` and a
    network-disabled, CPU-limited container for every method.
    ``verify_transfer`` must prove the disposable verification clone first,
    then check the target baseline and clean state before transferring the
    exact patch.
    """

    def provision(self, source: Path, destination: Path, verification_destination: Path) -> Mapping[str, Any]:
        raise NotImplementedError

    def capture(self, source: Path, baseline_sha: str) -> Mapping[str, Any]:
        raise NotImplementedError

    def verify_transfer(
        self,
        source: Path,
        verification_base: Path,
        target: Path,
        baseline_sha: str,
        patch: bytes,
        commands: tuple[str, ...],
        *,
        already_transferred: bool = False,
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    def transfer_state(self, verification_base: Path, target: Path, baseline_sha: str, patch: bytes) -> str:
        raise NotImplementedError


class UnavailableTaskArtifactSandbox(TaskArtifactSandbox):
    def _unavailable(self) -> None:
        raise ObservatoryError(
            "artifact_sandbox_unavailable",
            "Task evidence needs the configured isolated execution sandbox.",
            409,
        )

    def provision(self, source: Path, destination: Path, verification_destination: Path) -> Mapping[str, Any]:
        self._unavailable()

    def capture(self, source: Path, baseline_sha: str) -> Mapping[str, Any]:
        self._unavailable()

    def verify_transfer(self, source: Path, verification_base: Path, target: Path, baseline_sha: str, patch: bytes, commands: tuple[str, ...], *, already_transferred=False) -> Mapping[str, Any]:
        self._unavailable()

    def transfer_state(self, verification_base: Path, target: Path, baseline_sha: str, patch: bytes) -> str:
        self._unavailable()


def _error(code: str, message: str) -> ObservatoryError:
    return ObservatoryError(code, message, 409)


def _safe_path(value: object) -> str:
    if type(value) is not str or not value or len(value) > 512 or "\\" in value:
        raise _error("unsafe_artifact_path", "The captured change contains an unsupported path.")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", "..", ".git", ".anvil"} for part in path.parts):
        raise _error("unsafe_artifact_path", "The captured change contains an unsupported path.")
    return path.as_posix()


def _safe_mode(value: object) -> str:
    if value is None:
        return "100644"
    if type(value) is not str or value in {"120000", "160000"} or not re.fullmatch(r"100[0-7]{3}", value):
        raise _error("unsafe_artifact_mode", "The captured change contains an unsupported file mode.")
    return value


def _bounded_text(value: object) -> str:
    if type(value) is not str:
        return ""
    return value.encode("utf-8", "replace")[:MAX_OUTPUT_BYTES].decode("utf-8", "replace")


class TaskArtifacts:
    """Own private task evidence files and validate sandbox observations."""

    def __init__(
        self,
        sandbox: TaskArtifactSandbox | None = None,
        *,
        max_patch_bytes: int = MAX_PATCH_BYTES,
        is_active=None,
    ):
        self.sandbox = sandbox or UnavailableTaskArtifactSandbox()
        self.max_patch_bytes = max_patch_bytes
        # A missing coordinator is deliberately treated as active.  Capturing a
        # live Pi checkout races agent writes and could transfer an unreviewed
        # patch, so deployments must provide an owner-side activity check.
        self.is_active = is_active or (lambda _row: True)

    def ensure_quiescent(self, row: Mapping[str, Any]) -> None:
        if self.is_active(row):
            raise _error("task_runner_active", "Stop the task runner and wait for it to exit before capturing or transferring evidence.")

    def provision(self, source: str, runner_root: str, binding_id: str) -> tuple[str, str, str, str]:
        root = Path(runner_root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = root / binding_id
        verification_destination = root / (binding_id + "-verify")
        result = self.sandbox.provision(Path(source), destination, verification_destination)
        checkout = Path(result.get("runner_checkout", ""))
        verification_checkout = Path(result.get("verification_checkout", ""))
        baseline = result.get("baseline_sha")
        if checkout != destination or verification_checkout != verification_destination or type(baseline) is not str or not re.fullmatch(r"[0-9a-f]{40,64}", baseline):
            raise _error("invalid_sandbox_result", "The isolated workspace did not report a stable baseline.")
        # This is server-created storage, never a browser-selected path.
        artifact_root = root / ".workbench-artifacts" / binding_id
        artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return str(checkout), str(verification_checkout), baseline, str(artifact_root)

    def capture(self, row: Mapping[str, Any]) -> dict[str, Any]:
        self.ensure_quiescent(row)
        result = self.sandbox.capture(Path(row["runner_checkout"]), row["baseline_sha"])
        if result.get("baseline_sha") != row["baseline_sha"]:
            raise _error("baseline_changed", "The isolated checkout no longer matches its claimed baseline.")
        patch = result.get("patch")
        if not isinstance(patch, bytes) or not patch or len(patch) > self.max_patch_bytes:
            raise _error("artifact_too_large", "The captured patch is empty or exceeds the task evidence bound.")
        files = result.get("files")
        if not isinstance(files, list) or not files or len(files) > MAX_PATHS:
            raise _error("artifact_paths_invalid", "The captured task change has an unsupported file list.")
        observed = []
        seen = set()
        for item in files:
            if not isinstance(item, Mapping):
                raise _error("artifact_paths_invalid", "The captured task change has an unsupported file list.")
            path = _safe_path(item.get("path"))
            if path in seen:
                raise _error("artifact_paths_invalid", "The captured task change has duplicate file paths.")
            seen.add(path)
            status = item.get("status", "M")
            if status not in {"A", "M", "D", "R"}:
                raise _error("artifact_paths_invalid", "The captured task change has an unsupported file status.")
            observed.append({"path": path, "status": status, "mode": _safe_mode(item.get("mode"))})
        scopes = row.get("declared_paths")
        if not isinstance(scopes, list) or not scopes:
            raise _error("scope_unavailable", "The frozen Anvil work packet declares no file scope for this transfer.")
        declared = tuple(_safe_path(scope) for scope in scopes)
        outside = [item["path"] for item in observed if not any(self._in_scope(item["path"], scope) for scope in declared)]
        if outside:
            raise _error("outside_declared_scope", "The captured patch changes files outside the frozen Anvil task scope.")
        # A sandbox must report all paths it changed.  This check also blocks
        # path-only metadata that disguises a binary/symlink patch.
        text = patch.decode("utf-8", "replace")
        if re.search(r"^(?:new|old|deleted file) mode (?:120000|160000)$", text, re.MULTILINE):
            raise _error("unsafe_artifact_mode", "The captured change contains an unsupported file mode.")
        patch_paths = set(re.findall(r"^diff --git a/(.+?) b/(.+?)$", text, re.MULTILINE))
        for left, right in patch_paths:
            _safe_path(left)
            _safe_path(right)
            if not any(self._in_scope(left, scope) for scope in declared) or not any(self._in_scope(right, scope) for scope in declared):
                raise _error("outside_declared_scope", "The captured patch changes files outside the frozen Anvil task scope.")
        digest = hashlib.sha256(patch).hexdigest()
        root = Path(row["artifact_root"])
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        patch_path = root / (digest + ".patch")
        if not patch_path.exists():
            patch_path.write_bytes(patch)
            os.chmod(patch_path, 0o600)
        preview = {
            "artifact_digest": digest,
            "baseline_sha": row["baseline_sha"],
            "packet_digest": row["packet_digest"],
            "files": observed,
            "patch_bytes": len(patch),
            "patch": text,
            "captured_at": time.time(),
        }
        self._write_json(root / (digest + ".json"), preview)
        return preview

    def verify(self, row: Mapping[str, Any], artifact_digest: str, *, already_transferred=False) -> dict[str, Any]:
        self.ensure_quiescent(row)
        preview = self.load(row, artifact_digest)
        commands = tuple(row.get("verification_commands", ()))
        if not commands or len(commands) > MAX_COMMANDS or any(type(command) is not str or not command or len(command) > 1024 for command in commands):
            raise _error("verification_contract_invalid", "The frozen work packet has unsupported verification commands.")
        patch = (Path(row["artifact_root"]) / (artifact_digest + ".patch")).read_bytes()
        if hashlib.sha256(patch).hexdigest() != artifact_digest:
            raise _error("artifact_tampered", "The retained reviewed patch no longer matches its digest.")
        result = self.sandbox.verify_transfer(
            Path(row["runner_checkout"]),
            Path(row["verification_checkout"]),
            Path(row["claim_worktree"]),
            row["baseline_sha"],
            patch,
            commands,
            already_transferred=already_transferred,
        )
        if result.get("baseline_sha") != row["baseline_sha"] or result.get("verification_clean") is not True or type(result.get("applied")) is not bool:
            raise _error("transfer_refused", "The claimed workspace changed or was not clean; the reviewed patch was not transferred.")
        rows = result.get("commands")
        if not isinstance(rows, list) or len(rows) != len(commands):
            raise _error("verification_result_invalid", "The isolated verifier returned an unsupported result.")
        results = []
        for expected, item in zip(commands, rows):
            if not isinstance(item, Mapping) or item.get("command") != expected or type(item.get("exit_code")) is not int:
                raise _error("verification_result_invalid", "The isolated verifier returned an unsupported result.")
            duration = item.get("duration_seconds")
            if type(duration) not in (int, float) or duration < 0 or duration > 3600:
                raise _error("verification_result_invalid", "The isolated verifier returned an unsupported result.")
            results.append({"command": expected, "exit_code": item["exit_code"], "duration_seconds": duration,
                            "stdout": _bounded_text(item.get("stdout")), "stderr": _bounded_text(item.get("stderr"))})
        evidence = {"artifact_digest": artifact_digest, "packet_digest": row["packet_digest"], "baseline_sha": row["baseline_sha"],
                    "files": preview["files"], "commands": results, "verified_at": time.time(),
                    "passed": all(item["exit_code"] == 0 for item in results), "transferred": result["applied"]}
        if evidence["passed"] != evidence["transferred"]:
            raise _error("verification_transfer_invalid", "The isolated verifier returned an inconsistent transfer result.")
        self._write_json(Path(row["artifact_root"]) / (artifact_digest + ".verification.json"), evidence)
        return evidence

    def transfer_state(self, row: Mapping[str, Any], artifact_digest: str) -> str:
        self.ensure_quiescent(row)
        self.load(row, artifact_digest)
        patch = (Path(row["artifact_root"]) / (artifact_digest + ".patch")).read_bytes()
        if hashlib.sha256(patch).hexdigest() != artifact_digest:
            raise _error("artifact_tampered", "The retained reviewed patch no longer matches its digest.")
        state = self.sandbox.transfer_state(Path(row["verification_checkout"]), Path(row["claim_worktree"]), row["baseline_sha"], patch)
        if state not in {"pristine", "exact", "other"}:
            raise _error("transfer_recovery_invalid", "The transfer recovery check returned an unsupported result.")
        return state

    def load(self, row: Mapping[str, Any], artifact_digest: str) -> dict[str, Any]:
        if type(artifact_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", artifact_digest):
            raise _error("artifact_not_found", "The reviewed task evidence is unavailable.")
        path = Path(row["artifact_root"]) / (artifact_digest + ".json")
        try:
            preview = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise _error("artifact_not_found", "The reviewed task evidence is unavailable.") from None
        if preview.get("artifact_digest") != artifact_digest or preview.get("baseline_sha") != row["baseline_sha"] or preview.get("packet_digest") != row["packet_digest"]:
            raise _error("artifact_stale", "The reviewed task evidence does not match this task binding.")
        return preview

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_ARTIFACT_JSON_BYTES:
            raise _error("artifact_too_large", "The task evidence exceeds the private artifact bound.")
        path.write_bytes(encoded)
        os.chmod(path, 0o600)

    @staticmethod
    def _in_scope(path: str, scope: str) -> bool:
        normalized = scope.rstrip("/")
        return path == normalized or path.startswith(normalized + "/") or fnmatchcase(path, scope)
