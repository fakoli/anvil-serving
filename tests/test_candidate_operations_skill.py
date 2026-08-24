from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / ".agents" / "skills" / "anvil-serving-candidate-operations"


def test_candidate_operations_skill_is_project_loaded_and_fail_closed():
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    ui = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")

    for phrase in (
        "anvil-serving host gpus --json",
        "models recipes status",
        "models recipes logs",
        "models recipes unload",
        "models recipes load",
        "serves switch ROLE MODEL --dry-run",
        "anvil-serving eval preflight",
        "host-operations:credential-source-diagnostics",
        "host-operations:windows-gpu-lane-hygiene",
        "workflow-intake:external-procedure-intake",
        "starting state",
        "promoted=false",
    ):
        assert phrase in skill

    assert "raw Docker only for the narrowest read-only diagnosis" in skill
    assert "[TODO:" not in skill
    assert "$anvil-serving-candidate-operations" in ui
