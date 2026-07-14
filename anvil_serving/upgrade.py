"""Guarded self-upgrade support for the installed ``anvil-serving`` CLI.

The command deliberately upgrades released packages from PyPI.  Editable source
checkouts are detected and refused unless the operator explicitly asks to detach
them, because silently replacing an editable install would make local edits stop
affecting the command.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from . import guard


PACKAGE_NAME = "anvil-serving"
PYPI_JSON_URL = "https://pypi.org/pypi/anvil-serving/json"
_RELEASE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class UpgradeError(RuntimeError):
    """A safe self-upgrade plan could not be built or verified."""


@dataclass(frozen=True)
class Installation:
    """The locally installed package state relevant to self-upgrade."""

    version: str
    manager: str
    editable: bool
    root: Path


@dataclass(frozen=True)
class UpgradePlan:
    """A fully resolved, auditable package-manager invocation."""

    current_version: str
    latest_version: str
    manager: str
    editable: bool
    command: tuple[str, ...]

    @property
    def already_current(self) -> bool:
        return self.current_version == self.latest_version

    def as_dict(self) -> dict[str, object]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "manager": self.manager,
            "editable": self.editable,
            "already_current": self.already_current,
            "command": list(self.command),
        }


def _path_parts(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in path.parts)


def _is_editable(dist, root: Path) -> bool:
    direct_url = dist.read_text("direct_url.json")
    if direct_url:
        try:
            if bool(json.loads(direct_url).get("dir_info", {}).get("editable")):
                return True
        except (AttributeError, json.JSONDecodeError, TypeError):
            pass

    # Legacy/editable setuptools metadata is rooted at the source checkout and
    # may not carry direct_url.json.  A wheel root does not contain pyproject.toml.
    return (root / "pyproject.toml").is_file() and (root / "anvil_serving").is_dir()


def inspect_installation(*, _distribution=distribution, prefix: str | None = None) -> Installation:
    """Inspect package metadata without invoking an external package manager."""
    try:
        dist = _distribution(PACKAGE_NAME)
    except PackageNotFoundError as exc:
        raise UpgradeError("anvil-serving package metadata is not installed") from exc

    root = Path(dist.locate_file("")).resolve()
    prefix_path = Path(prefix or sys.prefix).resolve()
    parts = _path_parts(prefix_path)
    installer = (dist.read_text("INSTALLER") or "").strip().casefold()

    if "pipx" in parts and "venvs" in parts:
        manager = "pipx"
    elif "uv" in parts and "tools" in parts:
        manager = "uv"
    elif installer == "uv" and prefix_path.name.casefold().replace("_", "-") == PACKAGE_NAME:
        # Covers a custom UV_TOOL_DIR while avoiding ordinary uv-created
        # project environments (usually named .venv), which are not uv tools.
        manager = "uv"
    else:
        manager = "pip"

    return Installation(
        version=dist.version,
        manager=manager,
        editable=_is_editable(dist, root),
        root=root,
    )


def latest_pypi_version(*, _open=urllib.request.urlopen) -> str:
    """Return the latest stable release reported by PyPI."""
    try:
        with _open(PYPI_JSON_URL, timeout=10) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise UpgradeError(f"could not read the latest PyPI release: {exc}") from exc

    try:
        version = payload["info"]["version"]
    except (KeyError, TypeError) as exc:
        raise UpgradeError("PyPI returned an invalid anvil-serving release document") from exc
    if not isinstance(version, str) or not _RELEASE_RE.match(version):
        raise UpgradeError(f"PyPI returned an invalid anvil-serving version: {version!r}")
    return version


def _release_tuple(version: str) -> tuple[int, int, int]:
    match = _RELEASE_RE.match(version)
    if match is None:
        raise UpgradeError(f"cannot safely compare non-semantic version {version!r}")
    return tuple(int(part) for part in match.groups())


def build_upgrade_plan(
    installation: Installation,
    latest_version: str,
    *,
    manager: str = "auto",
    allow_editable: bool = False,
    _which=shutil.which,
    executable: str | None = None,
) -> UpgradePlan:
    """Build one manager-owned upgrade command without changing the host."""
    selected = installation.manager if manager == "auto" else manager
    if selected not in {"uv", "pipx", "pip"}:
        raise UpgradeError(f"unsupported package manager {selected!r}")
    if _release_tuple(latest_version) < _release_tuple(installation.version):
        raise UpgradeError(
            f"refusing to downgrade {installation.version} to PyPI release {latest_version}"
        )
    if installation.editable and not allow_editable:
        raise UpgradeError(
            "this is an editable source install; use `git pull` and `pip install -e .` "
            "to update it, or pass --allow-editable to replace it with the PyPI release"
        )

    requirement = f"{PACKAGE_NAME}=={latest_version}"
    if selected == "uv":
        program = _which("uv")
        if not program:
            raise UpgradeError("uv owns this install but `uv` is not available on PATH")
        command = (program, "tool", "upgrade", requirement)
    elif selected == "pipx":
        program = _which("pipx")
        if not program:
            raise UpgradeError("pipx owns this install but `pipx` is not available on PATH")
        # pipx upgrade resolves the latest package from the registry and retains
        # pipx's ownership metadata.  The exact expected version is verified later.
        command = (program, "upgrade", PACKAGE_NAME)
    else:
        command_parts = [executable or sys.executable, "-m", "pip", "install", "--upgrade"]
        if installation.editable:
            command_parts.append("--force-reinstall")
        command_parts.append(requirement)
        command = tuple(command_parts)

    return UpgradePlan(
        current_version=installation.version,
        latest_version=latest_version,
        manager=selected,
        editable=installation.editable,
        command=command,
    )


def apply_upgrade(
    plan: UpgradePlan,
    *,
    _run=subprocess.run,
    _which=shutil.which,
) -> dict[str, object]:
    """Apply the plan once and verify the installed command reports the target version."""
    if plan.already_current:
        return {**plan.as_dict(), "status": "already-current", "installed_version": plan.current_version}

    try:
        completed = _run(list(plan.command), check=False)
    except OSError as exc:
        raise UpgradeError(f"could not start {plan.manager}: {exc}") from exc
    if completed.returncode != 0:
        raise UpgradeError(
            f"{plan.manager} exited with status {completed.returncode}; the upgrade was not retried"
        )

    command_path = _which("anvil-serving")
    if not command_path:
        raise UpgradeError("upgrade completed but `anvil-serving` is no longer available on PATH")
    try:
        verified = _run(
            [command_path, "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise UpgradeError(f"could not verify the upgraded command: {exc}") from exc
    output = (verified.stdout or verified.stderr or "").strip()
    expected_output = f"anvil-serving {plan.latest_version}"
    if verified.returncode != 0 or output != expected_output:
        raise UpgradeError(
            "upgrade command completed but verification did not report "
            f"anvil-serving {plan.latest_version}: {output or '<no output>'}"
        )
    return {**plan.as_dict(), "status": "upgraded", "installed_version": plan.latest_version}


def _print_plan(plan: UpgradePlan) -> None:
    print(f"current:  {plan.current_version}")
    print(f"latest:   {plan.latest_version}")
    print(f"manager:  {plan.manager}")
    print(f"editable: {'yes' if plan.editable else 'no'}")
    print("command:  " + subprocess.list2cmdline(list(plan.command)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="anvil-serving upgrade",
        description="Upgrade this CLI to the newest stable anvil-serving release on PyPI.",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the resolved upgrade without applying it")
    parser.add_argument(
        "--manager",
        choices=("auto", "uv", "pipx", "pip"),
        default="auto",
        help="package manager override (default: detect the owning manager)",
    )
    parser.add_argument(
        "--allow-editable",
        action="store_true",
        help="replace an editable source install with the published package",
    )
    args = parser.parse_args(argv)

    try:
        installed = inspect_installation()
        latest = latest_pypi_version()
        # Dry-run may inspect an editable checkout without authorizing its replacement.
        plan = build_upgrade_plan(
            installed,
            latest,
            manager=args.manager,
            allow_editable=args.allow_editable or args.dry_run,
        )
        _print_plan(plan)
        if args.dry_run:
            print("status:   dry-run")
            if installed.editable and not args.allow_editable:
                print("note:     apply is refused unless --allow-editable is also passed")
            return 0
        if not guard.confirmation_authorized():
            raise UpgradeError("confirmation required; rerun with --confirm")
        result = apply_upgrade(plan)
    except UpgradeError as exc:
        print(f"anvil-serving upgrade: {exc}", file=sys.stderr)
        return 1

    print(f"status:   {result['status']}")
    return 0
