"""Bounded subprocess contracts used by benchmark replay."""

import json
import os
import subprocess
import sys
import time

import pytest

from anvil_serving import transports


def test_bounded_process_uses_requested_cwd_and_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAY_PARENT_ONLY", "must-not-inherit")
    environment = {"REPLAY_ALLOWED": "yes", "LC_CTYPE": "C.UTF-8"}
    result = transports._run_bounded_process(
        [sys.executable, "-c", "import json,os; print(json.dumps([os.getcwd(),dict(os.environ)]))"],
        timeout=5,
        max_output_bytes=4096,
        cwd=tmp_path,
        env=environment,
    )
    assert result.returncode == 0
    assert result.stderr == b""
    assert json.loads(result.stdout) == [str(tmp_path), environment]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group contract")
def test_bounded_process_stays_in_worker_process_group():
    result = transports._run_bounded_process(
        [sys.executable, "-c", "import os; print(os.getpgrp())"],
        timeout=5,
        max_output_bytes=128,
        env={},
    )
    assert result.returncode == 0
    assert int(result.stdout) == os.getpgrp()


@pytest.mark.parametrize(
    "script,expected_error",
    [
        ("import time; time.sleep(30)", subprocess.TimeoutExpired),
        ("import os; os.write(1,b'a'*768); os.write(2,b'b'*768)", transports._ProcessOutputOverflow),
        ("import os\nwhile True: os.write(1,b'a'*65536)", transports._ProcessOutputOverflow),
        ("import os\nwhile True: os.write(2,b'b'*65536)", transports._ProcessOutputOverflow),
    ],
)
def test_bounded_process_refuses_timeout_or_output_and_reaps_child(monkeypatch, script, expected_error):
    processes = []
    popen = subprocess.Popen

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(transports.subprocess, "Popen", launch)
    started = time.monotonic()
    with pytest.raises(expected_error):
        transports._run_bounded_process(
            [sys.executable, "-c", script], timeout=0.5, max_output_bytes=1024, env={}
        )
    assert time.monotonic() - started < 5
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed
    assert processes[0].stderr.closed
