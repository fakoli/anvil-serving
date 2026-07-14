from __future__ import annotations

import io
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


def test_inspect_installation_detects_uv_tool_from_prefix(tmp_path):
    dist = _Distribution(tmp_path / "site-packages", installer="uv")

    found = upgrade.inspect_installation(
        _distribution=lambda _name: dist,
        prefix=str(tmp_path / "uv" / "tools" / "anvil-serving"),
    )

    assert found.manager == "uv"
    assert found.editable is False


def test_uv_pip_project_environment_is_not_misclassified_as_uv_tool(tmp_path):
    dist = _Distribution(tmp_path / "site-packages", installer="uv")

    found = upgrade.inspect_installation(
        _distribution=lambda _name: dist,
        prefix=str(tmp_path / ".venv"),
    )

    assert found.manager == "pip"


def test_latest_pypi_version_reads_release_document():
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def open_url(url, timeout):
        assert url == upgrade.PYPI_JSON_URL
        assert timeout == 10
        return Response(b'{"info": {"version": "0.14.0"}}')

    assert upgrade.latest_pypi_version(_open=open_url) == "0.14.0"


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        ("uv", ("uv-bin", "tool", "upgrade", "anvil-serving==0.14.0")),
        ("pipx", ("pipx-bin", "upgrade", "anvil-serving")),
        (
            "pip",
            ("python-bin", "-m", "pip", "install", "--upgrade", "anvil-serving==0.14.0"),
        ),
    ],
)
def test_build_upgrade_plan_uses_owning_manager(tmp_path, manager, expected):
    installed = upgrade.Installation("0.13.0", manager, False, tmp_path)
    programs = {"uv": "uv-bin", "pipx": "pipx-bin"}

    plan = upgrade.build_upgrade_plan(
        installed,
        "0.14.0",
        _which=programs.get,
        executable="python-bin",
    )

    assert plan.command == expected
    assert plan.manager == manager


def test_build_upgrade_plan_refuses_editable_install(tmp_path):
    installed = upgrade.Installation("0.13.0", "pip", True, tmp_path)

    with pytest.raises(upgrade.UpgradeError, match="editable source install"):
        upgrade.build_upgrade_plan(installed, "0.14.0")


def test_editable_override_forces_reinstall(tmp_path):
    installed = upgrade.Installation("0.13.0", "pip", True, tmp_path)

    plan = upgrade.build_upgrade_plan(
        installed,
        "0.14.0",
        allow_editable=True,
        executable="python-bin",
    )

    assert plan.command == (
        "python-bin",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        "anvil-serving==0.14.0",
    )


def test_build_upgrade_plan_refuses_pypi_downgrade(tmp_path):
    installed = upgrade.Installation("0.14.0", "pip", False, tmp_path)

    with pytest.raises(upgrade.UpgradeError, match="refusing to downgrade"):
        upgrade.build_upgrade_plan(installed, "0.13.0")


def test_apply_upgrade_runs_once_and_verifies(tmp_path):
    plan = upgrade.UpgradePlan(
        "0.13.0",
        "0.14.0",
        "pip",
        False,
        ("python", "-m", "pip", "install", "anvil-serving==0.14.0"),
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="anvil-serving 0.14.0\n", stderr="")
        return SimpleNamespace(returncode=0)

    result = upgrade.apply_upgrade(plan, _run=run, _which=lambda _name: "anvil-serving-bin")

    assert result["status"] == "upgraded"
    assert [call[0] for call in calls] == [list(plan.command), ["anvil-serving-bin", "--version"]]


def test_apply_upgrade_does_not_retry_failed_mutation(tmp_path):
    plan = upgrade.UpgradePlan("0.13.0", "0.14.0", "uv", False, ("uv", "tool", "upgrade"))
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=2)

    with pytest.raises(upgrade.UpgradeError, match="was not retried"):
        upgrade.apply_upgrade(plan, _run=run)
    assert calls == [list(plan.command)]


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
