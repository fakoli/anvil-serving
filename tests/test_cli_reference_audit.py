import copy
import json

import pytest

from scripts import audit_cli_references as audit

VOLATILE = {"files_scanned", "files_by_category", "canonical_counts"}


@pytest.fixture(scope="module")
def full_record():
    """One whole-repository scan shared by the tests that read it; copy before mutating."""
    return audit.inventory_record(audit.scan(audit.ROOT, "full"))


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


def test_json_record_keeps_volatile_counts_and_repo_inventory_matches(full_record):
    record = full_record
    assert VOLATILE <= record.keys()
    assert audit.inventory_matches(audit.ROOT, "full", record)


def test_new_allowlisted_legacy_reference_is_drift(full_record):
    record = copy.deepcopy(full_record)
    name = next(iter(audit.LEGACY_PATTERNS))
    record["allowed_legacy_counts"][name] += 1
    assert not audit.inventory_matches(audit.ROOT, "full", record)
    record["allowed_legacy_counts"][name] -= 1
    record["skill_roots"] = [*record["skill_roots"], "extra/skills"]
    assert not audit.inventory_matches(audit.ROOT, "full", record)


def test_added_file_does_not_change_repo_inventory(monkeypatch, tmp_path):
    (tmp_path / "docs").mkdir()
    first = tmp_path / "docs" / "a.md"
    first.write_text("# a\n\n`anvil-serving eval benchmark external`\n", encoding="utf-8")
    second = tmp_path / "docs" / "b.md"
    files = [first]
    monkeypatch.setattr(audit, "discover_files", lambda root, scope: (tmp_path, tuple(files)))

    def committed():
        record = audit.inventory_record(audit.scan(tmp_path, "full"))
        return record, json.dumps(audit.committed_record(record, "full"), sort_keys=True)

    before, before_json = committed()
    second.write_text("# b\n\n`anvil-serving eval benchmark external`\n", encoding="utf-8")
    files.append(second)
    after, after_json = committed()
    assert after["files_scanned"] == before["files_scanned"] + 1
    assert after["canonical_counts"] != before["canonical_counts"]
    assert after_json == before_json
