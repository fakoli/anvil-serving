import json

from scripts import audit_cli_references as audit

VOLATILE = {"files_scanned", "files_by_category", "canonical_counts"}


def test_committed_inventory_omits_volatile_counts():
    value = json.loads((audit.ROOT / audit.INVENTORY_REL).read_text(encoding="utf-8"))
    assert value["schema_version"] == audit.SCHEMA_VERSION == 2
    for record in value["scopes"].values():
        assert not VOLATILE & record.keys()
        assert {"skill_roots", "allowed_legacy_counts", "violation_counts"} <= record.keys()


def test_fixture_scope_keeps_full_record():
    value = json.loads(
        (audit.ROOT / audit.FIXTURE_REL / "expected.json").read_text(encoding="utf-8")
    )
    assert VOLATILE <= value["record"].keys()
    result = audit.scan(audit.ROOT, "fixtures")
    assert audit.inventory_matches(audit.ROOT, "fixtures", audit.inventory_record(result))


def test_json_record_keeps_volatile_counts_and_repo_inventory_matches():
    record = audit.inventory_record(audit.scan(audit.ROOT, "full"))
    assert VOLATILE <= record.keys()
    assert audit.inventory_matches(audit.ROOT, "full", record)


def test_new_allowlisted_legacy_reference_is_drift():
    record = audit.inventory_record(audit.scan(audit.ROOT, "full"))
    name = next(iter(audit.LEGACY_PATTERNS))
    record["allowed_legacy_counts"][name] += 1
    assert not audit.inventory_matches(audit.ROOT, "full", record)
    record["allowed_legacy_counts"][name] -= 1
    record["skill_roots"] = [*record["skill_roots"], "extra/skills"]
    assert not audit.inventory_matches(audit.ROOT, "full", record)


def test_added_file_does_not_change_repo_inventory():
    record = audit.inventory_record(audit.scan(audit.ROOT, "full"))
    record["files_scanned"] += 1
    record["canonical_counts"][next(iter(record["canonical_counts"]))] += 1
    assert audit.inventory_matches(audit.ROOT, "full", record)
