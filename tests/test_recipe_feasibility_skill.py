import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "anvil-serving-recipe-feasibility"
SKILL = SKILL_DIR / "SKILL.md"
CONTRACT = SKILL_DIR / "references" / "feasibility-contract.md"
EXAMPLE = SKILL_DIR / "references" / "qwen38-250k-example.json"
SCRIPT = SKILL_DIR / "scripts" / "recipe_feasibility.py"
WRAPPER = ROOT / ".agents" / "skills" / "anvil-serving-recipe-feasibility" / "SKILL.md"
OPENAI_YAML = SKILL_DIR / "agents" / "openai.yaml"


def _module():
    spec = importlib.util.spec_from_file_location("recipe_feasibility", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
