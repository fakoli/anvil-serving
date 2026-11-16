from pathlib import Path

from anvil_serving.workbench_app.task_sandbox import CommandResult, ProductionTaskSandbox


BASE = "a" * 40


def _config(owner_path=None):
    owner = owner_path.stat() if owner_path is not None else None
    return {"engine_binary": "/usr/bin/docker", "image": "example/pi@sha256:" + "b" * 64, "uid": owner.st_uid if owner else 1000, "gid": owner.st_gid if owner else 1000, "cpus": 2, "memory_bytes": 2 * 1024**3, "pids": 128}


def test_capture_uses_only_pinned_unprivileged_networkless_containers(tmp_path):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        command = tuple(argv)
        if "rev-parse" in command:
            output = (BASE + "\n").encode()
        elif "--raw" in command:
            output = b":100644 100755 " + b"0" * 40 + b" " + b"1" * 40 + b" M\0src/feature.py\0"
        elif "ls-files" in command:
            output = b"new.py\0"
        elif "--no-index" in command:
            output = b"diff --git a/new.py b/new.py\nnew file mode 100644\n"
        else:
            output = b"diff --git a/src/feature.py b/src/feature.py\n"
        return CommandResult(0, output, b"", 0.01)
    source = tmp_path / "runner"
    source.mkdir()
    result = ProductionTaskSandbox(_config(), run=run).capture(source, BASE)
    assert result["files"] == [{"path": "src/feature.py", "status": "M", "mode": "100755"}, {"path": "new.py", "status": "A", "mode": "100644"}]
    for argv, kwargs in calls:
        assert argv[:3] == ("/usr/bin/docker", "run", "--rm")
        assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
        assert "--read-only" in argv and "--cap-drop" in argv and "ALL" in argv
        assert kwargs["limit"] <= 2 * 32 * 1024


def test_crash_recovery_refuses_target_with_only_an_executable_mode_difference(tmp_path):
    target, verifier, runner = tmp_path / "claim", tmp_path / "verify", tmp_path / "runner"
    for path in (target, verifier, runner):
        path.mkdir()
    scripts = []

    def run(argv, **_kwargs):
        if argv[0] == "git":
            output = (BASE + "\n").encode() if "rev-parse" in argv else b""
            return CommandResult(0, output, b"", 0.01)
        script = next(value for value in argv if isinstance(value, str) and ("tree_equal" in value or "for ((n=" in value))
        scripts.append(script)
        if "tree_manifest()" in script:
            return CommandResult(0, b"other", b"", 0.01)
        mount = next(value for value in argv if "target=/results" in value)
        result_root = Path(mount.split("source=", 1)[1].split(",target=", 1)[0])
        (result_root / "0.meta").write_text("0 0\n", encoding="ascii")
        (result_root / "0.out").write_text("", encoding="utf-8")
        (result_root / "0.err").write_text("", encoding="utf-8")
        return CommandResult(0, b"", b"", 0.01)

    sandbox = ProductionTaskSandbox(_config(tmp_path), run=run)
    try:
        sandbox.verify_transfer(runner, verifier, target, BASE, b"diff --git a/x b/x\n", ("true",), already_transferred=True)
    except Exception as exc:
        assert getattr(exc, "code", None) == "transfer_recovery_required"
    else:
        raise AssertionError("mode-only target difference must refuse crash recovery")
    assert any("find . -path ./.git -prune" in script and "-printf '%y %m %p" in script for script in scripts)
