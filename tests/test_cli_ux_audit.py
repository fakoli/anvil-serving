from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_cli_ux", ROOT / "scripts" / "audit_cli_ux.py"
)
assert SPEC is not None and SPEC.loader is not None
audit_cli_ux = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_cli_ux)


def _documents():
    manifest = audit_cli_ux._read_json(ROOT / audit_cli_ux.MANIFEST_REL)
    audit = audit_cli_ux._read_json(ROOT / audit_cli_ux.AUDIT_REL)
    return manifest, audit


def _attest(record, dimension):
    record["attestations"][dimension] = audit_cli_ux.dimension_attestation(
        record["manifest_fingerprint"],
        dimension,
        record["dimensions"][dimension],
        record["evidence"].get(dimension, []),
    )


def _failing_record(document, dimension):
    return next(
        record
        for record in document["records"]
        if audit_cli_ux._status(
            record["dimensions"][dimension],
            path=record["path"],
            dimension=dimension,
        )
        == "fail"
    )


def test_checked_in_cli_ux_audit():
    manifest, audit = _documents()

    failures = audit_cli_ux.validate_audit(manifest, audit)

    assert len(audit["records"]) == len(audit_cli_ux.visible_leaves(manifest))
    assert sum(failures.values()) <= sum(audit["maximum_failures"].values())


def test_cli_ux_audit_rejects_missing_and_duplicate_leaves():
    manifest, audit = _documents()
    missing = deepcopy(audit)
    missing["records"].pop()
    with pytest.raises(audit_cli_ux.AuditError, match="missing"):
        audit_cli_ux.validate_audit(manifest, missing)

    duplicate = deepcopy(audit)
    duplicate["records"].append(deepcopy(duplicate["records"][0]))
    with pytest.raises(audit_cli_ux.AuditError, match="duplicate"):
        audit_cli_ux.validate_audit(manifest, duplicate)


def test_cli_ux_audit_rejects_failure_budget_regression():
    manifest, audit = _documents()
    regressed = deepcopy(audit)
    regressed["maximum_failures"]["help"] -= 1

    with pytest.raises(audit_cli_ux.AuditError, match="help failures increased"):
        audit_cli_ux.validate_audit(manifest, regressed)


def test_cli_ux_audit_rejects_slack_after_an_improvement():
    manifest, audit = _documents()
    slack = deepcopy(audit)
    record = _failing_record(slack, "help")
    record["dimensions"]["help"] = "pass"
    record["evidence"]["help"] = [
        "tests/test_cli.py::test_every_visible_command_path_exposes_help"
    ]
    _attest(record, "help")

    with pytest.raises(audit_cli_ux.AuditError, match="budget must equal current failures"):
        audit_cli_ux.validate_audit(manifest, slack)


def test_cli_ux_audit_rejects_fabricated_or_unbound_pass_evidence():
    manifest, audit = _documents()
    fabricated = deepcopy(audit)
    record = _failing_record(fabricated, "help")
    record["dimensions"]["help"] = "pass"
    fabricated["maximum_failures"]["help"] -= 1
    record["evidence"]["help"] = [
        "tests/no_such_test.py::test_invented"
    ]
    _attest(record, "help")

    with pytest.raises(audit_cli_ux.AuditError, match="does not exist"):
        audit_cli_ux.validate_audit(manifest, fabricated)

    self_referential = deepcopy(audit)
    record = _failing_record(self_referential, "help")
    record["dimensions"]["help"] = "pass"
    self_referential["maximum_failures"]["help"] -= 1
    record["evidence"]["help"] = [
        "tests/test_cli_ux_audit.py::test_checked_in_cli_ux_audit"
    ]
    _attest(record, "help")
    with pytest.raises(audit_cli_ux.AuditError, match="invalid pytest node ID"):
        audit_cli_ux.validate_audit(manifest, self_referential)

    for fake_node in (
        "tests/test_cli.py::NoSuchClass::test_every_visible_command_path_exposes_help",
        "tests/test_cli.py::test_every_visible_command_path_exposes_help[not-real]",
        "tests\\test_cli.py::test_every_visible_command_path_exposes_help",
    ):
        malformed = deepcopy(audit)
        record = _failing_record(malformed, "help")
        record["dimensions"]["help"] = "pass"
        malformed["maximum_failures"]["help"] -= 1
        record["evidence"]["help"] = [fake_node]
        _attest(record, "help")
        with pytest.raises(audit_cli_ux.AuditError, match="invalid pytest node ID"):
            audit_cli_ux.validate_audit(manifest, malformed)


def test_cli_ux_audit_history_rejects_budget_status_and_strict_downgrades():
    _, previous = _documents()
    current = deepcopy(previous)
    current["maximum_failures"]["help"] += 1
    with pytest.raises(audit_cli_ux.AuditError, match="failure budget regressed"):
        audit_cli_ux.validate_monotonic(previous, current)

    passed = deepcopy(previous)
    passed["records"][0]["dimensions"]["help"] = "pass"
    regressed = deepcopy(passed)
    regressed["records"][0]["dimensions"]["help"] = "fail"
    with pytest.raises(audit_cli_ux.AuditError, match="regressed from pass"):
        audit_cli_ux.validate_monotonic(passed, regressed)

    strict = deepcopy(previous)
    strict["enforcement"] = "strict"
    with pytest.raises(audit_cli_ux.AuditError, match="may not be downgraded"):
        audit_cli_ux.validate_monotonic(strict, previous)


