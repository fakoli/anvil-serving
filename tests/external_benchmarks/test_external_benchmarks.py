import atexit
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from anvil_serving import cli as top_cli
from anvil_serving.external_benchmarks import cli, schema, store
from anvil_serving.external_benchmarks.normalize import (
    normalize_external_row,
    normalize_engine,
    normalize_gpu_name,
    normalize_model_identity,
)
from anvil_serving.external_benchmarks.sources.millstone import MillstoneAdapter


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "external_benchmarks"
SCRATCH = Path(__file__).resolve().parents[1] / ".scratch_external_benchmarks"
atexit.register(lambda: shutil.rmtree(SCRATCH, ignore_errors=True))


def _scratch(name):
    path = SCRATCH / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def test_schema_initialization_creates_expected_tables():
    db = _scratch("schema") / "benchmarks.sqlite"
    store.init_db(db)
    with sqlite3.connect(db) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert set(schema.EXPECTED_TABLES).issubset(names)


def test_import_mode_stores_raw_snapshot_and_sha256():
    db = _scratch("import") / "benchmarks.sqlite"
    fixture = FIXTURES / "millstone_sample.json"
    rc = cli.main(["import", "--source", "millstone", "--file", str(fixture), "--db", str(db)])
    assert rc == 0
    expected_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
    with store.connect(db) as conn:
        row = conn.execute(
            "SELECT raw_path, raw_sha256, parse_status FROM external_snapshots"
        ).fetchone()
    assert row["raw_sha256"] == expected_sha
    assert row["parse_status"] == "parsed"
    assert Path(row["raw_path"]).read_bytes() == fixture.read_bytes()


def test_store_snapshot_uses_unique_raw_paths_for_same_second(monkeypatch):
    db = _scratch("unique-snapshot") / "benchmarks.sqlite"
    raw = b'{"benchmarks":[]}'
    monkeypatch.setattr(store.time, "strftime", lambda *args: "20260101T000000Z")
    monkeypatch.setattr(store.time, "time_ns", lambda: 123456789)

    first = store.store_snapshot(
        db,
        source_name="millstone",
        raw_bytes=raw,
        original_name="snapshot.json",
        parser_name="millstone",
        parser_version="1",
        imported_at="2026-01-01T00:00:00Z",
    )
    second = store.store_snapshot(
        db,
        source_name="millstone",
        raw_bytes=raw,
        original_name="snapshot.json",
        parser_name="millstone",
        parser_version="1",
        imported_at="2026-01-01T00:00:00Z",
    )

    assert first["raw_path"] != second["raw_path"]
    assert Path(first["raw_path"]).read_bytes() == raw
    assert Path(second["raw_path"]).read_bytes() == raw


def test_raw_root_for_current_directory_db_stays_beside_db():
    assert store.raw_root_for_db("benchmarks.sqlite") == Path("external-benchmarks") / "raw"
    assert (
        store.raw_root_for_db(".anvil/benchmarks.sqlite")
        == Path(".anvil") / "external-benchmarks" / "raw"
    )


def test_import_marks_snapshot_failed_when_insert_rows_raises(monkeypatch, capsys):
    db = _scratch("insert-failure") / "benchmarks.sqlite"
    fixture = FIXTURES / "millstone_sample.json"

    def fail_insert(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "insert_rows", fail_insert)
    rc = cli.main(["import", "--source", "millstone", "--file", str(fixture), "--db", str(db)])
    captured = capsys.readouterr()

    assert rc == 1
    assert "importing rows failed" in captured.err
    with store.connect(db) as conn:
        row = conn.execute(
            "SELECT raw_path, parse_status, parse_error FROM external_snapshots"
        ).fetchone()
    assert row["parse_status"] == "failed"
    assert "database is locked" in row["parse_error"]
    assert Path(row["raw_path"]).read_bytes() == fixture.read_bytes()


def test_millstone_parser_extracts_rows_from_fixture_data():
    adapter = MillstoneAdapter()
    json_result = adapter.parse(
        (FIXTURES / "millstone_sample.json").read_bytes(),
        original_name="millstone_sample.json",
    )
    html_result = adapter.parse(
        (FIXTURES / "millstone_sample.html").read_bytes(),
        original_name="millstone_sample.html",
    )
    assert len(json_result.rows) >= 3
    assert len(html_result.rows) >= 3
    assert json_result.rows[0]["gpu_model"] == "rtx_pro_6000_blackwell_96gb"


def test_millstone_markdown_table_with_spaced_separator_parses():
    text = """
| Model | Engine | Precision | GPU | Context Tokens | Concurrency | Throughput tok/s |
| --- | --- | --- | --- | --- | --- | --- |
| Qwen3.6-35B-A3B-MTP | vLLM | NVFP4 | RTX PRO 6000 | 32K | 4 | 410 |
"""
    result = MillstoneAdapter().parse(text.encode(), original_name="millstone.md")
    assert len(result.rows) == 1
    assert result.rows[0]["context_tokens"] == 32768


def test_gpu_normalization_maps_rtx_pro_6000_variants():
    variants = [
        "RTX PRO 6000 Blackwell",
        "RTX PRO 6000",
        "RTX Pro 6000",
        "NVIDIA RTX PRO 6000 Blackwell 96GB",
    ]
    assert {normalize_gpu_name(v) for v in variants} == {"rtx_pro_6000_blackwell_96gb"}
    assert normalize_gpu_name("RTX 5090") == "rtx_5090_32gb"


