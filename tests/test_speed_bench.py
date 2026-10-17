"""Original synthetic prompts exercise the offline corpus trust boundary."""

import hashlib
import json
import os
import socket

import pytest

from anvil_serving.benchmarking import speed_bench as speed
from anvil_serving.benchmarking.jobs import BenchmarkJobError


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def corpus(tmp_path, *, rows=None, config="qualitative"):
    source = "https://example.test/synthetic"
    if rows is None:
        rows = [{"question_id": f"{category}-{n}", "category": category,
                 "turns": [f"Synthetic café task {n}.", "Explain the answer."],
                 "source": source, "src_id": str(n), "sub_category": None,
                 "difficulty": "synthetic", "multiturn": True}
                for category in ["coding", "rag", "reasoning", "writing"] for n in range(10)]
    raw = b"".join(canonical(row) + b"\n" for row in rows)
    data = tmp_path / "corpus.jsonl"
    data.write_bytes(raw)
    sources = {source: {"revision": "a" * 40, "license_url": "https://example.test/license",
                        "license_decision": "approved"}}
    manifest = {"schema": speed.CORPUS_SCHEMA, "dataset": "nvidia/SPEED-Bench",
                "dataset_revision": "b" * 40, "preparer_revision": "c" * 40,
                "config": config, "data_file": data.name, "data_sha256": sha(raw),
                "sources": sources, "sources_sha256": sha(canonical(sources))}
    path = tmp_path / "manifest.json"
    path.write_bytes(canonical(manifest))
    return path, data, manifest, rows


def test_selection_content_and_plan_identities(tmp_path):
    path, _, _, rows = corpus(tmp_path)
    plan = speed.plan_speed_bench(path)
    assert len(plan["selected"]) == 32 and plan["selected_turn_count"] == 64
    assert plan["execution_ready"] is False
    assert plan["provenance_status"] == "declarations_only"
    assert plan["nominal_input_tokens"] is None
    assert [r["question_id"] for r in plan["selected"] if r["category"] == "coding"] == [
        "coding-6", "coding-2", "coding-8", "coding-3", "coding-5", "coding-0", "coding-7", "coding-9",
    ]
    assert plan["plan_sha256"] == sha(canonical({k: v for k, v in plan.items() if k != "plan_sha256"}))
    for selected in plan["selected"]:
        row = next(r for r in rows if r["question_id"] == selected["question_id"])
        assert selected["row_sha256"] == sha(canonical(row))
    assert "Synthetic" not in json.dumps(plan) and "Explain" not in json.dumps(plan)
    assert speed.plan_speed_bench(path) == plan
    corpus(tmp_path, rows=list(reversed(rows)))
    reordered = speed.plan_speed_bench(path)
    assert reordered["selected"] == plan["selected"]
    assert reordered["plan_sha256"] != plan["plan_sha256"]
    assert reordered["corpus_sha256"] != plan["corpus_sha256"]
    changed_seed = speed.plan_speed_bench(path, seed=17)
    assert changed_seed["selected"] != reordered["selected"]
    rows[0]["turns"].reverse()
    corpus(tmp_path, rows=rows)
    changed = speed.plan_speed_bench(path, samples_per_category=10)
    changed_row = next(r for r in changed["selected"] if r["question_id"] == rows[0]["question_id"])
    assert changed_row["row_sha256"] == sha(canonical(rows[0]))


@pytest.mark.parametrize("config,bucket", [("throughput_1k", 1024), ("throughput_2k", 2048),
    ("throughput_8k", 8192), ("throughput_16k", 16384), ("throughput_32k", 32768)])
def test_entropy_categories_and_nominal_buckets(tmp_path, config, bucket):
    _, _, _, rows = corpus(tmp_path)
    rows = [dict(rows[0], question_id=c, category=c) for c in ["high_entropy", "mixed", "low_entropy"]]
    path, _, _, _ = corpus(tmp_path, rows=rows, config=config)
    plan = speed.plan_speed_bench(path, samples_per_category=1)
    assert plan["categories"] == ["high_entropy", "low_entropy", "mixed"]
    assert plan["nominal_input_tokens"] == bucket
    assert "measured_input_tokens" not in plan


