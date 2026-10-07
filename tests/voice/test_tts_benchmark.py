from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def test_sequential_schedule_retains_order_identity_audio_and_rtf(tmp_path):
    benchmark = _module()
    corpus = _corpus(tmp_path)
    requests = []
    raw = struct.pack("<240h", *range(240))
    responses = []
    now = [0.0]

    def clock():
        return now[0]

    def transport(url, *, data, headers, timeout):
        requests.append(json.loads(data))
        now[0] += 0.002
        response = _Response(raw)
        original = response.read1

        def read(n):
            now[0] += 0.001
            return original(n)

        response.read1 = read
        responses.append(response)
        return response

    evidence = benchmark.run_tts_benchmark(
        corpus, config=TTSStageConfig(model="kokoro", voice_id="af_voice"),
        audio_out=tmp_path / "audio", repetitions=2, transport=transport, clock=clock,
        endpoint_identity={"revision": "pinned"}, config_identity={"profile": "fixture"},
    )
    assert evidence["complete"] is True
    assert evidence["schedule"]["expected_warm_requests"] == 4
    assert [row["case_id"] for row in evidence["runs"]] == [
        "z-first", "a-second", "z-first", "a-second",
    ]
    assert [row["repetition"] for row in evidence["runs"]] == [1, 1, 2, 2]
    assert evidence["cold_request"]["repetition"] == 0
    assert evidence["endpoint"]["voice_id"] == "af_voice"
    assert evidence["endpoint"]["revision"] == "pinned"
    assert requests == [
        {"model": "kokoro", "input": text, "response_format": "pcm", "stream": True,
         "voice": "af_voice"}
        for text in ["Hello.", "Hello.", "Cancel that reminder.", "Hello.", "Cancel that reminder."]
    ]
    first = evidence["runs"][0]
    assert first["response"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert first["response"]["bytes"] == 480
    assert first["audio"]["duration_seconds"] == 0.01
    assert first["timing"]["first_audio_ms"] == 3.0
    assert first["timing"]["end_to_end_ms"] == 11.0
    assert first["generation_seconds_per_audio_second"] == 1.1
    assert first["audio_seconds_per_generation_second"] == pytest.approx(1 / 1.1)
    artifact = tmp_path / "audio" / first["audio"]["normalized_wav_path"]
    with wave.open(str(artifact), "rb") as stream:
        assert stream.getframerate() == 16000
        assert stream.getnframes() == 160
    assert all(response.closed for response in responses)
    encoded = json.dumps(evidence, allow_nan=False)
    assert str(tmp_path) not in encoded
    assert evidence["measurement_scope"]["acoustic_playback"] is False
    assert evidence["promotion"]["promoted"] is False


@pytest.mark.parametrize("raw,status,kind", [
    (b"", 200, "empty_audio"),
    (b"abc", 200, "malformed_audio"),
    (b"RIFF" + b"x" * 100, 200, "format_mismatch"),
    (b"private binary response", 503, "http_status"),
])
def test_failures_are_complete_false_and_never_copy_response_body(tmp_path, raw, status, kind):
    benchmark = _module()
    evidence = benchmark.run_tts_benchmark(
        _corpus(tmp_path), config=TTSStageConfig(), audio_out=tmp_path / "audio",
        repetitions=1, transport=lambda *args, **kwargs: _Response(raw, status),
    )
    assert evidence["complete"] is False
    assert len(evidence["failures"]) == 3
    assert all(row["failure"]["kind"] == kind for row in evidence["failures"])
    assert "private binary response" not in json.dumps(evidence)
    assert all(row["timing"]["first_audio_ms"] is None for row in evidence["failures"])


def test_wav_uses_header_sample_rate_and_audio_offset_for_first_audio(tmp_path):
    benchmark = _module()
    raw = _wav(rate=16000)
    evidence = benchmark.run_tts_benchmark(
        _corpus(tmp_path), config=TTSStageConfig(response_format="wav", source_sample_rate=24000),
        audio_out=tmp_path / "audio", repetitions=1,
        transport=lambda *args, **kwargs: _Response(raw, content_type="audio/wav"),
    )
    first = evidence["cold_request"]
    assert evidence["complete"] is True
    assert first["audio"]["source_sample_rate"] == 16000
    assert first["audio"]["duration_seconds"] == 0.015
    assert first["response"]["bytes"] == 524
    assert first["audio"]["source_pcm_bytes"] == 480
    assert first["timing"]["first_audio_ms"] >= first["timing"]["first_response_body_ms"]


@pytest.mark.parametrize("mutation", [
    {"id": "../escape"}, {"id": "a/b"}, {"text": "  "}, {"language": ""},
    {"schema_version": "tts-corpus/v2"}, {"audio_path": "../escape.wav"},
])
def test_corpus_rejects_bad_records_before_endpoint_io(tmp_path, mutation):
    benchmark = _module()
    row = {"schema_version": "tts-corpus/v1", "id": "valid", "text": "Hello.",
           "category": "short", "language": "en"}
    row.update(mutation)
    with pytest.raises(benchmark.TTSBenchmarkError):
        benchmark.run_tts_benchmark(
            _corpus(tmp_path, [row]), config=TTSStageConfig(), audio_out=tmp_path / "audio",
            transport=lambda *args, **kwargs: pytest.fail("endpoint contacted"),
        )
    assert not (tmp_path / "audio").exists()


def test_duplicate_empty_and_count_invalid_corpus(tmp_path):
    benchmark = _module()
    manifest = _corpus(tmp_path)
    first = json.loads(manifest.read_text().splitlines()[0])
    with pytest.raises(benchmark.TTSBenchmarkError, match="duplicate"):
        benchmark.validate_tts_corpus(_corpus(tmp_path, [first, first]))
    manifest.write_text("")
    with pytest.raises(benchmark.TTSBenchmarkError, match="no cases"):
        benchmark.validate_tts_corpus(manifest)
    with pytest.raises(benchmark.TTSBenchmarkError, match="contain 3"):
        benchmark.validate_tts_corpus(_corpus(tmp_path), expected_cases=3)


@pytest.mark.parametrize("kwargs", [
    {"repetitions": 0}, {"repetitions": True}, {"repetitions": 21},
    {"concurrency": 2}, {"concurrency": True},
    {"config": TTSStageConfig(timeout=math.nan)},
    {"config": TTSStageConfig(protocol="gepard")},
    {"config": TTSStageConfig(base_url="http://127.0.0.1:8091/v1?secret=bad")},
])
def test_invalid_schedule_and_endpoint_fail_before_io(tmp_path, kwargs):
    benchmark = _module()
    options = {"config": TTSStageConfig(), "repetitions": 1, **kwargs}
    with pytest.raises(benchmark.TTSBenchmarkError):
        benchmark.run_tts_benchmark(
            _corpus(tmp_path), audio_out=tmp_path / "audio", **options,
            transport=lambda *args, **kwargs: pytest.fail("endpoint contacted"),
        )


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


def test_response_limit_preserves_partial_metadata_without_binary_in_json(tmp_path, monkeypatch):
    benchmark = _module()
    monkeypatch.setattr(benchmark, "MAX_RESPONSE_BYTES", 128)
    evidence = benchmark.run_tts_benchmark(
        _corpus(tmp_path), config=TTSStageConfig(), audio_out=tmp_path / "audio",
        repetitions=1, transport=lambda *args, **kwargs: _Response(b"\x01\0" * 100),
    )
    assert evidence["complete"] is False
    assert evidence["cold_request"]["failure"]["kind"] == "response_limit"
    assert evidence["cold_request"]["response"]["complete"] is False
    assert evidence["cold_request"]["response"]["bytes"] <= 128


def test_large_wav_metadata_does_not_lose_first_audio_timing(tmp_path, monkeypatch):
    benchmark = _module()
    monkeypatch.setattr(benchmark, "MAX_CHUNK_RECORDS", 2)
    raw = _wav()
    metadata = b"JUNK" + struct.pack("<I", 256) + b"x" * 256
    raw = raw[:12] + metadata + raw[12:]
    raw = raw[:4] + struct.pack("<I", len(raw) - 8) + raw[8:]
    evidence = benchmark.run_tts_benchmark(
        _corpus(tmp_path), config=TTSStageConfig(response_format="wav"),
        audio_out=tmp_path / "audio", repetitions=1,
        transport=lambda *args, **kwargs: _Response(raw, content_type="audio/wav"),
    )
    assert evidence["complete"] is True
    assert evidence["cold_request"]["timing"]["first_audio_ms"] is not None
    assert evidence["cold_request"]["timing"]["chunk_records_truncated"] is True


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


def test_stdlib_http_posts_exact_voice_and_refuses_redirects(tmp_path):
    benchmark = _module()
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            observed.append((self.path, json.loads(body), self.headers.get("Authorization")))
            if json.loads(body)["input"] == "Hello.":
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                data = _wav()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(307)
                self.send_header("Location", "/other/audio/speech")
                self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        evidence = benchmark.run_tts_benchmark(
            _corpus(tmp_path), config=TTSStageConfig(
                base_url="http://127.0.0.1:%d/v1" % server.server_port,
                model="exact-model", voice_id="exact-voice", response_format="wav",
            ), audio_out=tmp_path / "audio", repetitions=1,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert evidence["complete"] is False
    assert evidence["runs"][1]["failure"]["kind"] == "http_status"
    assert evidence["runs"][1]["response"]["status"] == 307
    assert len(observed) == 3
    assert all(path == "/v1/audio/speech" for path, _, _ in observed)
    assert all(body["voice"] == "exact-voice" for _, body, _ in observed)


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


def test_cli_writes_tts_evidence_from_overlay_without_config_mutation(tmp_path, monkeypatch, capsys):
    benchmark = _module()
    config = tmp_path / "voice.toml"
    config.write_text(VOICE_MANIFEST)
    overlay = tmp_path / "candidate.toml"
    overlay.write_text('''[voice.tts]
model = "mlx-candidate"
response_format = "wav"
voice_id = "voice-name"
[tts_benchmark.identity]
revision = "pinned-revision"
runtime = "mlx-audio"
''')
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
    assert evidence["configuration"]["manifest_sha256"] == hashlib.sha256(VOICE_MANIFEST.encode()).hexdigest()
    assert config.read_text() == VOICE_MANIFEST
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


def test_network_error_details_and_bearer_values_never_enter_evidence(tmp_path, monkeypatch):
    benchmark = _module()
    monkeypatch.setenv("ANVIL_TEST_TOKEN", "private-token-value")
    received = []

    def fail(url, *, data, headers, timeout):
        received.append(headers["Authorization"])
        raise OSError("Bearer private-token-value: binary response b'private-audio'")

    evidence = benchmark.run_tts_benchmark(
        _corpus(tmp_path), config=TTSStageConfig(api_key_env="ANVIL_TEST_TOKEN"),
        audio_out=tmp_path / "audio", repetitions=1, transport=fail,
    )
    assert received == ["Bearer private-token-value"] * 3
    assert evidence["complete"] is False
    assert evidence["configuration"]["resolved_tts"]["api_key_env"] == "ANVIL_TEST_TOKEN"
    assert "private-token-value" not in json.dumps(evidence)
    assert "private-audio" not in json.dumps(evidence)


def test_cli_persists_failures_and_returns_nonzero(tmp_path, monkeypatch, capsys):
    benchmark = _module()
    config = tmp_path / "voice.toml"
    config.write_text(VOICE_MANIFEST)
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr(benchmark, "_default_transport", lambda *args, **kwargs: _Response(b"secret bytes", status=500))
    evidence_path = tmp_path / "failure.json"
    assert cli.main([
        "benchmark", "--scope", "tts", "--config", str(config), "--corpus", str(_corpus(tmp_path)),
        "--repetitions", "1", "--audio-out", str(tmp_path / "audio"), "--evidence-out", str(evidence_path),
    ]) == 1
    evidence = json.loads(evidence_path.read_text())
    assert evidence["complete"] is False
    assert len(evidence["failures"]) == 3
    output = capsys.readouterr()
    assert "secret bytes" not in output.out + output.err


def test_tts_overlay_rejects_mixed_tables_before_io(tmp_path, capsys):
    config = tmp_path / "voice.toml"
    config.write_text(VOICE_MANIFEST)
    overlay = tmp_path / "bad.toml"
    overlay.write_text('[voice.tts]\nmodel="candidate"\n[voice.llm]\nmodel="changed"\n')
    assert cli.main([
        "benchmark", "--scope", "tts", "--config", str(config), "--corpus", str(_corpus(tmp_path)),
        "--tts-candidate-overlay", str(overlay), "--audio-out", str(tmp_path / "audio"),
        "--evidence-out", str(tmp_path / "evidence.json"),
    ]) == 2
    assert "only [voice.tts]" in capsys.readouterr().err
    assert not (tmp_path / "audio").exists()


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


def test_maximum_schedule_has_bounded_chunk_records_and_exact_first_audio(tmp_path):
    benchmark = _module()
    records = [
        {"schema_version": "tts-corpus/v1", "id": "case-%03d" % index,
         "text": "Case %d." % index, "category": "short", "language": "en"}
        for index in range(128)
    ]
    evidence = benchmark.run_tts_benchmark(
        _corpus(tmp_path, records), config=TTSStageConfig(), audio_out=tmp_path / "audio",
        repetitions=8, transport=lambda *args, **kwargs: _Response(b"\x01\0" * 2400),
    )
    assert evidence["complete"] is True
    assert len(evidence["runs"]) == 1024
    requests = [evidence["cold_request"], *evidence["runs"]]
    assert all(len(row["timing"]["chunks"]) <= 64 for row in requests)
    assert sum(len(row["timing"]["chunks"]) for row in requests) <= 16384
    assert all(row["timing"]["chunk_records_truncated"] for row in requests)
    assert all(row["timing"]["first_audio_ms"] is not None for row in requests)


def test_warm_run_wall_time_labels_artifact_processing_and_excludes_endpoint_throughput(tmp_path, monkeypatch):
    benchmark = _module()
    now = [0.0]
    original_write = benchmark._write_artifacts

    def write_with_cost(*args, **kwargs):
        now[0] += 2.0
        return original_write(*args, **kwargs)

    monkeypatch.setattr(benchmark, "_write_artifacts", write_with_cost)
    evidence = benchmark.run_tts_benchmark(
        _corpus(tmp_path), config=TTSStageConfig(), audio_out=tmp_path / "audio", repetitions=1,
        transport=lambda *args, **kwargs: _Response(b"\x01\0" * 120), clock=lambda: now[0],
    )
    assert evidence["summary"]["warm_artifact_inclusive_wall_seconds"] == 4.0
    assert all(row["timing"]["end_to_end_ms"] == 0 for row in evidence["runs"])
    assert evidence["measurement_scope"]["warm_wall_includes_artifact_processing"] is True
    assert evidence["summary"]["endpoint_throughput_measured"] is False
    assert "warm_wall_seconds" not in evidence["summary"]
