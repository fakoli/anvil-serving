from __future__ import annotations

import hashlib
import json
import threading
import time
import wave
from pathlib import Path


from anvil_serving.voice import corpus
from anvil_serving.voice.stages.stt import STTStageConfig, transcribe_file
from anvil_serving.voice.stt_benchmark import (
    EVIDENCE_SCHEMA_VERSION,
    run_stt_benchmark,
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
