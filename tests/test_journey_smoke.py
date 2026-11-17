from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys

from anvil_serving.product_families import PRODUCT_FAMILIES


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "journey_smoke.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("journey_smoke", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_module()
STEPS = {(family.id, step.stage) for family in PRODUCT_FAMILIES for step in family.journey}


def _step(persona: str, family: str, stage: str, status: str) -> dict[str, object]:
    return {"persona": persona, "family": family, "stage": stage, "status": status,
            "last_line": "reason"}


def test_every_journey_placeholder_has_a_value() -> None:
    used = {match for family in PRODUCT_FAMILIES for step in family.journey
            for match in re.findall(r"<[A-Z_]+>", step.cli)}
    assert used == set(smoke.VALUES)


def test_gated_steps_exist_in_the_catalog() -> None:
    assert smoke.MUST_PASS | smoke.MUST_PASS_OPERATOR | smoke.MUST_PASS_GATEWAY <= STEPS
    assert not smoke.MUST_PASS & (smoke.MUST_PASS_OPERATOR | smoke.MUST_PASS_GATEWAY)


def test_failures_flag_crashes_and_gated_refusals_only() -> None:
    report = {
        "install": "ok", "version": "anvil-serving 0", "python": "3.13",
        "quickstart": [{"check": "OpenAI chat", "ok": False, "detail": "HTTP 502"}],
        "steps": [
            _step("gateway", "model-serving", "select", "refused"),      # gated: fails
            _step("gateway", "model-serving", "observe", "refused"),     # operator-only gate
            _step("operator", "model-serving", "observe", "refused"),    # gated for operator
            _step("operator", "anvil-connect", "validate", "refused"),   # may refuse
            _step("operator", "anvil-connect", "render", "crash"),       # never allowed
            _step("gateway", "anvil-media", "discover", "ok"),
            _step("gateway", "control-plane-fleet", "diagnose", "refused"),   # gated without the scaffold
            _step("operator", "control-plane-fleet", "diagnose", "refused"),  # needs Docker there
        ],
    }
    report["version_status"] = "ok"
    report["expected_steps"] = sorted(STEPS)
    report["quickstart"] += [
        {"check": name, "ok": True, "detail": ""}
        for name in smoke.QUICKSTART_CHECKS if name != "OpenAI chat"
    ]
    existing = {(step["persona"], step["family"], step["stage"]) for step in report["steps"]}
    report["steps"] += [
        _step(persona, family, stage, "ok")
        for persona in ("gateway", "operator") for family, stage in sorted(STEPS)
        if (persona, family, stage) not in existing
    ]
    problems = smoke.failures(report)
    assert [problem.split(":")[0] for problem in problems] == [
        "quickstart",
        "gateway/model-serving/select",
        "operator/model-serving/observe",
        "operator/anvil-connect/render",
        "gateway/control-plane-fleet/diagnose",
    ]
    assert "refused" in smoke.render(report)


def test_empty_successful_install_is_not_a_successful_smoke() -> None:
    report = {"install": "ok", "version": "failed", "version_status": "refused",
              "quickstart": [], "steps": []}
    assert smoke.failures(report)


def test_missing_persona_cannot_pass() -> None:
    report = {"install": "ok", "version_status": "ok",
              "expected_steps": sorted(STEPS),
              "quickstart": [{"check": name, "ok": True, "detail": ""}
                             for name in smoke.QUICKSTART_CHECKS],
              "steps": [_step("gateway", family, stage, "ok") for family, stage in STEPS]}
    assert smoke.failures(report)


def test_signal_termination_is_a_crash(monkeypatch) -> None:
    import subprocess

    monkeypatch.setattr(smoke.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], -9, stdout=b""))
    assert smoke._run(["example"], {})["status"] == "crash"


def test_complete_report_passes_but_missing_or_duplicate_step_fails() -> None:
    report = {"install": "ok", "version_status": "ok", "expected_steps": sorted(STEPS),
              "quickstart": [{"check": name, "ok": True, "detail": ""}
                             for name in smoke.QUICKSTART_CHECKS],
              "steps": [_step(persona, family, stage, "ok")
                        for persona in ("gateway", "operator") for family, stage in sorted(STEPS)]}
    assert smoke.failures(report) == []
    removed = report["steps"].pop()
    assert "journey report is incomplete or duplicated" in smoke.failures(report)
    report["steps"].extend([removed, removed])
    assert "journey report is incomplete or duplicated" in smoke.failures(report)


def test_missing_quickstart_check_fails_even_when_remaining_checks_pass() -> None:
    report = {"install": "ok", "version_status": "ok", "expected_steps": sorted(STEPS),
              "quickstart": [{"check": name, "ok": True, "detail": ""}
                             for name in smoke.QUICKSTART_CHECKS if name != "OpenAI chat"],
              "steps": [_step(persona, family, stage, "ok")
                        for persona in ("gateway", "operator") for family, stage in sorted(STEPS)]}
    assert smoke.failures(report) == ["quickstart report is incomplete or duplicated"]


def test_real_model_report_requires_both_preflight_checks() -> None:
    names = smoke.QUICKSTART_CHECKS | {"preflight, model server", "preflight, through the gateway"}
    report = {"install": "ok", "version_status": "ok", "real_model": True,
              "expected_steps": sorted(STEPS),
              "quickstart": [{"check": name, "ok": True, "detail": ""} for name in names],
              "steps": [_step(persona, family, stage, "ok")
                        for persona in ("gateway", "operator") for family, stage in sorted(STEPS)]}
    assert smoke.failures(report) == []
    report["quickstart"] = [check for check in report["quickstart"]
                            if check["check"] != "preflight, model server"]
    assert smoke.failures(report) == ["quickstart report is incomplete or duplicated"]
