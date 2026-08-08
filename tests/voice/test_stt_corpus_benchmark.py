from __future__ import annotations

import hashlib
import json
import threading
import time
import wave
from pathlib import Path

import pytest

from anvil_serving.voice import corpus
from anvil_serving.voice.corpus import CorpusError
from anvil_serving.voice.stages.stt import STTClientError, STTStageConfig, transcribe_file
from anvil_serving.voice.stt_benchmark import (
    EVIDENCE_SCHEMA_VERSION,
    STTBenchmarkError,
    character_error_rate,
    repetition_detected,
    run_stt_benchmark,
    word_error_rate,
)


def _wav(path: Path, frames: int = 1600) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * frames)


def _flac(path: Path, seconds: float = 1.0) -> None:
    total_samples = int(16000 * seconds)
    packed = (16000 << 44) | ((1 - 1) << 41) | ((16 - 1) << 36) | total_samples
    streaminfo = (
        (4096).to_bytes(2, "big")
        + (4096).to_bytes(2, "big")
        + b"\0" * 6
        + packed.to_bytes(8, "big")
        + b"\0" * 16
    )
    path.write_bytes(b"fLaC" + bytes([0x80, 0, 0, 34]) + streaminfo)


def _manifest(tmp_path: Path, cases: int = 2) -> Path:
    records = []
    for index in range(cases):
        audio = tmp_path / ("case-%d.wav" % index)
        _wav(audio, 1600 + index)
        records.append(
            {
                "schema_version": corpus.CORPUS_SCHEMA_VERSION,
                "id": "case-%d" % index,
                "audio_path": audio.name,
                "reference_text": "Hello, World!" if index == 0 else "Set a timer.",
                "category": "librispeech-test-clean" if index == 0 else "synthetic-timer",
                "language": "en",
                "source_identity": "fixture:%d" % index,
                "license": "test-only",
                "sha256": corpus.sha256_file(audio),
            }
        )
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return manifest


def test_audio_metadata_accepts_16k_mono_wav_and_flac(tmp_path):
    wav = tmp_path / "sample.wav"
    flac = tmp_path / "sample.flac"
    _wav(wav, 8000)
    _flac(flac, 2.0)
    assert corpus.audio_metadata(wav).duration_seconds == 0.5
    assert corpus.audio_metadata(flac).duration_seconds == 2.0


