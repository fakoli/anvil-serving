"""The fixed chunk profile preserves other engine behavior and existing containment."""
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / 'configs/runtime/strata-6f32ec0-sm120'
LEAF = ROOT / 'variants-prefill4096'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
