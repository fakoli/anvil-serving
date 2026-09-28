"""The installed readback command is fixed, bounded and fails closed."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from anvil_serving.control_plane.controller.propagation_job_store import ExecutionProfile, PropagationJobError
from anvil_serving.control_plane.controller.propagation_reader import FixedFleetReader


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the owner reader runs on Linux")


def _reader(tmp_path, source, *, budget=2, limit=4096):
    script = tmp_path / "reader.py"
    script.write_text(source, encoding="utf-8")
    profile = ExecutionProfile(
        "reader-1", "a" * 64, (sys.executable, str(script)),
        hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        artifact_pins=((str(script), hashlib.sha256(script.read_bytes()).hexdigest()),),
        cwd=str(tmp_path), environment={"PATH": os.environ.get("PATH", "")},
        budget_seconds=budget, output_limit=limit,
    )
    return FixedFleetReader(profile), script


def test_reader_passes_only_fixed_typed_input_and_rechecks_pin(tmp_path):
    reader, script = _reader(tmp_path,
        "import json,sys\nrequest=json.load(sys.stdin)\n"
        "print(json.dumps({'kind':request['kind'],'contract':request['contract'],"
        "'job':request.get('job'),'verification_id':request.get('verification_id')}))\n")
    assert reader.preview(b'{"revision":"revision-1"}') == {
        "kind": "preview", "contract": {"revision": "revision-1"}, "job": None,
        "verification_id": None,
    }
    assert reader.observe(b'{"revision":"revision-1"}', {
        "job_id": "job-1", "operation_id": "operation-1", "contract_digest": "a" * 64,
        "intent_id": "intent-1", "private_value": "must-not-escape",
    }, "verify", "verification-1")["job"] == {
        "job_id": "job-1", "operation_id": "operation-1", "contract_digest": "a" * 64,
        "intent_id": "intent-1",
    }
    script.write_text("print('{}')\n", encoding="utf-8")
    with pytest.raises(PropagationJobError, match="reader_unavailable"):
        reader.preview(b"{}")


@pytest.mark.parametrize("source,budget,limit", [
    ("import time\ntime.sleep(3)\n", 1, 4096),
    ("print('x'*2048)\n", 2, 1024),
    ("raise SystemExit(3)\n", 2, 4096),
    ("import subprocess,sys\nsubprocess.Popen([sys.executable,'-c','import time;time.sleep(3)'],"
     "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nprint('{}')\n", 2, 4096),
])
def test_reader_refuses_timeout_oversize_and_failure(tmp_path, source, budget, limit):
    reader, _ = _reader(tmp_path, source, budget=budget, limit=limit)
    with pytest.raises(PropagationJobError, match="reader_unavailable"):
        reader.preview(b"{}")


def test_reader_bounds_concurrent_children(tmp_path):
    marker = tmp_path / "started"
    reader, _ = _reader(tmp_path,
        f"from pathlib import Path\nimport time\nPath({str(marker)!r}).touch()\n"
        "time.sleep(0.5)\nprint('{}')\n", budget=2)
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(reader.preview, b"{}")
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(0.01)
        assert marker.exists()
        with pytest.raises(PropagationJobError, match="reader_unavailable"):
            reader.preview(b"{}")
        assert first.result(timeout=2) == {}


def test_reader_refuses_detached_descendant_and_reaps_it(tmp_path):
    marker = tmp_path / "descendant-pid"
    child_source = f"from pathlib import Path;import os,time;Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(10)"
    reader, _ = _reader(tmp_path,
        "import subprocess,sys\n"
        f"subprocess.Popen([sys.executable,'-c',{child_source!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
        "start_new_session=True)\nprint('{}')\n", budget=2)
    try:
        with pytest.raises(PropagationJobError, match="reader_unavailable"):
            reader.preview(b"{}")
        assert marker.exists()
        pid = int(marker.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_interrupted_reader_cleans_up_owned_descendant(tmp_path, monkeypatch):
    marker = tmp_path / "reader-pid"
    reader, _ = _reader(tmp_path,
        f"from pathlib import Path\nimport os,time\nPath({str(marker)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(10)\n", budget=2)
    def interrupted(process, *args, **kwargs):
        assert process.stdin is not None
        process.stdin.write(args[0])
        process.stdin.close()
        for _ in range(200):
            if marker.exists():
                raise KeyboardInterrupt()
            time.sleep(0.01)
        raise AssertionError("reader child did not start")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess.Popen, "communicate", interrupted)
            with pytest.raises(KeyboardInterrupt):
                reader.preview(b"{}")
        assert marker.exists()
        pid = int(marker.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
