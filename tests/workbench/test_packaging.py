"""Installed asset/build contracts, independent of a live Docker daemon."""

import json
from importlib.resources import files
from pathlib import Path

from anvil_serving import cli


def test_pi_build_uses_packaged_pinned_sources_and_never_the_companion_checkout(capsys):
    assert cli.main(["workbench", "build", "--runner", "pi", "--source", "/does/not/exist", "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    command = result["command"]
    assert result["dry_run"] is True
    assert command[:2] == ["docker", "build"]
    source = Path(command[-1])
    package = json.loads((source / "package.json").read_text())
    assert package["dependencies"]["@earendil-works/pi-coding-agent"] == "0.85.1"
    assert (source / "proxy.cjs").is_file()
    assert command[command.index("--tag") + 1] == "anvil-pi-runner:0.85.1"


def test_runner_assets_are_byte_identical_to_reviewed_sources():
    root = Path(__file__).resolve().parents[2]
    packaged = files("anvil_serving").joinpath("_pi_runner")
    for name in ("Dockerfile", "package.json", "package-lock.json", "proxy.cjs"):
        assert packaged.joinpath(name).read_bytes() == (root / "pi_runner" / name).read_bytes()