def test_cli_ux_audit_rejects_any_manifest_metadata_drift():
    manifest, audit = _documents()
    changed_manifest = deepcopy(manifest)
    command = next(record for record in changed_manifest["commands"] if record["path"] == "init")
    command["summary"] = "Changed without refreshing the audit."

    with pytest.raises(audit_cli_ux.AuditError, match="manifest_fingerprint is stale"):
        audit_cli_ux.validate_audit(changed_manifest, audit)


def test_cli_ux_audit_inventories_visible_callable_parents():
    manifest, _ = _documents()
    changed = deepcopy(manifest)
    parent = next(record for record in changed["commands"] if record["path"] == "router")
    parent["handler"] = "anvil_serving.router_manage:main"

    assert "router" in {record["path"] for record in audit_cli_ux.visible_leaves(changed)}


def test_cli_ux_audit_requires_reason_for_not_applicable():
    manifest, audit = _documents()
    invalid = deepcopy(audit)
    invalid["records"][0]["dimensions"]["preview"] = {
        "status": "not-applicable",
        "reason": "",
    }

    with pytest.raises(audit_cli_ux.AuditError, match="non-empty reason"):
        audit_cli_ux.validate_audit(manifest, invalid)


def test_cli_ux_audit_strict_mode_requires_zero_failures_and_budgets():
    manifest, audit = _documents()
    strict = deepcopy(audit)
    strict["enforcement"] = "strict"

    with pytest.raises(audit_cli_ux.AuditError, match="strict enforcement"):
        audit_cli_ux.validate_audit(manifest, strict)


def test_cli_ux_audit_rejects_boolean_schema_version():
    manifest, audit = _documents()
    invalid = deepcopy(audit)
    invalid["schema_version"] = True

    with pytest.raises(audit_cli_ux.AuditError, match="schema_version"):
        audit_cli_ux.validate_audit(manifest, invalid)


def test_cli_ux_audit_refresh_requires_reattestation_after_manifest_drift():
    manifest, audit = _documents()
    changed_manifest = deepcopy(manifest)
    command = next(record for record in changed_manifest["commands"] if record["path"] == "init")
    command["summary"] = "New summary."
    existing = next(record for record in audit["records"] if record["path"] == "init")
    existing["dimensions"]["help"] = "pass"
    existing["evidence"]["help"] = [
        "tests/test_cli.py::test_every_visible_command_path_exposes_help"
    ]
    _attest(existing, "help")

    refreshed = audit_cli_ux.refresh_audit(changed_manifest, audit)
    record = next(record for record in refreshed["records"] if record["path"] == "init")

    assert record["dimensions"]["help"] == "pass"
    assert record["manifest_fingerprint"] == audit_cli_ux._manifest_fingerprint(command)
    with pytest.raises(audit_cli_ux.AuditError, match="current interface"):
        audit_cli_ux.validate_audit(changed_manifest, refreshed)


def test_cli_ux_audit_attestation_binds_evidence_and_na_reason():
    manifest, audit = _documents()
    reviewed = deepcopy(audit)
    record = _failing_record(reviewed, "help")
    record["dimensions"]["help"] = "pass"
    reviewed["maximum_failures"]["help"] -= 1
    record["evidence"]["help"] = [
        "tests/test_cli.py::test_every_visible_command_path_exposes_help"
    ]
    _attest(record, "help")
    record["evidence"]["help"] = [
        "tests/test_cli.py::test_python_version_guard_allows_supported_interpreter"
    ]
    with pytest.raises(audit_cli_ux.AuditError, match="decision, and evidence"):
        audit_cli_ux.validate_audit(manifest, reviewed)

    not_applicable = deepcopy(audit)
    record = _failing_record(not_applicable, "preview")
    record["dimensions"]["preview"] = {
        "status": "not-applicable",
        "reason": "Read-only operation.",
    }
    not_applicable["maximum_failures"]["preview"] -= 1
    _attest(record, "preview")
    record["dimensions"]["preview"]["reason"] = "Different rationale."
    with pytest.raises(audit_cli_ux.AuditError, match="decision, and evidence"):
        audit_cli_ux.validate_audit(manifest, not_applicable)


def test_cli_ux_audit_rejects_test_text_inside_a_string(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ghost.py").write_text(
        '\"\"\"\ndef test_ghost():\n    pass\n\"\"\"\n', encoding="utf-8"
    )

    with pytest.raises(audit_cli_ux.AuditError, match="is not defined"):
        audit_cli_ux._validate_test_node(
            tmp_path,
            "tests/test_ghost.py::test_ghost",
            path="example",
            dimension="help",
        )


def test_cli_ux_audit_json_errors_are_structured(tmp_path: Path, capsys):
    result = audit_cli_ux.main(
        ["--root", str(tmp_path / "missing"), "--check", "--json"]
    )

    assert result == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_cli_ux_audit_bootstrap_is_exclusive_and_refresh_preserves_mode(
    tmp_path: Path, capsys
):
    root = tmp_path / "repo"
    docs = root / "docs"
    docs.mkdir(parents=True)
    shutil.copyfile(ROOT / audit_cli_ux.MANIFEST_REL, root / audit_cli_ux.MANIFEST_REL)

    assert audit_cli_ux.main(["--root", str(root), "--bootstrap", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    audit_path = root / audit_cli_ux.AUDIT_REL
    if os.name != "nt":
        audit_path.chmod(0o640)
    assert audit_cli_ux.main(["--root", str(root), "--refresh", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    if os.name != "nt":
        assert stat.S_IMODE(audit_path.stat().st_mode) == 0o640

    assert audit_cli_ux.main(["--root", str(root), "--bootstrap", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "exists" in payload["error"].casefold()
