"""Critical mutation and advisory safeguards for external benchmark tooling."""
import json
from pathlib import Path

from anvil_serving import cli as top_cli
from anvil_serving.external_benchmarks import cli, store
from anvil_serving.external_benchmarks.sources import ADAPTERS
from anvil_serving.external_benchmarks.sources.llmrequirements import LlmRequirementsAdapter


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "external_benchmarks"


def test_llmrequirements_advisory_source_preserves_no_promotion_claim():
    assert isinstance(ADAPTERS["llmrequirements"], LlmRequirementsAdapter)
    result = ADAPTERS["llmrequirements"].parse(
        (FIXTURES / "llmrequirements_sample.json").read_bytes(),
        source_url="https://llmrequirements.com/data/db.json",
    )
    assert len(result.rows) == 1
    assert "promotion_quality_evidence remains false" in result.warnings[0]


def test_external_import_requires_confirmation_but_dry_run_never_writes(capsys, tmp_path):
    db = tmp_path / "benchmarks.sqlite"
    fixture = FIXTURES / "millstone_sample.json"
    args = [
        "eval", "benchmark", "external", "import", "--source", "millstone",
        "--file", str(fixture), "--db", str(db),
    ]
    assert top_cli.main(args) == 3
    assert "confirmation required" in capsys.readouterr().err
    assert top_cli.main([*args, "--dry-run"]) == 0
    assert "would import" in capsys.readouterr().out
    assert not db.exists()


def test_external_import_refuses_oversized_input_before_db_write(monkeypatch, capsys, tmp_path):
    source = tmp_path / "large.json"
    source.write_bytes(b"12345")
    db = tmp_path / "benchmarks.sqlite"
    monkeypatch.setattr(cli, "MAX_IMPORT_BYTES", 4)
    assert cli.main([
        "import", "--source", "manual", "--file", str(source), "--db", str(db),
    ]) == 1
    assert "exceeds 4 byte limit" in capsys.readouterr().err
    assert not db.exists()


def test_external_export_is_atomic_and_backs_up_existing_target(tmp_path):
    db = tmp_path / "benchmarks.sqlite"
    store.init_db(db)
    target = tmp_path / "rows.json"
    target.write_text("operator content", encoding="utf-8")
    assert cli.main(["export", "--out", str(target), "--db", str(db)]) == 0
    assert json.loads(target.read_text(encoding="utf-8")) == []
    assert (tmp_path / "rows.json.anvil.bak.1").read_text(encoding="utf-8") == "operator content"
