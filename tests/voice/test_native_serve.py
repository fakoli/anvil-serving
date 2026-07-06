from __future__ import annotations

import signal
from types import SimpleNamespace

from anvil_serving.voice.serves.native import NativeServe, NativeServeConfig


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status


def _cfg(tmp_path, **overrides):
    data = {
        "kind": "stt",
        "base_url": "http://127.0.0.1:30010/v1",
        "model": "mlx-stt",
        "start_command": "python -m mlx_audio.server --host 127.0.0.1 --port 30010",
        "workdir": str(tmp_path),
        "pid_file": str(tmp_path / "stt.pid"),
        "log_file": str(tmp_path / "stt.log"),
        "ready_timeout": 0.1,
        "stop_timeout": 0.1,
    }
    data.update(overrides)
    return NativeServeConfig(**data)


def test_native_bring_up_starts_process_writes_pid_and_waits_ready(tmp_path):
    opened = {"count": 0}
    popen_calls = []

    def fake_open(url, timeout):
        opened["count"] += 1
        assert url == "http://127.0.0.1:30010/v1/models"
        if opened["count"] == 1:
            raise OSError("not ready")
        return _Resp()

    def fake_popen(argv, **kwargs):
        popen_calls.append((argv, kwargs))
        return SimpleNamespace(pid=12345)

    def fake_kill(pid, sig):
        assert pid == 12345
        assert sig == 0

    serve = NativeServe(
        _cfg(tmp_path),
        _open=fake_open,
        _popen=fake_popen,
        _kill=fake_kill,
        _sleep=lambda _: None,
    )

    result = serve.bring_up()

    assert result["returncode"] == 0
    assert result["reason"] == "started"
    assert result["ready"] is True
    assert (tmp_path / "stt.pid").read_text(encoding="utf-8") == "12345\n"
    assert popen_calls[0][0][:3] == ["python", "-m", "mlx_audio.server"]
    assert popen_calls[0][1]["cwd"] == str(tmp_path)
    assert (tmp_path / "stt.log").exists()


def test_native_tear_down_terminates_pid_file_process_and_removes_pid(tmp_path):
    pid_file = tmp_path / "stt.pid"
    pid_file.write_text("12345\n", encoding="utf-8")
    alive = {"value": True}
    kill_calls = []

    def fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        if sig == 0:
            if not alive["value"]:
                raise ProcessLookupError()
            return
        if sig == signal.SIGTERM:
            alive["value"] = False

    serve = NativeServe(
        _cfg(tmp_path),
        _open=lambda url, timeout: (_ for _ in ()).throw(OSError("down")),
        _kill=fake_kill,
        _sleep=lambda _: None,
    )

    result = serve.tear_down()

    assert result["returncode"] == 0
    assert result["reason"] == "pid_file"
    assert not pid_file.exists()
    assert (12345, signal.SIGTERM) in kill_calls


def test_native_tear_down_is_noop_when_endpoint_is_down_even_with_stop_command(tmp_path):
    def fail_run(argv, **kwargs):
        raise AssertionError("stop_command should not run for an already-down endpoint")

    serve = NativeServe(
        _cfg(tmp_path, stop_command="pkill -f 'mlx_audio.server.*--port 30010'"),
        _open=lambda url, timeout: (_ for _ in ()).throw(OSError("down")),
        _run=fail_run,
    )

    result = serve.tear_down()

    assert result["returncode"] == 0
    assert result["reason"] == "already_down"


def test_native_tear_down_uses_stop_command_for_ready_unmanaged_listener(tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    serve = NativeServe(
        _cfg(tmp_path, stop_command="pkill -f 'mlx_audio.server.*--port 30010'"),
        _open=lambda url, timeout: _Resp(),
        _run=fake_run,
    )

    result = serve.tear_down()

    assert result["returncode"] == 0
    assert result["reason"] == "stop_command"
    assert seen["argv"] == ["pkill", "-f", "mlx_audio.server.*--port 30010"]
    assert seen["cwd"] == str(tmp_path)
