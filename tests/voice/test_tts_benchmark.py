from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import wave

import pytest

from anvil_serving.voice import cli, config as voice_config
from anvil_serving.voice.stages.tts import TTSStageConfig


def _module():
    from anvil_serving.voice import tts_benchmark
    return tts_benchmark


def _corpus(tmp_path, records=None):
    records = records or [
        {"schema_version": "tts-corpus/v1", "id": "z-first", "text": "Hello.",
         "category": "short", "language": "en"},
        {"schema_version": "tts-corpus/v1", "id": "a-second", "text": "Cancel that reminder.",
         "category": "correction", "language": "en"},
    ]
    manifest = tmp_path / "corpus.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    return manifest


def _wav(pcm=b"\x01\0" * 240, rate=24000):
    out = io.BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm)
    return out.getvalue()


class _Response:
    def __init__(self, raw, status=200, content_type="application/octet-stream"):
        self.raw = io.BytesIO(raw)
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def read1(self, n):
        return self.raw.read(min(n, 64))

    def close(self):
        self.closed = True


def test_audio_directory_rejects_git_existing_and_symlink_paths(tmp_path):
    benchmark = _module()
    git_root = tmp_path / "repository"
    git_root.mkdir()
    (git_root / ".git").write_text("gitdir: elsewhere")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path / "target", target_is_directory=True)
    for target in [git_root / "audio", occupied, linked / "audio", tmp_path / ".." / "escape"]:
        with pytest.raises(benchmark.TTSBenchmarkError):
            benchmark.run_tts_benchmark(
                _corpus(tmp_path), config=TTSStageConfig(), audio_out=target,
                transport=lambda *args, **kwargs: pytest.fail("endpoint contacted"),
            )


def test_blank_nonfinite_and_repeated_signal_are_advisory(tmp_path):
    benchmark = _module()
    floats = struct.pack("<4f", 0.0, math.nan, math.inf, -math.inf)
    fmt = struct.pack("<HHIIHH", 3, 1, 16000, 64000, 4, 32)
    chunks = b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 16) + floats
    raw = b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks
    evidence = benchmark.run_tts_benchmark(
        _corpus(tmp_path), config=TTSStageConfig(response_format="wav"),
        audio_out=tmp_path / "audio", repetitions=1,
        transport=lambda *args, **kwargs: _Response(raw, content_type="audio/wav"),
    )
    assert evidence["complete"] is True
    advisory = evidence["cold_request"]["screening"]
    assert advisory["blank_audio"] is True
    assert advisory["nonfinite_samples"] == 3
    assert evidence["runs"][1]["screening"]["identical_audio_for_different_text"] is True
    assert evidence["quality_validation"]["independent_stt"] == "not_run"
    assert evidence["quality_validation"]["human_listening"] == "not_run"


def test_file_failures_count_partial_artifacts_toward_storage_limit(tmp_path, monkeypatch):
    benchmark = _module()
    monkeypatch.setattr(benchmark, "MAX_ARTIFACT_BYTES", 1000)
    original = wave.open

    def fail_writing_wav(file, mode=None):
        if mode == "wb":
            raise OSError("test failed writing WAV")
        return original(file, mode)

    monkeypatch.setattr(benchmark.wave, "open", fail_writing_wav)
    evidence = benchmark.run_tts_benchmark(
        _corpus(tmp_path), config=TTSStageConfig(), audio_out=tmp_path / "audio", repetitions=1,
        transport=lambda *args, **kwargs: _Response(b"\x01\0" * 240),
    )
    assert evidence["audio_artifacts"]["bytes"] == 480
    assert evidence["runs"][0]["failure"]["kind"] == "artifact_limit"
    assert sum(path.stat().st_size for path in (tmp_path / "audio").iterdir()) <= 1000


VOICE_MANIFEST = '''
[voice]
name = "fixture"
[voice.llm]
base_url = "http://127.0.0.1:8000/v1"
model = "llm.voice"
[voice.stt]
base_url = "http://127.0.0.1:8090/v1"
model = "stt"
[voice.tts]
base_url = "http://127.0.0.1:8091/v1"
model = "kokoro"
response_format = "pcm"
'''


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_cli_writes_tts_evidence_from_overlay_without_config_mutation(tmp_path, monkeypatch, capsys, newline):
    benchmark = _module()
    config = tmp_path / "voice.toml"
    manifest_bytes = VOICE_MANIFEST.replace("\n", newline).encode("utf-8")
    config.write_bytes(manifest_bytes)
    overlay = tmp_path / "candidate.toml"
    overlay.write_text('''[voice.tts]
model = "mlx-candidate"
response_format = "wav"
voice_id = "voice-name"
[tts_benchmark.identity]
revision = "pinned-revision"
runtime = "mlx-audio"
''')
    overlay_bytes = overlay.read_bytes()
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr(benchmark, "_default_transport", lambda *args, **kwargs: _Response(_wav(), content_type="audio/wav"))
    evidence_path = tmp_path / "evidence.json"
    assert cli.main([
        "benchmark", "--scope", "tts", "--config", str(config),
        "--tts-candidate-overlay", str(overlay), "--corpus", str(_corpus(tmp_path)),
        "--repetitions", "1", "--concurrency", "1", "--audio-out", str(tmp_path / "audio"),
        "--evidence-out", str(evidence_path),
    ]) == 0
    evidence = json.loads(evidence_path.read_text())
    assert evidence["endpoint"]["model"] == "mlx-candidate"
    assert evidence["endpoint"]["voice_id"] == "voice-name"
    assert evidence["endpoint"]["revision"] == "pinned-revision"
    assert evidence["configuration"]["candidate"] == "candidate"
    assert evidence["configuration"]["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert evidence["configuration"]["candidate_overlay_sha256"] == hashlib.sha256(overlay_bytes).hexdigest()
    assert config.read_bytes() == manifest_bytes
    assert overlay.read_bytes() == overlay_bytes
    assert str(tmp_path / "audio") not in capsys.readouterr().out
    with pytest.raises(voice_config.ConfigError, match="must be pcm"):
        voice_config.load_manifest(str(config), candidate_overlay={"voice": {"tts": {"response_format": "wav"}}})


def test_cli_requires_audio_corpus_evidence_and_rejects_other_scope_flags(tmp_path, capsys):
    for args, required in [([], "--corpus"), (["--corpus", "c.jsonl"], "--audio-out"),
                           (["--corpus", "c.jsonl", "--audio-out", "audio"], "--evidence-out")]:
        assert cli.main(["benchmark", "--scope", "tts", *args]) == 2
        assert required in capsys.readouterr().err
    assert cli.main(["benchmark", "--scope", "tts", "--candidate-model", "llm"]) == 2
    assert "LLM" in capsys.readouterr().err


def test_corpus_symlink_and_duplicate_fields_rejected(tmp_path):
    benchmark = _module()
    corpus = _corpus(tmp_path)
    linked = tmp_path / "linked.jsonl"
    linked.symlink_to(corpus)
    with pytest.raises(benchmark.TTSBenchmarkError, match="regular file"):
        benchmark.validate_tts_corpus(linked)
    corpus.write_text('{"schema_version":"tts-corpus/v1","id":"one","id":"two","text":"hello","category":"short","language":"en"}\n')
    with pytest.raises(benchmark.TTSBenchmarkError, match="duplicate fields"):
        benchmark.validate_tts_corpus(corpus)
