"""Parse every shipped module so one broken view cannot prevent application boot."""

from pathlib import Path
import shutil
import subprocess

import pytest

STATIC = Path(__file__).parents[2] / "anvil_serving/observability/dashboard/static"


@pytest.mark.parametrize(
    "source", sorted(STATIC.rglob("*.js")), ids=lambda source: str(source.relative_to(STATIC))
)
def test_shipped_esmodule_parses(source, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the frontend syntax gate")
    module = tmp_path / "module.mjs"
    module.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(
        [node, "--check", module],
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, f"{source.relative_to(STATIC)}: {result.stderr}"
