"""Evidence binds the actual staged bytes and every published result file."""
import subprocess
import sys

import pytest

from anvil_serving.connect import qualification as subject


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux qualification artifact custody")


def test_go_version_preserves_major_component(tmp_path, monkeypatch):
    tool = tmp_path / "go"
    tool.write_bytes(b"fixture")
    tool.chmod(0o700)
    monkeypatch.setattr(subject.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, b"go version go1.27.1 linux/amd64\n"))
    assert subject._tool_metadata({"go": tool})["go"]["version"] == "1.27.1"
