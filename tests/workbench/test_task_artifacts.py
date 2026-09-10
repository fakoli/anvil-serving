import hashlib

import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.workbench_app.task_artifacts import TaskArtifacts, TaskArtifactSandbox


class SymlinkCapture(TaskArtifactSandbox):
    def provision(self, source, destination, verification_destination):
        raise AssertionError("not used")

    def capture(self, source, baseline_sha):
        return {"baseline_sha": baseline_sha, "patch": b"diff --git a/link b/link\nnew file mode 120000\n", "files": [{"path": "link", "status": "A", "mode": "120000"}]}

    def verify_transfer(self, source, verification_base, target, baseline_sha, patch, commands, *, already_transferred=False):
        raise AssertionError("not used")

    def transfer_state(self, verification_base, target, baseline_sha, patch):
        raise AssertionError("not used")


def test_capture_rejects_symlink_before_private_artifact_write(tmp_path):
    artifacts = TaskArtifacts(SymlinkCapture(), is_active=lambda _: False)
    row = {"runner_checkout": str(tmp_path / "runner"), "baseline_sha": "a" * 40, "packet_digest": "b" * 64, "artifact_root": str(tmp_path / "evidence")}
    with pytest.raises(ObservatoryError, match="unsupported file mode"):
        artifacts.capture(row)
    assert not (tmp_path / "evidence").exists()


class OutsideScopeCapture(SymlinkCapture):
    def capture(self, source, baseline_sha):
        return {"baseline_sha": baseline_sha, "patch": b"diff --git a/private.txt b/private.txt\n", "files": [{"path": "declared.txt", "status": "M", "mode": "100644"}]}


def test_capture_checks_patch_paths_against_frozen_anvil_scope(tmp_path):
    artifacts = TaskArtifacts(OutsideScopeCapture(), is_active=lambda _: False)
    row = {"runner_checkout": str(tmp_path / "runner"), "baseline_sha": "a" * 40, "packet_digest": "b" * 64, "artifact_root": str(tmp_path / "evidence"), "declared_paths": ["declared.txt"]}
    with pytest.raises(ObservatoryError, match="outside the frozen Anvil task scope"):
        artifacts.capture(row)


class FailedVerification(SymlinkCapture):
    def verify_transfer(self, source, verification_base, target, baseline_sha, patch, commands, *, already_transferred=False):
        return {"baseline_sha": baseline_sha, "verification_clean": True, "applied": False, "commands": [{"command": command, "exit_code": 1, "duration_seconds": 0.1, "stdout": "actual failure", "stderr": ""} for command in commands]}


def test_failed_verification_retains_actual_output_without_transfer(tmp_path):
    root = tmp_path / "evidence"
    patch = b"diff --git a/declared.txt b/declared.txt\n"
    digest = hashlib.sha256(patch).hexdigest()
    root.mkdir()
    (root / f"{digest}.patch").write_bytes(patch)
    (root / f"{digest}.json").write_text('{"artifact_digest":"' + digest + '","baseline_sha":"' + "a" * 40 + '","packet_digest":"' + "b" * 64 + '","files":[{"path":"declared.txt","status":"M","mode":"100644"}]}', encoding="utf-8")
    row = {"runner_checkout": str(tmp_path / "runner"), "verification_checkout": str(tmp_path / "verify"), "claim_worktree": str(tmp_path / "claim"), "baseline_sha": "a" * 40, "packet_digest": "b" * 64, "artifact_root": str(root), "declared_paths": ["declared.txt"], "verification_commands": ["python -m pytest"]}
    evidence = TaskArtifacts(FailedVerification(), is_active=lambda _: False).verify(row, digest)
    assert evidence["passed"] is False and evidence["transferred"] is False
    assert evidence["commands"][0]["stdout"] == "actual failure"
