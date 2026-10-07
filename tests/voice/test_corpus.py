from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import pytest

from anvil_serving.voice import cli, corpus


def _wav(path, identity):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(identity.to_bytes(2, "little") * 160)


def _offline_archives(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    checksums = {}
    for split, url in corpus.LIBRISPEECH_ARCHIVES.items():
        archive = downloads / Path(url).name
        archive.write_bytes(split.encode())
        checksums[archive.name] = hashlib.md5(archive.read_bytes(), usedforsecurity=False).hexdigest()
    monkeypatch.setattr(corpus, "_expected_md5s", lambda: checksums)

    def fake_extract(archive, destination):
        split = archive.name.removesuffix(".tar.gz")
        root = destination / "LibriSpeech" / split
        root.mkdir(parents=True)
        transcript = []
        for bucket, seconds in enumerate((2.0, 7.0, 12.0)):
            for index in range(25):
                utterance = "%d-1-%04d" % (100 + index, bucket * 100 + index)
                audio = root / (utterance + ".flac")
                packed = (16000 << 44) | (15 << 36) | int(seconds * 16000)
                streaminfo = b"\0" * 10 + packed.to_bytes(8, "big") + b"\0" * 16
                audio.write_bytes(b"fLaC\x80\0\0\x22" + streaminfo + utterance.encode())
                transcript.append(utterance + " HOLDOUT REFERENCE " + utterance)
        (root / "fixture.trans.txt").write_text("\n".join(transcript) + "\n")

    monkeypatch.setattr(corpus, "_safe_extract", fake_extract)
    monkeypatch.setattr(corpus, "_download", lambda *args: pytest.fail("network download attempted"))
    return downloads


def _transcode(source, destination):
    split = source.parent.name
    first, _, last = source.stem.split("-")
    identity = int(first) + int(last) * 100 + (40000 if split == "test-other" else 0)
    _wav(destination, identity)


def test_prepare_120_human_holdouts_excludes_canonical_sources_and_audio(tmp_path, monkeypatch):
    downloads = _offline_archives(tmp_path, monkeypatch)
    canonical = tmp_path / "canonical"
    original = corpus.prepare_corpus(
        canonical, synthesize=lambda text: b"\0\0" * (160 + len(text)),
        transcode_flac=_transcode, download_dir=downloads,
    )
    assert original["case_count"] == 30
    output = tmp_path / "supplement"
    holdout = corpus.prepare_corpus(
        output, synthesize=lambda text: pytest.fail("human holdout invoked TTS"),
        transcode_flac=_transcode, download_dir=downloads, human_cases_per_split=60,
        synthetic_cases=0, exclude_manifest=canonical / "manifest.jsonl",
    )
    assert holdout["case_count"] == 120
    assert holdout["category_counts"] == {"librispeech-test-clean": 60, "librispeech-test-other": 60}
    old_ids = {case.id for case in original["cases"]}
    old_hashes = {case.sha256 for case in original["cases"]}
    assert not old_ids.intersection(case.id for case in holdout["cases"])
    assert not old_hashes.intersection(case.sha256 for case in holdout["cases"])
    assert len({case.sha256 for case in holdout["cases"]}) == 120
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["exclusion"]["manifest_sha256"] == corpus.sha256_file(canonical / "manifest.jsonl")
    assert provenance["exclusion"]["case_count"] == 30
    assert provenance["selection_counts"] == {"human_cases_per_split": 60, "synthetic_cases": 0, "expected_cases": 120}
    assert provenance["duration_bucket_counts"] == {
        "test-clean": {"short": 20, "medium": 20, "long": 20},
        "test-other": {"short": 20, "medium": 20, "long": 20},
    }
    assert len(provenance["selected_sources"]) == 120
    assert all(len(record["source_audio_sha256"]) == 64 for record in provenance["selected_sources"])
    again = corpus.prepare_corpus(
        tmp_path / "again", synthesize=lambda text: pytest.fail("TTS invoked"),
        transcode_flac=_transcode, download_dir=downloads, human_cases_per_split=60,
        synthetic_cases=0, exclude_manifest=canonical / "manifest.jsonl",
    )
    assert [case.id for case in holdout["cases"]] == [case.id for case in again["cases"]]
    assert not list(tmp_path.glob(".supplement.*"))


@pytest.mark.parametrize("kwargs", [
    {"human_cases_per_split": 0}, {"human_cases_per_split": 4},
    {"human_cases_per_split": True}, {"human_cases_per_split": 301},
    {"synthetic_cases": -1}, {"synthetic_cases": 7}, {"synthetic_cases": True},
])
def test_invalid_preparation_counts_fail_before_network_or_output(tmp_path, monkeypatch, kwargs):
    monkeypatch.setattr(corpus, "_expected_md5s", lambda: pytest.fail("network consulted"))
    with pytest.raises(corpus.CorpusError):
        corpus.prepare_corpus(
            tmp_path / "out", synthesize=lambda text: b"", transcode_flac=_transcode, **kwargs,
        )
    assert not (tmp_path / "out").exists()


def test_holdout_rejects_duplicate_audio_and_leaves_no_partial_corpus(tmp_path, monkeypatch):
    downloads = _offline_archives(tmp_path, monkeypatch)
    with pytest.raises(corpus.CorpusError, match="duplicate audio"):
        corpus.prepare_corpus(
            tmp_path / "out", synthesize=lambda text: b"", download_dir=downloads,
            transcode_flac=lambda source, destination: _wav(destination, 1),
            human_cases_per_split=3, synthetic_cases=0,
        )
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".out.*"))


def test_holdout_cli_forwards_counts_and_exclusion(tmp_path, monkeypatch):
    manifest = tmp_path / "voice.toml"
    manifest.write_text('''[voice]
name="fixture"
[voice.llm]
base_url="http://127.0.0.1:8000/v1"
model="llm.voice"
[voice.stt]
base_url="http://127.0.0.1:8090/v1"
model="stt"
[voice.tts]
base_url="http://127.0.0.1:8091/v1"
model="tts"
''')
    seen = {}

    def prepare(output, **kwargs):
        seen.update(kwargs)
        return {"case_count": 120}

    monkeypatch.setattr(cli.voice_corpus, "prepare_corpus", prepare)
    assert cli.main([
        "corpus", "prepare", "--config", str(manifest), "--out", str(tmp_path / "holdout"),
        "--ffmpeg", "fixture-ffmpeg", "--human-cases-per-split", "60", "--synthetic-cases", "0",
        "--exclude-manifest", "canonical.jsonl",
    ]) == 0
    assert seen["human_cases_per_split"] == 60
    assert seen["synthetic_cases"] == 0
    assert seen["exclude_manifest"] == "canonical.jsonl"
