"""Resource ceilings and failure boundaries for the managed image builder."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving import image_build


@pytest.fixture
def config(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    path = tmp_path / "image-builds.toml"
    path.write_text('''schema = "anvil-serving.image-builds/v1"
[builds.worker]
context = "."
dockerfile = "Dockerfile"
image = "anvil-worker:candidate"
cpus = 2
memory_mib = 8192
''', encoding="utf-8")
    return path


def test_preview_is_side_effect_free_with_explicit_limits(config, tmp_path):
    plan = image_build.build_plan("worker", config)
    def forbidden(*args, **kwargs):
        pytest.fail("preview invoked Docker")
    result = image_build.execute_build(plan, runner=forbidden, log_dir=tmp_path / "logs")
    assert result["dry_run"] and result["ok"]
    assert not (tmp_path / "logs").exists()
    assert "memory=8192m" in result["argv"]
    assert "memory-swap=8192m" in result["argv"]
    assert "cpu-period=100000" in result["argv"]
    assert "cpu-quota=200000" in result["argv"]


@pytest.mark.parametrize(("old", "new"), [
    ("cpus = 2", "cpus = true"), ("cpus = 2", "cpus = inf"),
    ("cpus = 2", "cpus = 0"), ("cpus = 2", "cpus = 16"),
    ("memory_mib = 8192", "memory_mib = -1"),
    ("memory_mib = 8192", "memory_mib = 65536"),
    ('image = "anvil-worker:candidate"', 'image = "--push"'),
    ('dockerfile = "Dockerfile"', 'dockerfile = "../Dockerfile"'),
    ('[builds.worker]', '[builds.worker]\nsecret = "not-accepted"'),
])
def test_invalid_declaration_refuses_before_docker(config, old, new):
    config.write_text(config.read_text().replace(old, new))
    with pytest.raises(image_build.ImageBuildError):
        image_build.build_plan("worker", config)


def test_parallel_stages_are_rejected(config):
    (config.parent / "Dockerfile").write_text("FROM scratch AS first\nFROM scratch\n")
    with pytest.raises(image_build.ImageBuildError, match="single-stage"):
        image_build.build_plan("worker", config)


def test_old_cli_cannot_fall_back_to_unbounded_build(config, tmp_path):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="old build help")
    with pytest.raises(image_build.ImageBuildError, match="lacks --resource"):
        image_build.execute_build(image_build.build_plan("worker", config), confirm=True,
                                  runner=run, log_dir=tmp_path / "logs")
    assert calls == [["docker", "buildx", "build", "--help"]]
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("mode", ["success", "failure", "timeout", "missing-id"])
def test_build_outcomes_never_operate_a_serve(config, tmp_path, mode):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "--help":
            return SimpleNamespace(returncode=0, stdout="--resource")
        if argv[1] == "info":
            return SimpleNamespace(returncode=0, stdout="linux\n")
        pytest.fail("unexpected Docker operation")
    def build(argv, **kwargs):
        calls.append(argv)
        assert argv[:3] == ["docker", "buildx", "build"]
        assert "--resource" in argv and "--load" in argv and "--push" not in argv
        assert argv[argv.index("--file") + 1] == "-"
        assert kwargs["source_bytes"] == (config.parent / "Dockerfile").read_bytes()
        kwargs["log"].write(b"bounded build diagnostics\n")
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, 30)
        if mode == "success":
            Path(argv[argv.index("--iidfile") + 1]).write_text("sha256:" + "a" * 64)
        return {"returncode": 1 if mode == "failure" else 0}
    def directory(path, runner):
        path.mkdir()
        return path
    result = image_build.execute_build(image_build.build_plan("worker", config), confirm=True,
                                      runner=run, build_runner=build, directory_factory=directory,
                                      log_dir=tmp_path / "logs")
    assert result["ok"] == (mode == "success")
    assert len(calls) == 3
    assert Path(result["log_path"]).read_text() == "bounded build diagnostics\n"
    assert result["image_id"] == ("sha256:" + "a" * 64 if mode == "success" else None)


def test_dry_run_overrides_confirmation(config, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run invoked Docker")
    result = image_build.execute_build(image_build.build_plan("worker", config), confirm=True,
                                      dry_run=True, runner=forbidden, log_dir=tmp_path)
    assert result["dry_run"]


def test_changed_dockerfile_cannot_expand_validated_build(config, tmp_path):
    plan = image_build.build_plan("worker", config)
    (config.parent / "Dockerfile").write_text("FROM scratch\nFROM scratch\n")
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="--resource" if "--help" in argv else "linux")
    with pytest.raises(image_build.ImageBuildError, match="changed after"):
        image_build.execute_build(plan, confirm=True, runner=run, log_dir=tmp_path / "logs")
    assert not (tmp_path / "logs").exists()


def test_stream_retains_only_bounded_output_and_feeds_exact_stdin(tmp_path):
    with (tmp_path / "output.log").open("wb") as log:
        result = image_build._stream_build(
            [sys.executable, "-c", "import sys; assert sys.stdin.buffer.read() == b'bound source'; sys.stdout.buffer.write(b'x'*100000)"],
            source_bytes=b"bound source", log=log, timeout=10, max_log_bytes=1024,
        )
    assert result == {"returncode": 0, "log_truncated": True}
    assert (tmp_path / "output.log").stat().st_size == 1024


def test_private_directory_is_unique_and_protected(tmp_path):
    import os
    if os.name == "nt":
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout='"operator","S-1-5-21-1000"')
        path = image_build._private_run_directory(tmp_path / "logs", run)
        assert calls[1][0] == "icacls"
        assert "/inheritance:r" in calls[1]
        assert "*S-1-5-21-1000:(OI)(CI)F" in calls[1]
    else:
        path = image_build._private_run_directory(tmp_path / "logs", None)
        assert path.stat().st_mode & 0o777 == 0o700
    assert path.is_dir()


def test_timeout_terminates_child_that_inherits_the_output_pipe(tmp_path):
    import time
    marker = tmp_path / "orphan.txt"
    child = "import time; from pathlib import Path; time.sleep(1.5); Path(%r).write_text('orphan')" % str(marker)
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); print('spawned',flush=True); time.sleep(30)" % child
    with (tmp_path / "timeout.log").open("wb") as log:
        result = image_build._stream_build([sys.executable, "-c", parent],
                                           source_bytes=b"", log=log, timeout=0.5)
    assert "timed out" in result["error"]
    assert "daemon_state_unverified" in result["cancellation"]
    assert not result.get("output_error")
    time.sleep(1.6)
    assert not marker.exists()
