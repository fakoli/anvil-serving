"""Tests for the versioned image/video corpus runner."""

from __future__ import annotations

import hashlib
import json

import pytest

from anvil_serving.benchmarking import multimodal

MODEL_REVISION = "a" * 40
ENGINE_REVISION = "b" * 40


def _media(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    mime = "image/png" if name.endswith(".png") else "video/mp4"
    return {
        "path": name,
        "mime": mime,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _manifest(tmp_path, cases):
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps({
            "schema": "multimodal-corpus/v1",
            "provenance": {"license": "CC0-1.0", "generated": True},
            "cases": cases,
        }),
        encoding="utf-8",
    )
    return path


def _case(media, *, modality="image", sampling=None):
    value = {
        "id": "scene",
        "modality": modality,
        "media": media,
        "prompt": "Report the status labels.",
        "assertions": [
            {"type": "contains_casefold", "value": "ready"},
            {"type": "ordered_casefold", "values": ["red", "green"]},
        ],
        "repetitions": 2,
    }
    if sampling is not None:
        value["sampling"] = sampling
    return value


def _argv(corpus, output):
    return [
        "--base-url", "http://127.0.0.1:30000/v1",
        "--model", "agents-a1",
        "--model-revision", MODEL_REVISION,
        "--runtime-image", "vllm/vllm-openai:nightly-pinned",
        "--engine-revision", ENGINE_REVISION,
        "--hardware", "RTX PRO 6000 Blackwell 96GB",
        "--corpus", str(corpus),
        "--output", str(output),
        "--thinking-mode", "disabled",
    ]


def test_load_corpus_hashes_and_contains_media(tmp_path):
    image = _media(tmp_path, "scene.png", b"\x89PNG\r\n\x1a\nscene")
    corpus = _manifest(tmp_path, [_case([image])])

    loaded = multimodal.load_corpus(corpus)

    assert loaded["schema"] == "multimodal-corpus/v1"
    assert len(loaded["sha256"]) == 64
    assert loaded["cases"][0]["media"][0]["bytes"] == 13
    assert loaded["cases"][0]["media"][0]["_data_url"].startswith(
        "data:image/png;base64,"
    )


def test_video_sampling_is_exactly_one_of_fps_or_num_frames(tmp_path):
    video = _media(tmp_path, "clip.mp4", b"\x00\x00\x00\x18ftypmp42clip")
    corpus = _manifest(
        tmp_path,
        [_case(
            [video],
            modality="video",
            sampling={"fps": 1.0, "num_frames": 8},
        )],
    )

    with pytest.raises(ValueError, match="exactly fps or num_frames"):
        multimodal.load_corpus(corpus)


def test_corpus_rejects_hash_mismatch(tmp_path):
    image = _media(tmp_path, "scene.png", b"\x89PNG\r\n\x1a\nscene")
    image["sha256"] = "0" * 64
    corpus = _manifest(tmp_path, [_case([image])])

    with pytest.raises(ValueError, match="hash mismatch"):
        multimodal.load_corpus(corpus)


def test_assertions_are_deterministic_and_casefolded():
    results = multimodal.evaluate_assertions(
        "READY. First RED, then GREEN.",
        [
            {"type": "contains_casefold", "value": "ready"},
            {"type": "ordered_casefold", "values": ["red", "green"]},
        ],
    )

    assert all(result["passed"] for result in results)


def test_dry_run_validates_without_endpoint_or_artifact(monkeypatch, tmp_path, capsys):
    image = _media(tmp_path, "scene.png", b"\x89PNG\r\n\x1a\nscene")
    corpus = _manifest(tmp_path, [_case([image])])
    output = tmp_path / "evidence.json"
    monkeypatch.setattr(
        multimodal,
        "_endpoint_models",
        lambda *_args: (_ for _ in ()).throw(AssertionError("endpoint called")),
    )

    rc = multimodal.main(_argv(corpus, output) + ["--dry-run"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["workload"] == "multimodal"
    assert not output.exists()


def test_runner_records_identity_media_sampling_outputs_and_aggregates(
    monkeypatch, tmp_path
):
    image = _media(tmp_path, "scene.png", b"\x89PNG\r\n\x1a\nscene")
    corpus = _manifest(tmp_path, [_case([image])])
    output = tmp_path / "evidence.json"
    monkeypatch.setattr(multimodal, "_endpoint_models", lambda *_args: ["agents-a1"])

    seen_sampling = []

    def fake_chat(*_args, **kwargs):
        seen_sampling.append(kwargs["mm_processor_kwargs"])
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "READY. RED changes to GREEN."},
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        }, 0.25

    rc = multimodal.main(_argv(corpus, output), chat_request=fake_chat)

    assert rc == 0
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["schema"] == "multimodal-benchmark-evidence/v1"
    assert artifact["identity"]["model_revision"] == MODEL_REVISION
    assert artifact["identity"]["engine_revision"] == ENGINE_REVISION
    assert artifact["corpus"]["cases"][0]["media"][0]["sha256"] == image["sha256"]
    assert artifact["attempts"][0]["output"] == "READY. RED changes to GREEN."
    assert artifact["aggregates"]["modality"]["image"]["pass_rate"] == 1.0
    assert artifact["passed"] is True
    assert seen_sampling == [None, None]
