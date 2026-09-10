from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from anvil_serving.connect import qualification as runner
from anvil_serving.connect import qualification_container as image
from anvil_serving.connect import qualification_container_run as subject
from tests.connect.test_qualification_container import _inputs


def _result(status="passed"):
    return json.dumps({"tests": [{"name": name, "status": status, "duration_seconds": 0.1} for name in runner._TESTS], "escalated": False}).encode()


@pytest.mark.parametrize("raw", [b"{}", b"private-sentinel", b'{"tests":[],"escalated":false}', _result("private-sentinel"), _result().replace(b'0.1', b'NaN')])
def test_container_result_refuses_unknown_or_incomplete_metadata(raw):
    with pytest.raises(runner.QualificationError) as caught:
        subject._cases(raw, runner._TESTS)
    assert caught.value.case_counts is None
    assert "private-sentinel" not in str(caught.value)


def test_attached_output_is_bounded_and_return_code_matters():
    environment = {"PATH": "/usr/bin:/bin"}
    assert subject._attached([sys.executable, "-c", 'print("safe")'], environment, 2) == b"safe\n"
    for script in ('print("x"*9000)', 'raise SystemExit(1)', 'import time; time.sleep(5)'):
        with pytest.raises(runner.QualificationError):
            subject._attached([sys.executable, "-c", script], environment, 1)


@pytest.mark.parametrize("execution_fails,cleanup_fails", [(False, False), (True, False), (False, True)])
def test_container_lane_is_offline_private_and_always_removes_owned_container(tmp_path, monkeypatch, execution_fails, cleanup_fails):
    config, paths = _inputs(tmp_path)
    for filename in ("qualification.py", "_qualification_supervisor.py", "qualification_container_run.py"):
        destination = paths["source"] / "anvil_serving/connect" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(subject.__file__).with_name(filename).read_bytes())
    monkeypatch.setattr(runner, "_source_metadata", lambda _: {"revision": "a"*40, "dirty": False})
    monkeypatch.setattr(runner, "_tracked_connect_files", lambda _: [Path("connect/go.mod")])
    monkeypatch.setattr(image, "_cached_receipt", lambda *args: "sha256:"+"c"*64)
    monkeypatch.setattr(image, "_image", lambda *args, **kwargs: "sha256:"+"c"*64)
    monkeypatch.setenv("PRIVATE_SENTINEL", "private-sentinel")
    commands = []
    stages = []
    def attached(args, environment, timeout):
        assert args[:4] == ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", "run"]
        for flag, value in (("--network", "none"), ("--pull", "never"), ("--log-driver", "none"), ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges")):
            assert args[args.index(flag)+1] == value
        selected_cpus = args[args.index("--cpuset-cpus")+1].split(",")
        assert 1 <= len(selected_cpus) <= 2
        assert set(map(int, selected_cpus)) <= os.sched_getaffinity(0)
        assert "--read-only" in args and "--privileged" not in args and "--gpus" not in args and "--publish" not in args
        assert set(environment) == {"PATH", "LANG", "LC_ALL", "DOCKER_CONFIG"}
        assert "private-sentinel" not in json.dumps(environment)
        mount = args[args.index("--volume")+1]
        assert mount.endswith(":/source:ro")
        stage = Path(mount.removesuffix(":/source:ro"))
        stages.append(stage)
        assert (stage / "connect/go.mod").is_file()
        assert not (stage / "private-sentinel.env").exists()
        if execution_fails:
            raise runner._error("runner-failed", "safe failure", execution_started=True, stage="execution")
        return _result()
    def docker(args, **kwargs):
        commands.append(args)
        return (0, b"remaining" if cleanup_fails and args[0] == "container" else b"")
    monkeypatch.setattr(subject, "_attached", attached)
    monkeypatch.setattr(image, "_docker", docker)
    if execution_fails or cleanup_fails:
        with pytest.raises(runner.QualificationError) as caught:
            subject.qualify(config)
        assert caught.value.stage == ("cleanup" if cleanup_fails else "execution")
        if cleanup_fails:
            assert caught.value.case_counts["passed"] == 2
    else:
        result = subject.qualify(config)
        assert result["ok"] is True and result["counts"]["passed"] == 2
        root = Path(result["artifact_dir"])
        assert {path.name for path in root.iterdir()} == {"evidence.json", "result.json", "SHA256SUMS", "junit.xml"}
        for line in (root / "SHA256SUMS").read_text().splitlines():
            digest, filename = line.split("  ")
            assert hashlib.sha256((root / filename).read_bytes()).hexdigest() == digest
    assert any(args[:2] == ["rm", "--force"] for args in commands)
    assert stages and all(not path.exists() for path in stages)


def test_missing_image_receipt_never_downloads_or_executes(tmp_path, monkeypatch):
    config, _ = _inputs(tmp_path)
    monkeypatch.setattr(runner, "_source_metadata", lambda _: {})
    monkeypatch.setattr(image, "_cached_receipt", lambda *args: None)
    monkeypatch.setattr(subject, "_attached", lambda *args: pytest.fail("unexpected execution"))
    monkeypatch.setattr(image, "_docker", lambda *args, **kwargs: pytest.fail("unexpected Docker operation"))
    with pytest.raises(runner.QualificationError, match="prepare"):
        subject.qualify(config)


@pytest.mark.parametrize("uid,gid", [(0,1000),(1000,0),(0,0)])
def test_root_invocation_is_refused_before_discovery(monkeypatch, uid, gid):
    monkeypatch.setattr(subject.os, "geteuid", lambda: uid)
    monkeypatch.setattr(subject.os, "getegid", lambda: gid)
    monkeypatch.setattr(runner, "_read_config", lambda *args: pytest.fail("discovery before root guard"))
    with pytest.raises(runner.QualificationError, match="non-root"):
        subject.qualify()
