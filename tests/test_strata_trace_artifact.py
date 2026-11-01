"""The trace leaf forwards only fixed metadata and preserves the parent parser."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'configs/runtime/strata-6f32ec0-sm120'
LEAF = ROOT / 'diagnostics-trace-m50'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def parser(monkeypatch):
    base = load(ROOT / 'diagnostics-m50/diagnostics.py', 'trace_parent')
    monkeypatch.setitem(sys.modules, 'anvil_diagnostics_base', base)
    return load(LEAF / 'trace_diagnostics.py', 'trace_extension')
