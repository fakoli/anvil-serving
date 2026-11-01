"""Compact guarded-harness readiness and failure representatives."""


def test_preflight_stt_main_returns_nonzero_when_candidate_errors(monkeypatch, tmp_path):
    from scripts.voice import preflight_stt

    monkeypatch.setattr(preflight_stt, "bring_up_and_wait", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr(preflight_stt, "run_one", lambda candidate, *_args: {
        "name": candidate.name, "base_url": candidate.base_url, "model": candidate.model,
        "stream": candidate.stream, "latency_ms": 0.0, "hypothesis": "", "wer": None, "error": "boom",
    })
    report = tmp_path / "stt.json"
    assert preflight_stt.main(["--candidate", "name=bad,base_url=http://127.0.0.1:8090/v1,model=bad", "--report", str(report)]) == 1
    assert report.exists()


def test_preflight_tts_run_one_fails_empty_successful_stream(monkeypatch):
    from scripts.voice import preflight_tts

    monkeypatch.setattr(preflight_tts, "stream_speech", lambda *_args: iter(()))
    result = preflight_tts.run_one(
        preflight_tts.Candidate(name="empty", base_url="http://127.0.0.1:30011/v1", model="kokoro"),
        "hello", 1.0, capture_dir=None,
    )
    assert result["error"] == "no audio bytes received" and result["audio_bytes"] == 0


def test_preflight_tts_readiness_failure_is_nonzero_and_health_is_required(monkeypatch, tmp_path):
    from scripts.voice import preflight_tts

    monkeypatch.setattr(preflight_tts, "readiness", lambda *_args, **_kwargs: {"ready": False, "models": {"ready": False}})
    monkeypatch.setattr(preflight_tts, "run_one", lambda candidate, *_args, **_kwargs: {
        "name": candidate.name, "base_url": candidate.base_url, "model": candidate.model,
        "ttfa_ms": 1.0, "synth_seconds": 0.1, "audio_seconds": 1.0, "rtf": 0.1,
        "audio_bytes": 4, "audio_sanity": {}, "audio_sanity_note": "fixture",
        "source_sample_rate": candidate.source_sample_rate, "capture_path": None,
        "quality": "not measured", "error": None,
    })
    assert preflight_tts.main(["--candidate", "name=not-ready,base_url=http://127.0.0.1:30011/v1,model=kokoro"]) == 1

    monkeypatch.setattr(preflight_tts, "endpoint_health", lambda *_args, **_kwargs: {"ready": False})
    monkeypatch.setattr(preflight_tts, "models_probe", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr(preflight_tts, "container_info", lambda *_args, **_kwargs: {"status": "running"})
    assert not preflight_tts.readiness(
        preflight_tts.Candidate(name="tts", base_url="http://127.0.0.1:30011/v1", model="kokoro", container_name="kokoro-tts"),
        timeout=1.0,
    )["ready"]


def test_preflight_tts_readiness_requires_expected_model_id(monkeypatch):
    from scripts.voice import preflight_tts

    monkeypatch.setattr(preflight_tts, "endpoint_health", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr(preflight_tts, "models_probe", lambda *_args, **_kwargs: {
        "ready": True, "payload": {"object": "list", "data": [{"id": "different-tts"}]},
    })
    monkeypatch.setattr(preflight_tts, "container_info", lambda *_args, **_kwargs: {"status": "running"})
    result = preflight_tts.readiness(
        preflight_tts.Candidate(name="tts", base_url="http://127.0.0.1:30011/v1", model="kokoro", container_name="kokoro-tts"),
        timeout=1.0,
    )
    assert result["ready"] is False and result["model_ready"] is False
