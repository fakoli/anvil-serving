from __future__ import annotations

import json
import os
import sys
import time

import pytest

from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.runtime import run_argv

def _wait_gone(pid, limit=5.0):
    """Poll until ``pid`` is gone (or a zombie awaiting reaping)."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            state = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0]
        except ProcessLookupError:
            return
        except OSError:
            state = ""
        if state == "Z":
            return
        time.sleep(0.01)


def test_bounded_command_timeout_terminates_descendants(tmp_path):
    marker = tmp_path / "orphan-marker.json"
    grandchild = (
        "import json,pathlib,sys,time;"
        "time.sleep(1.5);"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'orphan':True}),encoding='utf-8')"
    )
    parent = (
        "import subprocess,sys,time;"
        f"p=subprocess.Popen([sys.executable,'-c',{json.dumps(grandchild)},sys.argv[1]]);"
        "open(sys.argv[1]+'.pid','w').write(str(p.pid));"
        "time.sleep(60)"
    )

    with pytest.raises(ToolError) as error:
        run_argv(
            [sys.executable, "-c", parent, str(marker)],
            confirm=True,
            timeout=0.5,
        )

    assert error.value.code == "timeout"
    pidfile = tmp_path / "orphan-marker.json.pid"
    assert pidfile.exists()
    _wait_gone(int(pidfile.read_text()))
    assert not marker.exists()
