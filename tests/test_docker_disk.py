from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from anvil_serving import docker_disk


def _completed(argv, *, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _docker_disk_path(tmp_path: Path) -> Path:
    path = tmp_path / "Docker" / "wsl" / "disk" / "docker_data.vhdx"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"vhdx-probe")
    return path


class FakeRunner:
    def __init__(self, *, status="running", attached=True, before=1000, after=400):
        self.status = status
        self.attached = attached
        self.before = before
        self.after = after
        self.compacted = False
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:3] == ["docker", "desktop", "status"]:
            return _completed(argv, stdout=json.dumps({"Status": self.status}))
        if argv[:3] == ["docker", "desktop", "stop"]:
            self.status = "stopped"
            self.attached = False
            return _completed(argv)
        if argv[0] == "powershell.exe" and "Get-VHD" in argv[-1]:
            size = self.after if self.compacted else self.before
            payload = {
                "Path": kwargs["env"]["ANVIL_SERVING_DOCKER_VHDX_PATH"],
                "VhdType": "Dynamic",
                "VhdFormat": "VHDX",
                "FileSize": size,
                "Size": 4096,
                "Attached": self.attached,
                "FragmentationPercentage": 50,
                "OptimizeVhdAvailable": True,
            }
            return _completed(argv, stdout=json.dumps(payload))
        if argv[0] == "powershell.exe" and "Mount-VHD" in argv[-1]:
            self.compacted = True
            return _completed(argv)
        raise AssertionError("unexpected command: %r" % (argv,))


class StoppedCliRunner(FakeRunner):
    def __call__(self, argv, **kwargs):
        if argv[:3] == ["docker", "desktop", "status"] and self.status == "stopped":
            self.calls.append(list(argv))
            return _completed(
                argv,
                stderr="Could not retrieve status. Is Docker Desktop running?",
                returncode=1,
            )
        return super().__call__(argv, **kwargs)


class PrivilegeFallbackRunner(FakeRunner):
    def __call__(self, argv, **kwargs):
        if argv[0] == "powershell.exe" and "Mount-VHD" in argv[-1]:
            self.calls.append(list(argv))
            return _completed(
                argv,
                stderr="A required privilege is not held by the client. (0x80070522)",
                returncode=1,
            )
        if argv[0] == "powershell.exe" and "Mode Prezeroed" in argv[-1]:
            self.calls.append(list(argv))
            self.compacted = True
            return _completed(argv)
        return super().__call__(argv, **kwargs)


def test_validate_refuses_relative_path(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    with pytest.raises(docker_disk.DockerDiskCompactionError, match="absolute"):
        docker_disk.validate_docker_data_disk("docker_data.vhdx")


def test_validate_refuses_unknown_vhdx_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = tmp_path / "unrelated.vhdx"
    path.write_bytes(b"vhdx")
    with pytest.raises(docker_disk.DockerDiskCompactionError, match="known Docker"):
        docker_disk.validate_docker_data_disk(path)


def test_validate_accepts_current_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = _docker_disk_path(tmp_path)
    assert docker_disk.validate_docker_data_disk(path) == path.resolve()


def test_validate_accepts_legacy_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = tmp_path / "Docker" / "wsl" / "disk" / "docker" / "_data.vhdx"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"vhdx")
    assert docker_disk.validate_docker_data_disk(path) == path.resolve()


def test_preview_does_not_stop_or_compact(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = _docker_disk_path(tmp_path)
    runner = FakeRunner()

    result = docker_disk.compact_docker_data_disk(path, dry_run=True, runner=runner)

    assert result["outcome"] == "preview"
    assert result["applied"] is False
    assert result["inspection"]["would_stop_docker_desktop"] is True
    assert not any(call[:3] == ["docker", "desktop", "stop"] for call in runner.calls)
    assert not any(any("Mount-VHD" in arg for arg in call) for call in runner.calls)


def test_confirm_stops_compacts_and_reports_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = _docker_disk_path(tmp_path)
    runner = FakeRunner()

    result = docker_disk.compact_docker_data_disk(path, confirm=True, runner=runner)

    assert result["outcome"] == "compacted"
    assert result["applied"] is True
    assert result["before_bytes"] == 1000
    assert result["after_bytes"] == 400
    assert result["reclaimed_bytes"] == 600
    assert result["docker_desktop_status"] == "stopped"
    assert result["compact_mode"] == "full-read-only"
    assert result["docker_stopped_by_operation"] is True
    assert any(call[:3] == ["docker", "desktop", "stop"] for call in runner.calls)
    assert any(any("Mount-VHD" in arg for arg in call) for call in runner.calls)


def test_confirm_preserves_already_stopped_state(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = _docker_disk_path(tmp_path)
    runner = FakeRunner(status="stopped", attached=False)

    result = docker_disk.compact_docker_data_disk(path, confirm=True, runner=runner)

    assert result["outcome"] == "compacted"
    assert result["docker_stopped_by_operation"] is False
    assert not any(call[:3] == ["docker", "desktop", "stop"] for call in runner.calls)


def test_known_stopped_cli_response_requires_detached_vhd(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = _docker_disk_path(tmp_path)
    runner = StoppedCliRunner(status="stopped", attached=False)

    result = docker_disk.compact_docker_data_disk(path, dry_run=True, runner=runner)

    assert result["inspection"]["docker_desktop_status"] == "stopped"


def test_known_stopped_cli_response_is_refused_while_attached(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = _docker_disk_path(tmp_path)
    runner = StoppedCliRunner(status="stopped", attached=True)

    with pytest.raises(docker_disk.DockerDiskCompactionError, match="Docker Desktop status"):
        docker_disk.inspect_docker_disk_compaction(path, runner=runner)


def test_missing_mount_privilege_uses_detached_prezeroed_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = _docker_disk_path(tmp_path)
    runner = PrivilegeFallbackRunner(status="stopped", attached=False)

    result = docker_disk.compact_docker_data_disk(path, confirm=True, runner=runner)

    assert result["outcome"] == "compacted"
    assert result["compact_mode"] == "detached-prezeroed"
    assert any("Mode Prezeroed" in call[-1] for call in runner.calls)


def test_confirm_blocks_when_vhd_remains_attached(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = _docker_disk_path(tmp_path)
    runner = FakeRunner(status="stopped", attached=True)

    result = docker_disk.compact_docker_data_disk(path, confirm=True, runner=runner)

    assert result["outcome"] == "blocked"
    assert "still attached" in result["error"]
    assert not any(any("Mount-VHD" in arg for arg in call) for call in runner.calls)


def test_inspection_fails_closed_without_optimize_vhd(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_disk, "_is_windows", lambda: True)
    path = _docker_disk_path(tmp_path)
    runner = FakeRunner()
    original = runner.__call__

    def no_optimize(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[0] == "powershell.exe" and "Get-VHD" in argv[-1]:
            payload = json.loads(result.stdout)
            payload["OptimizeVhdAvailable"] = False
            return _completed(argv, stdout=json.dumps(payload))
        return result

    with pytest.raises(docker_disk.DockerDiskCompactionError, match="Optimize-VHD"):
        docker_disk.inspect_docker_disk_compaction(path, runner=no_optimize)
