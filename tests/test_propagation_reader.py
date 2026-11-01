"""The installed readback command is fixed, bounded and fails closed."""

import hashlib
import os
from pathlib import Path
import sys

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


def test_cancellation_reader_binds_fresh_custody_without_private_job_fields(tmp_path):
    reader, _script = _reader(tmp_path, "import json,sys\nprint(json.dumps(json.load(sys.stdin)))\n")
    job = {"job_id": "job-1", "operation_id": "operation-1", "contract_digest": "a" * 64,
           "intent_id": "intent-1", "private_value": "must-not-escape"}
    identities = [{"pid": 1, "start_ticks": "2", "boot_id": "old-boot"}]
    custody = {"custody_digest": "b" * 64, "child_result_digest": "c" * 64}
    result = reader.cancellation_evidence(b"{}", job, identities, custody)
    assert set(result) == {"schema", "kind", "contract", "job", "identities", "custody"}
    assert result["custody"] == custody and result["identities"] == identities
    assert "private_value" not in result["job"]


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
