"""Execute the packaged mobile API against a synthetic owner, without a provider."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_packaged_mobile_api_behavior(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the packaged TypeScript behavior gate")
    patch = Path(__file__).parents[2] / "anvil_serving/_pi_web_bridge/0.10.0-host-bridge.patch"
    blocks = patch.read_text().split("diff --git ")
    for packaged, name in [("lib/mobile-pi-api.ts", "mobile-pi-api.ts"),
                           ("app/api/mobile-pi/[...path]/route.ts", "mobile-route.ts")]:
        source = next((block.split("\n", 1)[1] for block in blocks
                       if block.startswith("a/" + packaged + " ")), "")
        helper = "\n".join(line[1:] for line in source.splitlines() if line.startswith("+") and not line.startswith("+++"))
        (tmp_path / name).write_text(helper + "\n")
    fixture = Path(__file__).parent / "fixtures/pi_mobile_api.test.mjs"
    shutil.copyfile(fixture, tmp_path / fixture.name)
    result = subprocess.run([node, "--experimental-strip-types", "--test", fixture.name],
                            cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
