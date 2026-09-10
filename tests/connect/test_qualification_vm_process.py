from pathlib import Path
import os
import sys

import pytest

from anvil_serving.connect import _qualification_vm_process as subject
from anvil_serving.connect.qualification import QualificationError


def run(tmp_path, script, **kwargs):
    return subject.execute([sys.executable, "-I", "-c", script], home=tmp_path,
                           cpus=(min(os.sched_getaffinity(0)),), timeout=kwargs.pop("timeout", 3),
                           **kwargs)


def assert_reason(failure, expected):
    assert failure.value.reason == expected
    measurements = failure.value.measurements
    assert set(measurements) == {"elapsed_ms", "peak_rss_bytes", "output_bytes", "returncode"}
    assert all(type(measurements[name]) is int and measurements[name] >= 0
               for name in ("elapsed_ms", "peak_rss_bytes", "output_bytes"))
    assert measurements["returncode"] is None or type(measurements["returncode"]) is int


def test_console_is_discarded_and_one_fragmented_result_is_retained(tmp_path):
    result = run(tmp_path, "import os; os.write(1,b'ordinary console\\nRESULT '); os.write(1,b'{\\\"ok\\\":true}\\n')",
                 retained_prefix=b"RESULT ")
    assert result.returncode == 0
    assert result.output == b'{"ok":true}'
    assert result.output_bytes > len(result.output)


@pytest.mark.parametrize(("script", "reason"), [
    ("print('RESULT {}'); print('RESULT {}')", "record-duplicate"),
    ("import os; os.write(1,b'RESULT {}')", "record-incomplete"),
    ("print('no result')", "record-incomplete"),
])
def test_duplicate_missing_or_truncated_record_fails_closed(tmp_path, script, reason):
    with pytest.raises(QualificationError) as failure:
        run(tmp_path, script, retained_prefix=b"RESULT ")
    assert_reason(failure, reason)


def test_success_marker_does_not_hide_timeout(tmp_path):
    with pytest.raises(QualificationError) as failure:
        run(tmp_path, "import time; print('RESULT {}',flush=True); time.sleep(20)",
            retained_prefix=b"RESULT ", timeout=0.2)
    assert failure.value.code == "runner-timeout"
    assert_reason(failure, "timeout")


def test_console_output_is_bounded_and_redacted_even_when_discarded(tmp_path):
    private = "private-console-sentinel"
    with pytest.raises(QualificationError) as failure:
        run(tmp_path, f"print({private!r}*5000)", retained_prefix=b"RESULT ", maximum_output=1024)
    assert_reason(failure, "output-limit")
    assert private not in str(failure.value)
    assert private not in repr(vars(failure.value))


@pytest.mark.parametrize(("script", "reason"), [
    ("print('RESULT ' + 'x'*128)", "record-oversized"),
    ("import sys; sys.stdout.write('x'*128); sys.stdout.flush()", "line-limit"),
])
def test_record_and_line_limits_have_distinct_closed_reasons(tmp_path, script, reason):
    with pytest.raises(QualificationError) as failure:
        run(tmp_path, script, retained_prefix=b"RESULT ", maximum_record=32)
    assert_reason(failure, reason)


def test_ambient_credentials_are_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PRIVATE_TOKEN", "do-not-inherit")
    result = run(tmp_path, "import os; assert 'SYNTHETIC_PRIVATE_TOKEN' not in os.environ; print('ok')")
    assert result.returncode == 0 and result.output == b"ok\n"


def test_resource_sample_failure_terminates_owned_child(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "_rss", lambda pid: (_ for _ in ()).throw(OSError()))
    with pytest.raises(QualificationError) as failure:
        run(tmp_path, "import time; time.sleep(20)")
    assert_reason(failure, "process-error")


def test_rss_threshold_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "_rss", lambda pid: 101)
    with pytest.raises(QualificationError) as failure:
        run(tmp_path, "import time; time.sleep(20)", rss_limit=100)
    assert_reason(failure, "rss-limit")


def test_watched_file_limit_has_a_closed_reason(tmp_path):
    watched = tmp_path / "overlay"
    watched.write_bytes(b"x" * 101)
    with pytest.raises(QualificationError) as failure:
        run(tmp_path, "import time; time.sleep(20)", watched_file=watched, file_limit=100)
    assert_reason(failure, "file-limit")


def test_cleanup_failure_has_a_closed_reason(tmp_path, monkeypatch):
    children = []
    original = subject._terminate
    popen = subject.subprocess.Popen

    def capture_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        children.append(process)
        return process

    def leave_unreaped(process):
        raise OSError()

    monkeypatch.setattr(subject.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(subject, "_terminate", leave_unreaped)
    try:
        with pytest.raises(QualificationError) as failure:
            run(tmp_path, "import time; time.sleep(30)", timeout=0.2)
        assert failure.value.stage == "cleanup"
        assert_reason(failure, "cleanup-failure")
        assert failure.value.measurements["returncode"] is None
        assert len(children) == 1 and children[0].returncode is None
    finally:
        for child in children:
            original(child)


def test_timeout_kills_descendant_that_ignores_termination(tmp_path):
    pid_file = tmp_path / "child-pid"
    child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
    script = ("import subprocess,sys,pathlib,time; "
              f"p=subprocess.Popen([sys.executable,'-I','-c',{child!r}]); "
              f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(30)")
    with pytest.raises(QualificationError):
        run(tmp_path, script, timeout=0.3)
    pid = int(pid_file.read_text())
    status = Path(f"/proc/{pid}/stat")
    # Reparented zombies can remain briefly for PID1 to reap; they cannot run.
    if status.exists():
        assert status.read_text().split(") ", 1)[1][0] == "Z"


def test_keyboard_interrupt_reaps_owned_child_and_is_preserved(tmp_path, monkeypatch):
    selector_type = subject.selectors.DefaultSelector
    terminated = []
    original = subject._terminate

    class InterruptedSelector(selector_type):
        def select(self, timeout=None):
            raise KeyboardInterrupt

    def terminate(process):
        original(process)
        terminated.append(process.returncode)

    monkeypatch.setattr(subject.selectors, "DefaultSelector", InterruptedSelector)
    monkeypatch.setattr(subject, "_terminate", terminate)
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, "import time; time.sleep(30)")
    assert len(terminated) == 1 and terminated[0] is not None
