"""Tests for the versioned image/video corpus runner."""

from __future__ import annotations

import hashlib
import json


from anvil_serving.benchmarking import multimodal

MODEL_REVISION = "a" * 40
ENGINE_REVISION = "b" * 40
ENGINE_BUILD_REF = "c" * 40


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


def test_endpoint_identity_accepts_llama_cpp_aliases(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({
                "data": [{
                    "id": "compatibility-alias",
                    "aliases": ["compatibility-alias", "truthful-candidate-alias"],
                }]
            }).encode("utf-8")

    monkeypatch.setattr(multimodal.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    assert multimodal._endpoint_models("http://127.0.0.1:30000/v1", None, 30) == [
        "compatibility-alias",
        "truthful-candidate-alias",
    ]


def test_dry_run_validates_without_endpoint_or_artifact(monkeypatch, tmp_path, capsys):
    image = _media(tmp_path, "scene.png", b"\x89PNG\r\n\x1a\nscene")
    corpus = _manifest(tmp_path, [_case([image])])
    output = tmp_path / "evidence.json"
    monkeypatch.setattr(
        multimodal,
        "_endpoint_models",
        lambda *_args: (_ for _ in ()).throw(AssertionError("endpoint called")),
    )

    rc = multimodal.main(_argv(corpus, output) + [
        "--temperature", "1", "--top-p", "0.95", "--dry-run",
    ])

    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["workload"] == "multimodal"
    assert plan["sampling"] == {
        "temperature": {"requested": 1.0, "effective_request": 1.0, "sent": True},
        "top_p": {"requested": 0.95, "effective_request": 0.95, "sent": True},
    }
    assert not output.exists()


def test_runner_records_explicit_video_ceiling(monkeypatch, tmp_path):
    video = _media(tmp_path, "clip.mp4", b"\x00\x00\x00\x18ftypmp42clip")
    corpus = _manifest(
        tmp_path,
        [_case([video] * 2, modality="video", sampling={"num_frames": 8})],
    )
    output = tmp_path / "evidence.json"
    monkeypatch.setattr(multimodal, "_endpoint_models", lambda *_args: ["agents-a1"])

    def fake_chat(*_args, **kwargs):
        assert kwargs["mm_processor_kwargs"] == {"num_frames": 8}
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "READY. RED changes to GREEN."},
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        }, 0.25

    rc = multimodal.main(
        _argv(corpus, output) + ["--max-videos-per-request", "2"],
        chat_request=fake_chat,
    )

    assert rc == 0
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["configuration"]["max_videos_per_request"] == 2
