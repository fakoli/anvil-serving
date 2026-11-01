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


def test_log_root_is_private_under_shared_umask_and_rejects_unsafe_existing_root(tmp_path):
    import os
    if os.name == "nt":
        pytest.skip("POSIX directory permissions")
    logs = tmp_path / "logs"
    previous = os.umask(0o002)
    try:
        image_build._private_run_directory(logs, None)
    finally:
        os.umask(previous)
    assert logs.stat().st_mode & 0o777 == 0o700
    logs.chmod(0o775)
    with pytest.raises(image_build.ImageBuildError, match="writable by other users"):
        image_build._private_run_directory(logs, None)
    assert logs.stat().st_mode & 0o777 == 0o775
