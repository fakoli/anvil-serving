"""Installed asset/build contracts, independent of a live Docker daemon."""

import json
from importlib.resources import files
from pathlib import Path

from anvil_serving import cli
from anvil_serving.observability.dashboard import app


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


def test_supported_short_launch_discovers_only_installed_operator_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    assert app.build_parser().parse_args([]).observatory_config is None
    policy = tmp_path / "workbench.json"
    policy.write_text("{}")
    assert app.build_parser().parse_args([]).observatory_config == str(policy)


def test_packaged_docs_and_fonts_are_present_with_their_licenses():
    assets = files("anvil_serving.observability.dashboard.static")
    for name in ("serving", "anvil", "workbench", "benchmarks", "connect"):
        assert len(assets.joinpath("documents", name + ".md").read_text()) > 100
    for name in ("Barlow-Regular.ttf", "BarlowCondensed-SemiBold.ttf", "Barlow-SemiBold.ttf"):
        assert len(assets.joinpath("fonts", name).read_bytes()) > 1000
    for name in ("barlow-OFL.txt", "barlowcondensed-OFL.txt"):
        assert "SIL OPEN FONT LICENSE" in assets.joinpath("fonts", name).read_text()
