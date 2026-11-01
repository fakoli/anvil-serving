"""Offline integrity and failure gates for the pinned Strata image artifacts."""
import importlib.util
from pathlib import Path
import shutil
import sys
import types

import pytest

ARTIFACT = Path(__file__).resolve().parents[1] / 'configs/runtime/strata-6f32ec0-sm120'


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    # The artifact runs on Linux. Only the lock primitive is replaced on Windows;
    # input hashing and configuration/receipt logic are the real artifact code.
    monkeypatch.setitem(sys.modules, 'fcntl', types.SimpleNamespace(
        LOCK_EX=1, LOCK_NB=2, flock=lambda *_: None))
    base = _module(ARTIFACT / 'anvil/prepare.py', 'strata_base_test')
    monkeypatch.setitem(sys.modules, 'prepare', base)
    module = _module(ARTIFACT / 'variants/runtime.py', 'strata_runtime_test')
    copied = tmp_path / 'configs'
    shutil.copytree(ARTIFACT / 'variants/runtime-configs', copied)
    monkeypatch.setattr(module, 'CONFIG_DIR', copied)
    return module
