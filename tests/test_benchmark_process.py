"""Bounded subprocess contracts used by benchmark replay."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from anvil_serving import transports


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


def test_owned_process_group_refuses_nonowner_before_launch(monkeypatch):
    monkeypatch.setattr(os, "getpgrp", lambda: -1, raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("launched outside worker"))
    with pytest.raises(transports._ProcessGroupError, match="dedicated worker"):
        transports._run_bounded_process(
            [sys.executable], timeout=1, max_output_bytes=1024, owned_process_group=True
        )


_DESCENDANT = """
import os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(30)
"""

_GRADER = """
import os, subprocess, sys, time
from pathlib import Path
mode, pid_file, descendant = sys.argv[1:]
streams = {} if mode in ('success', 'pipe_error') else {
    'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
}
subprocess.Popen([sys.executable, '-c', descendant, pid_file], **streams)
for _ in range(200):
    if Path(pid_file).exists():
        break
    time.sleep(0.005)
else:
    raise RuntimeError('descendant did not start')
if mode == 'timeout':
    time.sleep(30)
elif mode == 'overflow':
    os.write(1, b'x' * 8192)
    time.sleep(30)
"""

_WORKER = """
import json, subprocess, sys, time
from anvil_serving import transports
mode, pid_file, grader, descendant = sys.argv[1:]
native_popen = subprocess.Popen
graders = []

class BrokenPipe:
    def __init__(self, pipe):
        self.pipe = pipe
    def read(self, _size):
        raise OSError('injected pipe failure')
    def close(self):
        self.pipe.close()
    @property
    def closed(self):
        return self.pipe.closed

def launch(argv, **kwargs):
    process = native_popen(argv, **kwargs)
    if argv[0] == sys.executable:
        graders.append(process)
        if mode == 'pipe_error':
            process.stdout = BrokenPipe(process.stdout)
    return process

transports.subprocess.Popen = launch
started = time.monotonic()
try:
    result = transports._run_bounded_process(
        [sys.executable, '-c', grader, mode, pid_file, descendant],
        timeout=1, max_output_bytes=1024, env={}, owned_process_group=True,
    )
    assert result.returncode == 0
    outcome = 'success'
except (subprocess.TimeoutExpired, transports._ProcessOutputOverflow, transports._ProcessPipeError) as exc:
    outcome = type(exc).__name__
assert len(graders) == 1
assert graders[0].poll() is not None
assert graders[0].stdout.closed and graders[0].stderr.closed
print(json.dumps({'outcome': outcome, 'elapsed': time.monotonic() - started}))
"""


@pytest.mark.skipif(os.name != "posix", reason="POSIX owned worker process groups")
@pytest.mark.parametrize("mode,expected", [
    ("timeout", "TimeoutExpired"),
    ("overflow", "_ProcessOutputOverflow"),
    ("success", "success"),
    ("pipe_error", "_ProcessPipeError"),
])
def test_owned_worker_cleans_descendant_without_touching_external_group(tmp_path, mode, expected):
    pid_file = tmp_path / "descendant.pid"
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env={},
    )
    worker = None
    try:
        worker = subprocess.Popen(
            [sys.executable, "-c", _WORKER, mode, str(pid_file), _GRADER, _DESCENDANT],
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
        stdout, stderr = worker.communicate(timeout=12)
        assert worker.returncode == 0, stderr.decode()
        observed = json.loads(stdout)
        assert observed["outcome"] == expected
        assert observed["elapsed"] < 7
        descendant = int(pid_file.read_text())
        status = subprocess.run(
            ["ps", "-p", str(descendant), "-o", "stat="],
            capture_output=True, text=True, timeout=1, check=False,
        )
        assert not status.stdout.strip() or status.stdout.lstrip().startswith("Z")
        assert sentinel.poll() is None
    finally:
        if worker is not None:
            try:
                os.killpg(worker.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            worker.communicate(timeout=3)
        sentinel.kill()
        sentinel.wait(timeout=3)
