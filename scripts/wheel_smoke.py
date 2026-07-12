"""Verify a built wheel from a clean, temporary installation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Callable, Sequence
import venv


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT_SECONDS = 120
MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTIC_CHARS = 4096
PACKAGE_DATA_PROBE = """
from importlib import resources
import anvil_serving

package = resources.files("anvil_serving")
required = (package / "py.typed", package / "router" / "tier0_keywords.json")
missing = [str(path) for path in required if not path.is_file()]
if missing:
    raise SystemExit("missing package data: " + ", ".join(missing))
print(anvil_serving.__file__)
print("package-data-ok")
""".strip()


class WheelSmokeError(RuntimeError):
    pass


def select_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("anvil_serving-*.whl"))
    if len(wheels) != 1:
        raise WheelSmokeError(
            f"expected exactly one anvil-serving wheel in {dist_dir}, found {len(wheels)}"
        )
    return wheels[0].resolve()


def environment_paths(root: Path, *, os_name: str = os.name) -> tuple[Path, Path]:
    if os_name == "nt":
        return root / "Scripts" / "python.exe", root / "Scripts" / "anvil-serving.exe"
    return root / "bin" / "python", root / "bin" / "anvil-serving"


def _create_venv(path: Path) -> None:
    venv.EnvBuilder(with_pip=True, clear=True).create(path)


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            check=False,
            timeout=timeout,
            shell=False,
        )
        stdout.seek(0)
        stderr.seek(0)
        stdout_bytes = stdout.read(MAX_COMMAND_OUTPUT_BYTES + 1)
        stderr_bytes = stderr.read(MAX_COMMAND_OUTPUT_BYTES + 1)
    if len(stdout_bytes) > MAX_COMMAND_OUTPUT_BYTES or len(stderr_bytes) > MAX_COMMAND_OUTPUT_BYTES:
        raise WheelSmokeError("command output exceeded the wheel smoke size limit")
    try:
        stdout_text = stdout_bytes.decode("utf-8")
        stderr_text = stderr_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WheelSmokeError("command output was not valid UTF-8") from exc
    return subprocess.CompletedProcess(completed.args, completed.returncode, stdout_text, stderr_text)


def _checked(
    runner: Callable[..., subprocess.CompletedProcess[str]],
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    completed = runner(argv, cwd=cwd, environment=environment, timeout=timeout)
    if not isinstance(completed.stdout, str) or not isinstance(completed.stderr, str):
        raise WheelSmokeError("command runner must return text output")
    if (
        len(completed.stdout.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES
        or len(completed.stderr.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise WheelSmokeError("command output exceeded the wheel smoke size limit")
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout).strip()[:MAX_DIAGNOSTIC_CHARS]
        raise WheelSmokeError(f"command failed ({completed.returncode}): {output}")
    return completed


def run_smoke(
    wheel: Path,
    *,
    checkout: Path = ROOT,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run,
    create_venv: Callable[[Path], None] = _create_venv,
    os_name: str = os.name,
) -> dict[str, object]:
    wheel = wheel.resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise WheelSmokeError(f"wheel does not exist: {wheel}")
    if timeout < 1:
        raise WheelSmokeError("timeout must be positive")

    with tempfile.TemporaryDirectory(prefix="anvil-wheel-smoke-") as temporary:
        temporary_path = Path(temporary).resolve()
        environment_path = temporary_path / "venv"
        outside_checkout = temporary_path / "run"
        outside_checkout.mkdir()
        create_venv(environment_path)
        python, entrypoint = environment_paths(environment_path, os_name=os_name)

        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

        _checked(
            runner,
            [str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)],
            cwd=outside_checkout,
            environment=environment,
            timeout=timeout,
        )
        package_probe = _checked(
            runner,
            [str(python), "-c", PACKAGE_DATA_PROBE],
            cwd=outside_checkout,
            environment=environment,
            timeout=timeout,
        )
        probe_lines = [line.strip() for line in package_probe.stdout.splitlines() if line.strip()]
        if len(probe_lines) < 2 or probe_lines[-1] != "package-data-ok":
            raise WheelSmokeError("package-data probe did not return its success marker")
        installed_package = Path(probe_lines[-2]).resolve()
        checkout = checkout.resolve()
        if installed_package == checkout or checkout in installed_package.parents:
            raise WheelSmokeError("smoke imported anvil_serving from the source checkout")

        help_probe = _checked(
            runner,
            [str(entrypoint), "router", "run", "--help"],
            cwd=outside_checkout,
            environment=environment,
            timeout=timeout,
        )
        if "usage:" not in help_probe.stdout.casefold() or "router run" not in help_probe.stdout:
            raise WheelSmokeError("installed console entry point did not expose canonical router help")
        return {
            "wheel": wheel.name,
            "installed_package": str(installed_package),
            "entrypoint": str(entrypoint),
            "canonical_command": "anvil-serving router run --help",
            "package_data": ["anvil_serving/py.typed", "anvil_serving/router/tier0_keywords.json"],
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        wheel = args.wheel.resolve() if args.wheel else select_wheel(args.dist_dir.resolve())
        result = run_smoke(wheel, timeout=args.timeout)
    except (OSError, subprocess.SubprocessError, WheelSmokeError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"wheel smoke failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "result": result}, indent=2, sort_keys=True))
    else:
        print(f"wheel smoke passed: {result['wheel']} -> {result['canonical_command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
