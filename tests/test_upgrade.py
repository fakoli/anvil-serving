from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving import guard, upgrade


class _Distribution:
    def __init__(self, root: Path, *, version="0.13.0", installer="pip", direct_url=None):
        self._root = root
        self.version = version
        self._installer = installer
        self._direct_url = direct_url

    def locate_file(self, _name):
        return self._root

    def read_text(self, name):
        if name == "INSTALLER":
            return self._installer
        if name == "direct_url.json":
            return self._direct_url
        return None


def test_inspect_installation_detects_direct_url_editable(tmp_path):
    dist = _Distribution(
        tmp_path,
        direct_url='{"dir_info": {"editable": true}, "url": "file:///checkout"}',
    )

    found = upgrade.inspect_installation(_distribution=lambda _name: dist, prefix=str(tmp_path))

    assert found.version == "0.13.0"
    assert found.manager == "pip"
    assert found.editable is True


def test_apply_upgrade_requires_exact_reported_version():
    plan = upgrade.UpgradePlan(
        "0.13.0", "0.14.0", "pip", False, ("python", "-m", "pip", "install")
    )

    def run(command, **_kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="anvil-serving 10.14.0\n", stderr="")
        return SimpleNamespace(returncode=0)

    with pytest.raises(upgrade.UpgradeError, match="verification did not report"):
        upgrade.apply_upgrade(plan, _run=run, _which=lambda _name: "anvil-serving-bin")


def test_main_dry_run_allows_inspecting_editable_install(monkeypatch, tmp_path, capsys):
    installed = upgrade.Installation("0.13.0", "pip", True, tmp_path)
    monkeypatch.setattr(upgrade, "inspect_installation", lambda: installed)
    monkeypatch.setattr(upgrade, "latest_pypi_version", lambda: "0.14.0")

    assert upgrade.main(["--dry-run"]) == 0

    output = capsys.readouterr().out
    assert "status:   dry-run" in output
    assert "apply is refused unless --allow-editable" in output


def test_main_requires_dispatcher_confirmation(monkeypatch, tmp_path, capsys):
    installed = upgrade.Installation("0.13.0", "pip", False, tmp_path)
    monkeypatch.setattr(upgrade, "inspect_installation", lambda: installed)
    monkeypatch.setattr(upgrade, "latest_pypi_version", lambda: "0.14.0")

    assert upgrade.main([]) == 1
    assert "confirmation required" in capsys.readouterr().err


def test_main_applies_inside_confirmed_dispatch_scope(monkeypatch, tmp_path, capsys):
    installed = upgrade.Installation("0.13.0", "pip", False, tmp_path)
    monkeypatch.setattr(upgrade, "inspect_installation", lambda: installed)
    monkeypatch.setattr(upgrade, "latest_pypi_version", lambda: "0.14.0")
    monkeypatch.setattr(
        upgrade,
        "apply_upgrade",
        lambda plan: {**plan.as_dict(), "status": "upgraded", "installed_version": "0.14.0"},
    )

    with guard.confirmation_scope(True):
        assert upgrade.main([]) == 0

    assert "status:   upgraded" in capsys.readouterr().out
