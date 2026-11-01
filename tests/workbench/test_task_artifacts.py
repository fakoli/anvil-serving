
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


class ValidCapture(SymlinkCapture):
    def capture(self, source, baseline_sha):
        return {"baseline_sha": baseline_sha, "patch": b"diff --git a/declared.txt b/declared.txt\n", "files": [{"path": "declared.txt", "status": "M", "mode": "100644"}]}


def test_capture_roots_binds_each_digest_to_its_root(tmp_path):
    roots = []
    for root_id, baseline in (("app", "a" * 40), ("lib", "b" * 40)):
        roots.append({"root_id": root_id, "runner_checkout": str(tmp_path / root_id), "baseline_sha": baseline,
                      "packet_digest": "c" * 64, "artifact_root": str(tmp_path / "evidence" / root_id), "declared_paths": ["declared.txt"]})
    manifest = TaskArtifacts(ValidCapture(), is_active=lambda _: False).capture_roots({"root_bindings": roots})
    assert [item["root_id"] for item in manifest["roots"]] == ["app", "lib"]
    assert [item["baseline_sha"] for item in manifest["roots"]] == ["a" * 40, "b" * 40]


def test_capture_checks_patch_paths_against_frozen_anvil_scope(tmp_path):
    artifacts = TaskArtifacts(OutsideScopeCapture(), is_active=lambda _: False)
    row = {"runner_checkout": str(tmp_path / "runner"), "baseline_sha": "a" * 40, "packet_digest": "b" * 64, "artifact_root": str(tmp_path / "evidence"), "declared_paths": ["declared.txt"]}
    with pytest.raises(ObservatoryError, match="outside the frozen Anvil task scope"):
        artifacts.capture(row)


class FailedVerification(SymlinkCapture):
    def verify_transfer(self, source, verification_base, target, baseline_sha, patch, commands, *, already_transferred=False):
        return {"baseline_sha": baseline_sha, "verification_clean": True, "applied": False, "commands": [{"command": command, "exit_code": 1, "duration_seconds": 0.1, "stdout": "actual failure", "stderr": ""} for command in commands]}