def test_audio_metadata_rejects_unsupported_and_wrong_wav_shape(tmp_path):
    with pytest.raises(CorpusError, match="unsupported"):
        corpus.audio_metadata(tmp_path / "sample.mp3")
    stereo = tmp_path / "stereo.wav"
    with wave.open(str(stereo), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0" * 400)
    with pytest.raises(CorpusError, match="16-kHz mono"):
        corpus.audio_metadata(stereo)


def test_validate_corpus_reports_identity_and_categories(tmp_path):
    result = corpus.validate_corpus(_manifest(tmp_path), expected_cases=2)
    assert result["schema_version"] == corpus.CORPUS_SCHEMA_VERSION
    assert result["case_count"] == 2
    assert len(result["manifest_sha256"]) == 64
    assert result["category_counts"] == {
        "librispeech-test-clean": 1,
        "synthetic-timer": 1,
    }


@pytest.mark.parametrize("mutation,match", [
    ("duplicate", "duplicate corpus id"),
    ("missing-reference", "reference_text"),
    ("hash", "hash mismatch"),
    ("escape", "escapes"),
])
def test_validate_corpus_fails_closed(tmp_path, mutation, match):
    manifest = _manifest(tmp_path)
    records = [json.loads(line) for line in manifest.read_text().splitlines()]
    if mutation == "duplicate":
        records[1]["id"] = records[0]["id"]
    elif mutation == "missing-reference":
        records[0]["reference_text"] = ""
    elif mutation == "hash":
        records[0]["sha256"] = "0" * 64
    else:
        records[0]["audio_path"] = "../outside.wav"
    manifest.write_text("".join(json.dumps(record) + "\n" for record in records))
    with pytest.raises(CorpusError, match=match):
        corpus.validate_corpus(manifest)


def test_select_librispeech_is_deterministic_across_durations_and_speakers(tmp_path):
    records = []
    for bucket, seconds in (("short", 2.0), ("medium", 7.0), ("long", 12.0)):
        for index in range(6):
            records.append(
                (
                    "%d-1-%s%d" % (100 + index, bucket[0], index),
                    "REFERENCE",
                    tmp_path / ("%s-%d.flac" % (bucket, index)),
                    seconds + index / 100,
                )
            )
    selected = corpus.select_librispeech_cases(reversed(records))
    assert len(selected) == 12
    assert [corpus._duration_bucket(item[3]) for item in selected] == (
        ["short"] * 4 + ["medium"] * 4 + ["long"] * 4
    )
    assert selected == corpus.select_librispeech_cases(records)


def _seed_offline_librispeech(tmp_path, monkeypatch) -> Path:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    archives = {}
    for split, url in corpus.LIBRISPEECH_ARCHIVES.items():
        archive = downloads / Path(url).name
        archive.write_bytes(("archive-" + split).encode())
        archives[archive.name] = hashlib.md5(archive.read_bytes(), usedforsecurity=False).hexdigest()
    monkeypatch.setattr(corpus, "_expected_md5s", lambda: archives)

    def fake_extract(archive, destination):
        split = archive.name.removesuffix(".tar.gz")
        root = destination / "LibriSpeech" / split
        for index, seconds in enumerate([2, 3, 4, 4.5, 6, 7, 8, 9, 11, 12, 13, 14]):
            speaker = 100 + index
            folder = root / str(speaker) / "1"
            folder.mkdir(parents=True, exist_ok=True)
            utterance = "%d-1-%04d" % (speaker, index)
            audio = folder / (utterance + ".flac")
            _flac(audio, seconds)
            (folder / ("%d-1.trans.txt" % speaker)).write_text(
                "%s TEST UTTERANCE %d\n" % (utterance, index),
                encoding="utf-8",
            )

    monkeypatch.setattr(corpus, "_safe_extract", fake_extract)
    return downloads


def test_prepare_corpus_is_transactional_and_builds_30_cases(tmp_path, monkeypatch):
    downloads = _seed_offline_librispeech(tmp_path, monkeypatch)
    output = tmp_path / "corpus"
    result = corpus.prepare_corpus(
        output,
        synthesize=lambda text: b"\0\0" * (160 + len(text)),
        transcode_flac=lambda source, destination: _wav(destination, 1600),
        download_dir=downloads,
    )
    assert result["case_count"] == 30
    assert (output / "manifest.jsonl").is_file()
    assert (output / "provenance.json").is_file()
    assert not list(tmp_path.glob(".corpus.*"))


def test_prepare_corpus_leaves_no_partial_output_on_synthesis_failure(tmp_path, monkeypatch):
    downloads = _seed_offline_librispeech(tmp_path, monkeypatch)
    output = tmp_path / "corpus"
    with pytest.raises(CorpusError, match="no PCM"):
        corpus.prepare_corpus(
            output,
            synthesize=lambda text: b"",
            transcode_flac=lambda source, destination: _wav(destination, 1600),
            download_dir=downloads,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".corpus.*"))


def test_wer_is_normalized_but_cer_preserves_case_and_punctuation():
    assert word_error_rate("Hello, WORLD!", "hello world") == 0.0
    assert character_error_rate("Hello!", "hello") > 0


def test_repetition_detection_flags_phrase_loops():
    assert repetition_detected("please wait please wait please wait")
    assert not repetition_detected("please wait while I check the timer")


