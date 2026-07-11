from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "wheel_smoke.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("wheel_smoke", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_module()


@pytest.mark.parametrize(
    ("os_name", "python_suffix", "entrypoint_suffix"),
    [
        ("nt", "Scripts/python.exe", "Scripts/anvil-serving.exe"),
        ("posix", "bin/python", "bin/anvil-serving"),
    ],
)
def test_environment_paths_are_cross_platform(os_name, python_suffix, entrypoint_suffix, tmp_path):
    python, entrypoint = smoke.environment_paths(tmp_path, os_name=os_name)
    assert python.as_posix().endswith(python_suffix)
    assert entrypoint.as_posix().endswith(entrypoint_suffix)


def test_select_wheel_requires_exactly_one_distribution(tmp_path):
    with pytest.raises(smoke.WheelSmokeError, match="found 0"):
        smoke.select_wheel(tmp_path)
    (tmp_path / "anvil_serving-1-py3-none-any.whl").touch()
    assert smoke.select_wheel(tmp_path).name == "anvil_serving-1-py3-none-any.whl"
    (tmp_path / "anvil_serving-2-py3-none-any.whl").touch()
    with pytest.raises(smoke.WheelSmokeError, match="found 2"):
        smoke.select_wheel(tmp_path)


@pytest.mark.parametrize("failure_index", [None, 0, 1, 2])
def test_run_smoke_is_checkout_independent_bounded_and_always_cleans_up(
    tmp_path, failure_index
):
    wheel = tmp_path / "anvil_serving-1-py3-none-any.whl"
    wheel.touch()
    calls = []
    temporary_roots = []

    def create_venv(path):
        temporary_roots.append(path.parent)
        python, entrypoint = smoke.environment_paths(path, os_name="posix")
        python.parent.mkdir(parents=True)
        python.touch()
        entrypoint.touch()

    def runner(argv, **kwargs):
        index = len(calls)
        calls.append((list(argv), kwargs))
        if index == failure_index:
            return subprocess.CompletedProcess(argv, 9, "", "synthetic failure")
        if index == 1:
            installed = temporary_roots[0] / "venv" / "lib" / "anvil_serving" / "__init__.py"
            return subprocess.CompletedProcess(argv, 0, f"{installed}\npackage-data-ok\n", "")
        if index == 2:
            return subprocess.CompletedProcess(argv, 0, "usage: anvil-serving router run\n", "")
        return subprocess.CompletedProcess(argv, 0, "installed\n", "")

    if failure_index is None:
        result = smoke.run_smoke(
            wheel, runner=runner, create_venv=create_venv, os_name="posix", timeout=17
        )
        assert result["canonical_command"] == "anvil-serving router run --help"
    else:
        with pytest.raises(smoke.WheelSmokeError, match="command failed"):
            smoke.run_smoke(
                wheel, runner=runner, create_venv=create_venv, os_name="posix", timeout=17
            )

    assert temporary_roots and not temporary_roots[0].exists()
    install = calls[0]
    assert install[0][1:7] == ["-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)]
    assert install[1]["cwd"] != ROOT
    assert install[1]["timeout"] == 17
    assert "PYTHONPATH" not in install[1]["environment"]
    if failure_index is None:
        assert calls[-1][0][1:] == ["router", "run", "--help"]


def test_run_smoke_rejects_checkout_import(tmp_path):
    wheel = tmp_path / "anvil_serving-1-py3-none-any.whl"
    wheel.touch()
    calls = 0

    def create_venv(path):
        python, entrypoint = smoke.environment_paths(path, os_name="posix")
        python.parent.mkdir(parents=True)
        python.touch()
        entrypoint.touch()

    def runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            return subprocess.CompletedProcess(
                argv, 0, f"{ROOT / 'anvil_serving' / '__init__.py'}\npackage-data-ok\n", ""
            )
        return subprocess.CompletedProcess(argv, 0, "installed\n", "")

    with pytest.raises(smoke.WheelSmokeError, match="source checkout"):
        smoke.run_smoke(wheel, runner=runner, create_venv=create_venv, os_name="posix")