def test_engine_normalization_maps_common_variants():
    assert normalize_engine("vllm") == "vLLM"
    assert normalize_engine("vLLM OpenAI") == "vLLM"
    assert normalize_engine("sglang") == "SGLang"
    assert normalize_engine("SGLang") == "SGLang"
    assert normalize_engine("llamacpp") == "llama.cpp"
    assert normalize_engine("TensorRT LLM") == "TensorRT-LLM"
    assert normalize_engine("LM Studio") is None


def test_model_normalization_maps_named_examples():
    cases = {
        "Qwen3.6-35B-A3B-MTP": ("qwen", "qwen3.6-35b-a3b-mtp"),
        "Nemotron-3-Super-120B-A12B": ("nemotron", "nemotron-3-super-120b-a12b"),
        "gpt-oss-20b": ("gpt-oss", "gpt-oss-20b"),
        "Gemma-4-31B": ("gemma", "gemma-4-31b"),
    }
    for raw, (family, variant) in cases.items():
        ident = normalize_model_identity(raw)
        assert ident["model_family"] == family
        assert ident["model_variant"] == variant


def test_number_normalization_handles_k_suffixes_by_metric_type():
    row = normalize_external_row(
        {
            "Model": "Qwen3.6-35B-A3B-MTP",
            "GPU": "RTX PRO 6000",
            "Context Tokens": "32K",
            "Prompt Tokens": "8K",
            "Output Tokens": "1K",
            "Throughput tok/s": "1.2k",
        }
    )
    assert row["context_tokens"] == 32768
    assert row["prompt_tokens"] == 8192
    assert row["output_tokens"] == 1024
    assert row["throughput_tok_s"] == 1200.0


def test_report_command_emits_markdown_with_expected_columns(capsys):
    db = _scratch("report") / "benchmarks.sqlite"
    fixture = FIXTURES / "millstone_sample.json"
    assert cli.main(["import", "--source", "millstone", "--file", str(fixture), "--db", str(db)]) == 0
    capsys.readouterr()
    rc = cli.main(["report", "--gpu", "RTX PRO 6000", "--format", "markdown", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "| source | model | gpu | engine | quant | ctx | conc | tok/s | ttft_ms |" in out
    assert "qwen3.6-35b-a3b-mtp" in out


def test_compare_command_warns_when_speculative_decoding_differs(capsys):
    db = _scratch("compare-warning") / "benchmarks.sqlite"
    fixture = FIXTURES / "millstone_sample.json"
    local = FIXTURES / "local_benchmark_sample.json"
    assert cli.main(["import", "--source", "millstone", "--file", str(fixture), "--db", str(db)]) == 0
    capsys.readouterr()
    rc = cli.main(["compare", "--local", str(local), "--gpu", "RTX PRO 6000", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Local run used NEXTN speculative decoding" in out
    assert "not an apples-to-apples" in out
    assert "Local run used prompt/prefix cache" in out


def test_compare_command_finds_exact_match(capsys):
    db = _scratch("compare-exact") / "benchmarks.sqlite"
    fixture = FIXTURES / "millstone_sample.json"
    local = FIXTURES / "local_benchmark_sample.json"
    assert cli.main(["import", "--source", "millstone", "--file", str(fixture), "--db", str(db)]) == 0
    capsys.readouterr()
    rc = cli.main(["compare", "--local", str(local), "--gpu", "RTX PRO 6000", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "exact external match" in out
    assert "| throughput_tok_s | 520.00 | 410.00 | +26.8% |" in out


def test_compare_gpu_arg_fills_missing_local_gpu(capsys):
    db = _scratch("compare-gpu-fallback") / "benchmarks.sqlite"
    fixture = FIXTURES / "millstone_sample.json"
    local = _scratch("compare-gpu-local") / "local.json"
    local.write_text(
        json.dumps(
            {
                "schema": "anvil-serving.benchmark/v1",
                "run_id": "local-no-gpu",
                "model": "Qwen3.6-35B-A3B-MTP",
                "engine": "vLLM",
                "quantization": "NVFP4",
                "context_tokens": 32768,
                "concurrency": 4,
                "metrics": {"throughput_tok_s": 500.0},
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["import", "--source", "millstone", "--file", str(fixture), "--db", str(db)]) == 0
    capsys.readouterr()
    rc = cli.main(["compare", "--local", str(local), "--gpu", "RTX PRO 6000", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "exact external match" in out
    assert "GPU differs" not in out


def test_compare_reports_nearest_external_rows_when_no_exact_match(capsys):
    db = _scratch("compare-nearest") / "benchmarks.sqlite"
    fixture = FIXTURES / "millstone_sample.json"
    local = _scratch("compare-nearest-local") / "local.json"
    local.write_text(
        json.dumps(
            {
                "run_id": "local-gemma",
                "model": "Gemma-4-31B",
                "engine": "TensorRT-LLM",
                "quantization": "MXFP4",
                "gpu_model": "RTX PRO 6000",
                "context_tokens": 32768,
                "concurrency": 2,
                "metrics": {"throughput_tok_s": 300.0},
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["import", "--source", "millstone", "--file", str(fixture), "--db", str(db)]) == 0
    capsys.readouterr()
    rc = cli.main(["compare", "--local", str(local), "--gpu", "RTX PRO 6000", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "match: nearest external row" in out
    assert "Nearest External Rows:" in out
    assert "model differs" in out


def test_top_level_cli_help_includes_external_bench(capsys):
    assert top_cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "external-bench" in out
