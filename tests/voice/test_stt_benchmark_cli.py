from __future__ import annotations

import json
import wave

from anvil_serving.voice import cli
from anvil_serving.voice import corpus


MANIFEST = """
[voice]
name = "test"

[voice.stt]
base_url = "http://127.0.0.1:30010/v1"
model = "tdt-0.6b-v3"
stream = false
response_format = "json"

[voice.llm]
base_url = "http://127.0.0.1:8000/v1"
model = "llm.voice"

[voice.tts]
base_url = "http://127.0.0.1:30011/v1"
model = "kokoro"
response_format = "pcm"
""".strip()


def _voice_manifest(tmp_path):
    path = tmp_path / "voice.toml"
    path.write_text(MANIFEST, encoding="utf-8")
    return path


def _corpus_manifest(tmp_path):
    audio = tmp_path / "sample.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 160)
    record = {
        "schema_version": corpus.CORPUS_SCHEMA_VERSION,
        "id": "sample",
        "audio_path": audio.name,
        "reference_text": "hello",
        "category": "librispeech-test-clean",
        "language": "en",
        "source_identity": "fixture",
        "license": "test",
        "sha256": corpus.sha256_file(audio),
    }
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


def test_stt_scope_requires_corpus_and_evidence(tmp_path, capsys):
    config = _voice_manifest(tmp_path)
    assert cli.main(["benchmark", "--scope", "stt", "--config", str(config)]) == 2
    assert "--corpus" in capsys.readouterr().err
    corpus_manifest = _corpus_manifest(tmp_path)
    assert cli.main([
        "benchmark", "--scope", "stt", "--config", str(config),
        "--corpus", str(corpus_manifest),
    ]) == 2
    assert "--evidence-out" in capsys.readouterr().err


def test_stt_scope_applies_stt_only_overlay_and_writes_evidence(
    tmp_path, monkeypatch, capsys
):
    config = _voice_manifest(tmp_path)
    corpus_manifest = _corpus_manifest(tmp_path)
    overlay = tmp_path / "nemotron.toml"
    overlay.write_text(
        """
[voice.stt]
base_url = "http://127.0.0.1:39041/v1"
model = "nvidia/nemotron-3.5-asr-streaming-0.6b"
language = "en-US"

[stt_benchmark.identity]
revision = "f3d333"
runtime = "transformers-serve"
image_digest = "sha256:abc"
""".strip(),
        encoding="utf-8",
    )
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence_path = evidence_root / "nemotron.json"
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(evidence_root))
    seen = {}

    def fake_run(corpus_path, **kwargs):
        seen["corpus"] = corpus_path
        seen.update(kwargs)
        return {
            "schema_version": "stt-benchmark-evidence/v1",
            "complete": True,
            "runs": [],
        }

    monkeypatch.setattr(cli.stt_benchmark, "run_stt_benchmark", fake_run)
    rc = cli.main([
        "benchmark", "--scope", "stt", "--config", str(config),
        "--corpus", str(corpus_manifest),
        "--stt-candidate-overlay", str(overlay),
        "--candidate", "nemotron",
        "--repetitions", "3",
        "--concurrency", "4",
        "--auto-language-probes", "6",
        "--evidence-out", str(evidence_path),
    ])
    assert rc == 0
    assert seen["config"].model == "nvidia/nemotron-3.5-asr-streaming-0.6b"
    assert seen["config"].base_url == "http://127.0.0.1:39041/v1"
    assert seen["config"].language == "en-US"
    assert seen["endpoint_identity"]["revision"] == "f3d333"
    assert seen["repetitions"] == 3
    assert seen["concurrency"] == 4
    assert seen["auto_language_probe_count"] == 6
    assert json.loads(evidence_path.read_text())["complete"] is True
    assert "evidence written" in capsys.readouterr().out


def test_stt_scope_writes_incomplete_evidence_and_returns_failure(tmp_path, monkeypatch):
    config = _voice_manifest(tmp_path)
    corpus_manifest = _corpus_manifest(tmp_path)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence_path = evidence_root / "failed.json"
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(evidence_root))
    monkeypatch.setattr(
        cli.stt_benchmark,
        "run_stt_benchmark",
        lambda *args, **kwargs: {
            "schema_version": "stt-benchmark-evidence/v1",
            "complete": False,
            "failures": [{"case_id": "sample"}],
        },
    )
    rc = cli.main([
        "benchmark", "--scope", "stt", "--config", str(config),
        "--corpus", str(corpus_manifest),
        "--evidence-out", str(evidence_path),
    ])
    assert rc == 1
    assert json.loads(evidence_path.read_text())["complete"] is False


def test_stt_scope_rejects_llm_candidate_options(tmp_path, capsys):
    config = _voice_manifest(tmp_path)
    corpus_manifest = _corpus_manifest(tmp_path)
    rc = cli.main([
        "benchmark", "--scope", "stt", "--config", str(config),
        "--corpus", str(corpus_manifest),
        "--evidence-out", str(tmp_path / "out.json"),
        "--candidate-model", "wrong-surface",
    ])
    assert rc == 2
    assert "not LLM candidate options" in capsys.readouterr().err


def test_stt_overlay_rejects_non_stt_tables(tmp_path, capsys):
    config = _voice_manifest(tmp_path)
    corpus_manifest = _corpus_manifest(tmp_path)
    overlay = tmp_path / "bad.toml"
    overlay.write_text("[voice.llm]\nmodel='bad'\n", encoding="utf-8")
    rc = cli.main([
        "benchmark", "--scope", "stt", "--config", str(config),
        "--corpus", str(corpus_manifest),
        "--evidence-out", str(tmp_path / "out.json"),
        "--stt-candidate-overlay", str(overlay),
    ])
    assert rc == 2
    assert "only [voice.stt]" in capsys.readouterr().err


def test_corpus_validate_cli(tmp_path, capsys):
    manifest = _corpus_manifest(tmp_path)
    assert cli.main([
        "corpus", "validate", "--manifest", str(manifest), "--expected-cases", "1",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["case_count"] == 1
