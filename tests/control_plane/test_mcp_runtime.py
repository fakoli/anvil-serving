from __future__ import annotations

import json
import sys
import time

import pytest

from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.runtime import run_argv


def test_bounded_command_timeout_terminates_descendants(tmp_path):
    marker = tmp_path / "orphan-marker.json"
    grandchild = (
        "import json,pathlib,sys,time;"
        "time.sleep(2);"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'orphan':True}),encoding='utf-8')"
    )
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{json.dumps(grandchild)},sys.argv[1]]);"
        "time.sleep(60)"
    )

    with pytest.raises(ToolError) as error:
        run_argv(
            [sys.executable, "-c", parent, str(marker)],
            confirm=True,
            timeout=1,
        )

    assert error.value.code == "timeout"
    time.sleep(2)
    assert not marker.exists()
