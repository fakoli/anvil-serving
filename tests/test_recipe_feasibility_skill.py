import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


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


def test_skill_contract_keeps_unknowns_and_classifications_distinct():
    skill = SKILL.read_text(encoding="utf-8")
    contract = CONTRACT.read_text(encoding="utf-8")
    for phrase in (
        "proven-infeasible",
        "modeled-infeasible",
        "policy-infeasible",
        "empirically-disqualified",
        "benchmark-survivor",
        "never insert a convenient zero",
        "matched no-speculation control",
        "operator or campaign owner",
        "default reserve, effective reserve",
        "reserve waiver does not waive",
    ):
        assert phrase in skill
    for phrase in (
        "T_req",
        "V_demand",
        "relative_quality_loss",
        "warm_e2e_gain",
        "tasks_per_hour_ratio",
        "An unknown value has a missing lower or upper bound",
        "default and effective reserve",
        "does not grant promotion authority",
    ):
        assert phrase in contract


def test_number_cleanup_accepts_integer_bounds_on_python_311():
    module = _module()
    assert module._clean_number(384_568) == 384_568
    assert module._clean_number(1.5) == 1.5


def test_example_prunes_q6_retains_n4_and_marks_overlapping_bounds_unresolved():
    module = _module()
    result = module.evaluate(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    rows = {row["id"]: row for row in result["candidates"]}
    assert rows["q38-conventional-q6-q4kv-mtp3"]["classification"] == "policy-infeasible"
    assert rows["q38-n4-q8-mtp3"]["classification"] == "benchmark-survivor"
    assert rows["q38-q4km-k8v4-mtp3"]["classification"] == "unresolved"
    assert rows["q38-unsloth-udq5xl-q4kv-mtp3"]["classification"] == "unresolved"
    assert rows["q38-sglang-nvfp4-128k-measured"]["classification"] == "proven-infeasible"
    tracked = {variable["path"]: variable for variable in result["variables"]}
    assert tracked["tracked_variables.wsl2_memory_limit_bytes"]["status"] == "unknown"
    assert "tracked_variables.wsl2_memory_limit_bytes" in result["unresolved_variables"]
    assert rows["q38-n4-q8-mtp3"]["axes"]["quality"]["missing_variables"] == [
        "quality_score",
        "reference_quality_score",
    ]
    assert result["promotion_authority"] is False


def test_estimated_point_value_is_rejected_instead_of_treated_as_exact():
    module = _module()
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["candidates"][0]["resident_components"]["target_weights"] = {
        "value": 16_000_000_000,
        "status": "estimated",
        "unit": "bytes",
        "source": "planning estimate",
    }
    data["candidates"] = [data["candidates"][0]]
    with pytest.raises(module.FeasibilityError, match="distinct numeric min/max bounds"):
        module.evaluate(data)


def test_estimated_stable_context_is_modeled_not_empirical():
    module = _module()
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    candidate = data["candidates"][0]
    candidate["measured_max_stable_context_tokens"] = {
        "min": 100_000,
        "max": 120_000,
        "status": "estimated",
        "unit": "tokens",
        "source": "planning bound only",
    }
    data["candidates"] = [candidate]
    row = module.evaluate(data)["candidates"][0]
    assert row["classification"] == "modeled-infeasible"
    assert row["axes"]["measured_context_evidence_status"] == "estimated"
    assert "measured_max_stable_context_tokens" in row["missing_evidence"]


def test_estimated_behavior_can_project_but_cannot_pass_or_fail_a_gate():
    module = _module()
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["requirements"]["thresholds"]["min_deterministic_pass_rate"]["value"] = 0.99
    candidate = data["candidates"][0]
    candidate["metrics"] = {
        "deterministic_pass_rate": {
            "min": 0.995,
            "max": 1.0,
            "status": "estimated",
            "source": "planning projection",
        },
        "quality_score": {
            "min": 0.99,
            "max": 0.995,
            "status": "estimated",
            "source": "planning projection",
        },
        "reference_quality_score": {
            "min": 1.0,
            "max": 1.001,
            "status": "estimated",
            "source": "planning projection",
        },
        "warm_e2e_seconds": {
            "min": 7.9,
            "max": 8.0,
            "status": "estimated",
            "source": "planning projection",
        },
        "no_spec_warm_e2e_seconds": {
            "min": 10.0,
            "max": 10.1,
            "status": "estimated",
            "source": "planning projection",
        },
        "successful_tasks_per_hour": {
            "min": 12.2,
            "max": 12.4,
            "status": "estimated",
            "source": "planning projection",
        },
        "reference_tasks_per_hour": {
            "min": 10.0,
            "max": 10.1,
            "status": "estimated",
            "source": "planning projection",
        },
    }
    data["candidates"] = [candidate]
    row = module.evaluate(data)["candidates"][0]
    assert row["classification"] == "benchmark-survivor"
    assert all(axis["status"] == "unknown" for axis in row["axes"].values() if isinstance(axis, dict))
    assert all(
        axis["projected_status"] == "pass"
        for axis in row["axes"].values()
        if isinstance(axis, dict)
    )


def test_markdown_hides_irrelevant_load_bearing_unknowns_after_hard_prune():
    module = _module()
    result = module.evaluate(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    markdown = module.to_markdown(result)
    assert "## Load-bearing unknowns" not in markdown


def test_unbounded_load_bearing_variable_is_unresolved_not_zero(tmp_path):
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    candidate = data["candidates"][0]
    candidate["per_token_components"]["target_and_draft_q8_kv"] = {
        "status": "unknown",
        "unit": "bytes/token",
        "notes": "The runtime KV layout has not been measured or bounded."
    }
    data["candidates"] = [candidate]
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(data), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(input_path), "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["candidates"][0]["classification"] == "unresolved"
    assert result["candidates"][0]["demand_bytes"]["max"] is None


def test_behavior_equations_can_qualify_and_disqualify_a_capacity_survivor():
    module = _module()
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    candidate = data["candidates"][0]
    candidate["metrics"] = {
        "deterministic_pass_rate": {
            "value": 1.0,
            "status": "measured",
            "source": "deterministic qualification suite",
        },
        "quality_score": {
            "value": 0.99,
            "status": "measured",
            "source": "severity-weighted coding suite",
        },
        "reference_quality_score": {
            "value": 1.0,
            "status": "measured",
            "source": "matched reference suite",
        },
        "warm_e2e_seconds": {
            "value": 8.0,
            "status": "measured",
            "source": "warm agent benchmark",
        },
        "no_spec_warm_e2e_seconds": {
            "value": 10.0,
            "status": "measured",
            "source": "matched no-spec benchmark",
        },
        "successful_tasks_per_hour": {
            "value": 12.0,
            "status": "measured",
            "source": "agent task benchmark",
        },
        "reference_tasks_per_hour": {
            "value": 10.0,
            "status": "measured",
            "source": "matched reference task benchmark",
        },
    }
    data["candidates"] = [candidate]
    result = module.evaluate(data)
    assert result["candidates"][0]["classification"] == "math-qualified"

    candidate["metrics"]["warm_e2e_seconds"]["value"] = 9.0
    result = module.evaluate(data)
    assert result["candidates"][0]["classification"] == "requirements-disqualified"
    assert result["candidates"][0]["axes"]["warm_e2e_speed"]["status"] == "fail"


def test_discovery_surfaces_point_to_canonical_skill():
    wrapper = WRAPPER.read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "OPERATOR-SKILLS-AND-SUBAGENTS.md").read_text(encoding="utf-8")
    metadata = OPENAI_YAML.read_text(encoding="utf-8")
    assert "../../../skills/anvil-serving-recipe-feasibility/SKILL.md" in wrapper
    assert len(wrapper.splitlines()) < 20
    assert "skills/anvil-serving-recipe-feasibility/SKILL.md" in agents
    assert "skills/anvil-serving-recipe-feasibility/SKILL.md" in docs
    assert "$anvil-serving-recipe-feasibility" in metadata