@pytest.mark.parametrize("change", [
    {"question_id": ""}, {"question_id": 42}, {"question_id": "bad\nidentifier"},
    {"category": "mixed"}, {"category": []}, {"source": "https://example.test/unknown"},
    {"src_id": "\ud800"}, {"turns": []}, {"turns": [" "]}, {"turns": [None]},
    {"turns": ["x"] * 6}, {"turns": ["prefix " + speed.UNRESOLVED_TEXT]},
    {"turns": ["\ud800"]}, {"multiturn": 1}, {"multiturn": False},
    {"difficulty": {}}, {"sub_category": "\x1b[31m"}, {"unexpected": "value"},
])
def test_all_rows_are_validated_before_selection(tmp_path, change):
    _, data, manifest, rows = corpus(tmp_path)
    rows[-1].update(change)
    # ensure_ascii=True lets this test encode deliberately invalid surrogates.
    raw = b"".join(json.dumps(r).encode() + b"\n" for r in rows)
    data.write_bytes(raw)
    manifest["data_sha256"] = sha(raw)
    path = tmp_path / "manifest.json"
    path.write_bytes(canonical(manifest))
    with pytest.raises(BenchmarkJobError) as error:
        speed.plan_speed_bench(path, categories=["coding"])
    assert error.value.code == "bad_speed_corpus"
    assert "Synthetic" not in str(error.value)


@pytest.mark.parametrize("raw", [b'{}\n', b'[]\n', b'null\n', b'\xff\n',
    b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'{"x":Infinity}\n', b'\n',
    b'{"x":' + b'[' * 1100 + b'0' + b']' * 1100 + b'}\n'])
def test_malformed_jsonl_is_typed_and_prompt_free(tmp_path, raw):
    path, data, manifest, _ = corpus(tmp_path)
    data.write_bytes(raw)
    manifest["data_sha256"] = sha(raw)
    path.write_bytes(canonical(manifest))
    with pytest.raises(BenchmarkJobError):
        speed.plan_speed_bench(path)


@pytest.mark.parametrize("change", [
    {"dataset_revision": "main"}, {"preparer_revision": "abc123"},
    {"data_sha256": "a" * 64}, {"sources_sha256": "a" * 64},
    {"dataset": "other"}, {"config": "throughput_4k"}, {"config": []},
    {"schema": "v2"}, {"data_file": "../corpus.jsonl"}, {"data_file": "C:corpus"},
    {"sources": {}}, {"extra": 1},
])
def test_manifest_contract(tmp_path, change):
    path, _, manifest, _ = corpus(tmp_path)
    manifest.update(change)
    path.write_bytes(canonical(manifest))
    with pytest.raises(BenchmarkJobError):
        speed.plan_speed_bench(path)


@pytest.mark.parametrize("field,value", [("revision", "main"), ("license_url", "file:///license"),
    ("license_url", "https://user:secret@example.test/license"), ("license_decision", "pending")])
def test_source_declarations_need_pins_and_license_decisions(tmp_path, field, value):
    path, _, manifest, _ = corpus(tmp_path)
    next(iter(manifest["sources"].values()))[field] = value
    manifest["sources_sha256"] = sha(canonical(manifest["sources"]))
    path.write_bytes(canonical(manifest))
    with pytest.raises(BenchmarkJobError):
        speed.plan_speed_bench(path)


@pytest.mark.parametrize("options", [{"seed": True}, {"seed": -1}, {"seed": 2**64},
    {"samples_per_category": False}, {"samples_per_category": 0}, {"samples_per_category": 11},
    {"categories": []}, {"categories": ["coding", "coding"]}, {"categories": ["mixed"]},
    {"categories": [[]]}, {"categories": "coding"}])
def test_selection_controls(tmp_path, options):
    path, _, _, _ = corpus(tmp_path)
    with pytest.raises(BenchmarkJobError):
        speed.plan_speed_bench(path, **options)


def test_duplicate_id_and_content_bounds(tmp_path, monkeypatch):
    path, _, _, rows = corpus(tmp_path)
    corpus(tmp_path, rows=rows + [rows[0]])
    with pytest.raises(BenchmarkJobError, match="Duplicate question"):
        speed.plan_speed_bench(path)
    corpus(tmp_path, rows=rows)
    for constant in ["MAX_MANIFEST_BYTES", "MAX_CORPUS_BYTES", "MAX_ROW_BYTES", "MAX_ROWS"]:
        with monkeypatch.context() as patch:
            patch.setattr(speed, constant, 1)
            with pytest.raises(BenchmarkJobError):
                speed.plan_speed_bench(path, samples_per_category=1)


def test_long_upstream_shaped_source_ids_are_preserved_without_disclosure(tmp_path):
    _, _, _, rows = corpus(tmp_path)
    rows[-1]["src_id"] = "source-role-description-" * 500
    path, _, _, _ = corpus(tmp_path, rows=rows)
    plan = speed.plan_speed_bench(path, samples_per_category=10)
    selected = next(r for r in plan["selected"] if r["question_id"] == rows[-1]["question_id"])
    assert selected["row_sha256"] == sha(canonical(rows[-1]))
    assert "source-role-description" not in json.dumps(plan)