def test_run_stt_benchmark_emits_complete_primary_and_synthetic_evidence(tmp_path):
    manifest = _manifest(tmp_path)
    active = 0
    maximum = 0
    lock = threading.Lock()

    def fake(path, config):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.005)
        text = "Hello, World!" if Path(path).stem == "case-0" else "Set a timer."
        with lock:
            active -= 1
        return {"text": text, "language": "en"}

    evidence = run_stt_benchmark(
        manifest,
        config=STTStageConfig(
            base_url="http://127.0.0.1:39041/v1",
            model="nemotron",
            language="en-US",
        ),
        repetitions=2,
        concurrency=2,
        endpoint_identity={"revision": "abc"},
        auto_language_probe_count=1,
        transcribe=fake,
    )
    assert evidence["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert evidence["complete"] is True
    assert evidence["schedule"]["observed_warm_requests"] == 4
    assert evidence["summary"]["primary_human"]["micro_wer"] == 0.0
    assert evidence["summary"]["synthetic_agent"]["micro_wer"] == 0.0
    assert evidence["endpoint"]["revision"] == "abc"
    assert evidence["language_detection_probes"][0]["language_tag_matches"] is True
    assert maximum == 2


@pytest.mark.parametrize("failure", ["malformed", "empty", "timeout", "repeated"])
def test_run_stt_benchmark_records_endpoint_failures_and_repetition(tmp_path, failure):
    manifest = _manifest(tmp_path, cases=1)

    def fake(path, config):
        if failure == "malformed":
            raise STTClientError("missing text")
        if failure == "empty":
            return {"text": ""}
        if failure == "timeout":
            raise TimeoutError("timed out")
        return {"text": "hello hello hello hello hello"}

    evidence = run_stt_benchmark(
        manifest,
        config=STTStageConfig(stream=False),
        repetitions=1,
        transcribe=fake,
    )
    if failure == "repeated":
        assert evidence["complete"] is True
        assert evidence["gate_observations"]["repetition_flags"] == 2
    else:
        assert evidence["complete"] is False
        assert evidence["summary"]["all"]["failed"] == 1
        assert evidence["failures"]


def test_run_stt_benchmark_fails_closed_on_cold_request_failure(tmp_path):
    manifest = _manifest(tmp_path, cases=1)
    calls = 0

    def fake(path, config):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("cold request timed out")
        return {"text": "hello world"}

    evidence = run_stt_benchmark(
        manifest,
        config=STTStageConfig(stream=False),
        repetitions=1,
        transcribe=fake,
    )
    assert evidence["summary"]["all"]["failed"] == 0
    assert evidence["complete"] is False
    assert evidence["gate_observations"]["malformed_or_failed_runs"] == 1
    assert len(evidence["failures"]) == 1


@pytest.mark.parametrize(
    "base_url",
    (
        "http://localhost:39041/v1",
        "http://user:password@127.0.0.1:39041/v1",
        "http://127.0.0.1:39041/transcriptions",
        "file:///tmp/v1",
    ),
)
def test_run_stt_benchmark_rejects_invalid_endpoint_before_requests(tmp_path, base_url):
    manifest = _manifest(tmp_path, cases=1)
    with pytest.raises(STTBenchmarkError):
        run_stt_benchmark(
            manifest,
            config=STTStageConfig(base_url=base_url),
            repetitions=1,
            transcribe=lambda *_args, **_kwargs: pytest.fail("request should not run"),
        )


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def read(self):
        return self.payload

    def close(self):
        self.closed = True


def test_transcribe_file_sends_language_and_validates_json(tmp_path):
    audio = tmp_path / "sample.wav"
    _wav(audio)
    response = _Response(b'{"text":"hello","language":"en"}')
    seen = {}

    def transport(url, *, data, headers, timeout):
        seen.update(url=url, data=data, headers=headers, timeout=timeout)
        return response

    result = transcribe_file(
        audio,
        STTStageConfig(
            base_url="http://127.0.0.1:39041/v1",
            model="m",
            language="en-US",
        ),
        transport=transport,
    )
    assert result["text"] == "hello"
    assert b'name="language"\r\n\r\nen-US' in seen["data"]
    assert b'name="stream"' not in seen["data"]
    assert response.closed


def test_transcribe_file_rejects_malformed_response(tmp_path):
    audio = tmp_path / "sample.wav"
    _wav(audio)
    with pytest.raises(STTClientError, match="valid JSON"):
        transcribe_file(
            audio,
            STTStageConfig(),
            transport=lambda *args, **kwargs: _Response(b"not-json"),
        )


def test_transcribe_file_rejects_unparsed_provider_envelope(tmp_path):
    audio = tmp_path / "sample.wav"
    _wav(audio)
    with pytest.raises(STTClientError, match="unparsed provider ASR envelope"):
        transcribe_file(
            audio,
            STTStageConfig(),
            transport=lambda *args, **kwargs: _Response(
                b'{"text":"language English<asr_text>Hello."}'
            ),
        )
