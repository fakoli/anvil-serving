from pathlib import Path
import os
import sys
import time

import pytest

from anvil_serving.connect import _qualification_vm_process as subject
from anvil_serving.connect.qualification import QualificationError


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux process controls")


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


def test_ambient_credentials_are_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PRIVATE_TOKEN", "do-not-inherit")
    result = run(tmp_path, "import os; assert 'SYNTHETIC_PRIVATE_TOKEN' not in os.environ; print('ok')")
    assert result.returncode == 0 and result.output == b"ok\n"


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

    def wait_terminal(timeout):
        deadline = time.monotonic() + timeout
        while True:
            try:
                raw = status.read_text()
            except (FileNotFoundError, ProcessLookupError):
                # procfs may open the entry just before the process is reaped.
                return True
            state = raw.split(") ", 1)[1][:1] if ") " in raw else ""
            if state == "Z":
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.01, remaining))

    # SIGKILL delivery to a process group is asynchronous. A reparented zombie
    # is terminal even when PID1 has not reaped it yet.
    assert wait_terminal(2), "timed out waiting for killed descendant"