@pytest.mark.parametrize("control", ["\u202e", "\u2066", "\u2069", "\u200b", "\u2028", "\u2029"])
@pytest.mark.parametrize("field", ["question_id", "src_id", "source"])
def test_unicode_controls_cannot_spoof_plan_metadata(tmp_path, control, field):
    _, _, _, rows = corpus(tmp_path)
    rows[0][field] += control
    path, _, _, _ = corpus(tmp_path, rows=rows)
    with pytest.raises(BenchmarkJobError, match="Metadata"):
        speed.plan_speed_bench(path)


def test_file_confinement_and_regular_file_requirement(tmp_path):
    folder = tmp_path / "inside"
    folder.mkdir()
    path, data, _, _ = corpus(folder)
    outside = tmp_path / "outside.jsonl"
    data.rename(outside)
    data.symlink_to(outside)
    with pytest.raises(BenchmarkJobError, match="inside"):
        speed.plan_speed_bench(path)
    data.unlink()
    data.mkdir()
    with pytest.raises(BenchmarkJobError):
        speed.plan_speed_bench(path)
    data.rmdir()
    if hasattr(os, "mkfifo"):
        os.mkfifo(data)
        with pytest.raises(BenchmarkJobError, match="regular"):
            speed.plan_speed_bench(path)


def test_plan_uses_no_network_or_write_opens(tmp_path, monkeypatch):
    path, _, _, _ = corpus(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    original_open = os.open

    def read_only_open(path, flags, *args, **kwargs):
        assert flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) == 0
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", read_only_open)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network access"))
    speed.plan_speed_bench(path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_public_cli_manifest_help_and_dispatch(tmp_path, capsys, monkeypatch):
    from anvil_serving import cli
    from anvil_serving.commands import manifest_data

    path, _, _, _ = corpus(tmp_path)
    record = next(r for r in manifest_data()["commands"] if r["path"] == "eval benchmark speed plan")
    assert record["mutation_class"] == "read"
    assert record["execution_policy"] == "offline"
    assert record["remote_operation"] is None and record["transports"] == []
    assert cli.main(["eval", "benchmark", "speed", "plan", "--help"]) == 0
    assert "--manifest" in capsys.readouterr().out
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network access"))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    args = ["eval", "benchmark", "speed", "plan", "--manifest", str(path),
            "--categories", "coding,reasoning", "--samples-per-category", "2", "--seed", "17"]
    assert cli.main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["selected"]) == 4 and plan["execution_ready"] is False
    assert plan == speed.plan_speed_bench(path, categories=["coding", "reasoning"], samples_per_category=2, seed=17)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert cli.main(args + ["--categories", "coding,coding"]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["code"] == "bad_speed_corpus" and "Synthetic" not in str(error)


def test_public_json_result_and_typed_failure(tmp_path, capsys):
    from anvil_serving import cli

    path, _, _, _ = corpus(tmp_path)
    args = ["--json", "eval", "benchmark", "speed", "plan", "--manifest", str(path)]
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    envelope = json.loads(output)
    assert envelope["data"] == speed.plan_speed_bench(path)
    assert envelope["command"] == "eval benchmark speed plan"
    assert str(path) not in output
    assert cli.main(args + ["--categories", "coding,coding"]) == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["error"]["code"] == "bad_speed_corpus"
    assert envelope["data"] is None


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("option", [None, "--seed", "--samples-per-category", "--unknown"])
def test_public_errors_hide_private_operands(capsys, json_mode, option):
    from anvil_serving import cli

    private = "PRIVATE_OPERAND_" + "x" * 10000
    args = (["--json"] if json_mode else []) + [
        "eval", "benchmark", "speed", "plan", "--manifest", f"/private/{private}.json"]
    if option:
        args.extend([option, private])
    assert cli.main(args) == (2 if option else 1)
    captured = capsys.readouterr()
    assert private not in captured.out + captured.err
    assert len(captured.out + captured.err) < 2000
    assert json.loads(captured.out)["error"]["code"] == ("usage_error" if option else "bad_speed_corpus")


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("action", [[], ["plna"], ["PRIVATE_ACTION"]])
def test_incomplete_public_command_hides_operands(capsys, json_mode, action):
    from anvil_serving import cli

    args = (["--json"] if json_mode else []) + ["eval", "benchmark", "speed", *action,
        "--manifest", "/private/PRIVATE_MANIFEST.json"]
    assert cli.main(args) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_" not in captured.out + captured.err
    if json_mode:
        assert json.loads(captured.out)["command"] == "eval benchmark speed"
