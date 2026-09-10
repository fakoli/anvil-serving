"""Parse every shipped module so one broken view cannot prevent application boot."""

from pathlib import Path
import shutil
import subprocess

import pytest

STATIC = Path(__file__).parents[2] / "anvil_serving/observability/dashboard/static"


@pytest.mark.parametrize(
    "source", sorted(STATIC.rglob("*.js")), ids=lambda source: str(source.relative_to(STATIC))
)
def test_shipped_esmodule_parses(source):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the frontend syntax gate")
    result = subprocess.run(
        [node, "--check", "--input-type=module"],
        input=source.read_text(),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, f"{source.relative_to(STATIC)}: {result.stderr}"
