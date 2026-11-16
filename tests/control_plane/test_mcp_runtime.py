from __future__ import annotations

import json
import sys

import pytest

from anvil_serving.control_plane.mcp.errors import ToolError
from tests.proc_helpers import wait_gone
from anvil_serving.control_plane.mcp.runtime import run_argv


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
    wait_gone(int(pidfile.read_text()))
    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-specific")
def test_bounded_command_timeout_tolerates_eperm_from_an_exited_group(monkeypatch):
    # Simulates macOS: killpg on a group with only exited members is EPERM.
    from anvil_serving.control_plane.mcp import runtime

    real_killpg = runtime.os.killpg

    def killpg(pgid, sig):
        if sig == runtime.signal.SIGKILL:
            raise PermissionError(1, "Operation not permitted")
        real_killpg(pgid, sig)

    monkeypatch.setattr(runtime.os, "killpg", killpg)
    with pytest.raises(ToolError) as error:
        run_argv([sys.executable, "-c", "import time; time.sleep(60)"], confirm=True, timeout=0.5)
    assert error.value.code == "timeout"
