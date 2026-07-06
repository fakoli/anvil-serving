"""Import-safety guard for the live-hardware harness scripts (anvil tasks
T007/T009/T010/T014/T016 -- UNIT 5).

Some of these scripts also have live-hardware tests elsewhere, but this module
keeps the dependency-light import and negative-path guarantees: importing every
harness module -- and the new ``anvil_serving.voice.connections.local_audio``
module -- never raises, even though ``torch``/``sounddevice``/``openai`` may be
absent here. A regressed guard (e.g. someone moves a ``sounddevice``/``openai``/
``torch`` import to module level) would turn this into a hard ``ImportError`` at
collection time -- exactly the regression this test exists to catch.

Dependency-light: stdlib ``importlib`` + ``pytest`` only. No GPU, no audio
device, no network.
"""
from __future__ import annotations

import importlib

import pytest

HARNESS_MODULES = [
    "anvil_serving.voice.connections.local_audio",
    "scripts.voice._real_pipeline",
    "scripts.voice.preflight_stt",
    "scripts.voice.preflight_tts",
    "scripts.voice.local_loop_demo",
    "scripts.voice.realtime_sdk_client_demo",
    "scripts.voice.mini_validation",
]


@pytest.mark.parametrize("module_name", HARNESS_MODULES)
def test_harness_module_imports_without_gpu_audio_or_network(module_name):
    module = importlib.import_module(module_name)
    assert module is not None


def test_local_audio_duplex_raises_a_clear_error_without_sounddevice():
    """Constructing (not just importing) the real duplex must fail with our
    own typed error, not a bare ImportError/OSError leaking out of
    sounddevice, when the package/PortAudio isn't available (true in this
    environment -- see the confirmed absence check this test relies on)."""
    from anvil_serving.voice.connections.local_audio import LocalAudioDuplex, LocalAudioUnavailable

    try:
        import sounddevice  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("sounddevice is installed in this environment; the negative-path guard isn't exercised")

    with pytest.raises(LocalAudioUnavailable):
        LocalAudioDuplex()


def test_preflight_stt_gpu_info_never_raises_without_torch():
    from scripts.voice.preflight_stt import gpu_info

    info = gpu_info()
    assert isinstance(info, dict)
    assert isinstance(info["available"], bool)
    if info["available"]:
        assert info["source"] in ("torch", "nvidia-smi")


def test_preflight_stt_report_flag_has_packet_path_default():
    from scripts.voice.preflight_stt import DEFAULT_REPORT_PATH, build_parser

    args = build_parser().parse_args(["--report"])
    assert args.report == DEFAULT_REPORT_PATH


def test_preflight_stt_candidate_parses_stream_mode():
    from scripts.voice.preflight_stt import parse_candidate_arg

    candidate = parse_candidate_arg(
        "name=parakeet,base_url=http://127.0.0.1:30010/v1,model=tdt_ctc-110m,container=parakeet-stt,stream=false"
    )

    assert candidate.stream is False
    assert candidate.container_name == "parakeet-stt"


def test_preflight_stt_run_one_uses_candidate_stream_mode(monkeypatch):
    from scripts.voice import preflight_stt

    seen = []

    def fake_transcribe_stream(pcm, sample_rate, config):
        seen.append(config.stream)
        yield ("hello world", True)

    monkeypatch.setattr(preflight_stt, "transcribe_stream", fake_transcribe_stream)

    result = preflight_stt.run_one(
        preflight_stt.Candidate(
            name="parakeet",
            base_url="http://127.0.0.1:8090/v1",
            model="tdt_ctc-110m",
            stream=False,
        ),
        b"\x00\x00",
        16000,
        "hello world",
        1.0,
    )

    assert seen == [False]
    assert result["stream"] is False
    assert result["error"] is None


def test_preflight_stt_run_one_records_unexpected_candidate_failure(monkeypatch):
    from scripts.voice import preflight_stt

    def fake_transcribe_stream(pcm, sample_rate, config):
        raise TimeoutError("timed out")
        yield ("unreachable", True)

    monkeypatch.setattr(preflight_stt, "transcribe_stream", fake_transcribe_stream)

    result = preflight_stt.run_one(
        preflight_stt.Candidate(
            name="slow",
            base_url="http://127.0.0.1:8090/v1",
            model="slow",
        ),
        b"\x00\x00",
        16000,
        "hello world",
        1.0,
    )

    assert result["error"] == "timed out"
    assert result["wer"] is None


def test_preflight_stt_main_returns_nonzero_when_candidate_errors(monkeypatch, tmp_path):
    from scripts.voice import preflight_stt

    monkeypatch.setattr(
        preflight_stt,
        "bring_up_and_wait",
        lambda candidate, *, ready_timeout, do_bring_up: {"ready": True},
    )
    monkeypatch.setattr(
        preflight_stt,
        "run_one",
        lambda candidate, pcm, sample_rate, reference_text, timeout: {
            "name": candidate.name,
            "base_url": candidate.base_url,
            "model": candidate.model,
            "stream": candidate.stream,
            "latency_ms": 0.0,
            "hypothesis": "",
            "wer": None,
            "error": "boom",
        },
    )
    report = tmp_path / "stt.json"

    rc = preflight_stt.main([
        "--candidate",
        "name=bad,base_url=http://127.0.0.1:8090/v1,model=bad",
        "--report",
        str(report),
    ])

    assert rc == 1
    assert report.exists()


def test_preflight_tts_gpu_info_never_raises_without_torch():
    from scripts.voice.preflight_tts import gpu_info

    info = gpu_info()
    assert isinstance(info, dict)
    assert isinstance(info["available"], bool)
    if info["available"]:
        assert info["source"] in ("torch", "nvidia-smi")


def test_preflight_tts_report_flag_has_packet_path_default():
    from scripts.voice.preflight_tts import DEFAULT_REPORT_PATH, build_parser

    args = build_parser().parse_args(["--report"])
    assert args.report == DEFAULT_REPORT_PATH


def test_preflight_tts_default_candidates_cover_t009_required_set():
    from scripts.voice.preflight_tts import DEFAULT_CANDIDATES

    assert [candidate["name"] for candidate in DEFAULT_CANDIDATES] == [
        "kokoro-82m",
        "orpheus-3b",
        "qwen3-tts-1.7b",
    ]
    assert [candidate["model"] for candidate in DEFAULT_CANDIDATES] == [
        "kokoro",
        "orpheus-3b",
        "qwen3-tts",
    ]
    assert all(candidate["base_url"].startswith("http://127.0.0.1:") for candidate in DEFAULT_CANDIDATES)


def test_preflight_tts_main_uses_full_default_set_and_fails_when_one_candidate_fails(monkeypatch, tmp_path):
    from scripts.voice import preflight_tts

    seen = []

    def fake_readiness(candidate, *, timeout):
        return {"ready": candidate.name != "qwen3-tts-1.7b"}

    def fake_run_one(candidate, text, timeout, *, capture_dir):
        seen.append(candidate.name)
        failed = candidate.name == "qwen3-tts-1.7b"
        return {
            "name": candidate.name,
            "base_url": candidate.base_url,
            "model": candidate.model,
            "ttfa_ms": None if failed else 1.0,
            "synth_seconds": 0.1,
            "audio_seconds": 0.0 if failed else 1.0,
            "rtf": None if failed else 0.1,
            "audio_bytes": 0 if failed else 4,
            "audio_sanity": None if failed else {"samples": 2, "rms": 1.58, "peak": 2, "nonzero_samples": 2},
            "audio_sanity_note": "not measured; no audio captured" if failed else "automated PCM sanity only; no human listening pass by this script",
            "source_sample_rate": candidate.source_sample_rate,
            "capture_path": None,
            "quality": "not measured; human listening pass required",
            "error": "boom" if failed else None,
        }

    monkeypatch.setattr(preflight_tts, "readiness", fake_readiness)
    monkeypatch.setattr(preflight_tts, "run_one", fake_run_one)
    report = tmp_path / "tts.json"

    rc = preflight_tts.main(["--report", str(report)])

    assert rc == 1
    assert seen == ["kokoro-82m", "orpheus-3b", "qwen3-tts-1.7b"]
    payload = report.read_text(encoding="utf-8")
    assert '"name": "qwen3-tts-1.7b"' in payload
    assert '"ready": false' in payload


def test_preflight_tts_candidate_parses_container_name():
    from scripts.voice.preflight_tts import parse_candidate_arg

    candidate = parse_candidate_arg(
        "name=kokoro,base_url=http://127.0.0.1:30011/v1,model=kokoro,container=kokoro-tts,source_sample_rate=24000"
    )

    assert candidate.container_name == "kokoro-tts"
    assert candidate.source_sample_rate == 24000


def test_preflight_tts_run_one_records_unexpected_candidate_failure(monkeypatch):
    from scripts.voice import preflight_tts

    def fake_stream_speech(text, config):
        raise TimeoutError("timed out")
        yield b"unreachable"

    monkeypatch.setattr(preflight_tts, "stream_speech", fake_stream_speech)

    result = preflight_tts.run_one(
        preflight_tts.Candidate(
            name="slow",
            base_url="http://127.0.0.1:30011/v1",
            model="slow",
        ),
        "hello world",
        1.0,
        capture_dir=None,
    )

    assert result["error"] == "timed out"
    assert result["rtf"] is None


def test_preflight_tts_run_one_records_audio_sanity_not_quality(monkeypatch):
    from scripts.voice import preflight_tts

    def fake_stream_speech(text, config):
        yield b"\x01\x00\x02\x00"

    monkeypatch.setattr(preflight_tts, "stream_speech", fake_stream_speech)

    result = preflight_tts.run_one(
        preflight_tts.Candidate(
            name="kokoro",
            base_url="http://127.0.0.1:30011/v1",
            model="kokoro",
        ),
        "hello world",
        1.0,
        capture_dir=None,
    )

    assert result["error"] is None
    assert result["audio_sanity"] == {
        "samples": 2,
        "rms": 1.58,
        "peak": 2,
        "nonzero_samples": 2,
    }
    assert result["audio_sanity_note"] == "automated PCM sanity only; no human listening pass by this script"
    assert result["quality"] == "not measured; human listening pass required"


def test_preflight_tts_run_one_fails_empty_successful_stream(monkeypatch):
    from scripts.voice import preflight_tts

    def fake_stream_speech(text, config):
        if False:
            yield b"unreachable"

    monkeypatch.setattr(preflight_tts, "stream_speech", fake_stream_speech)

    result = preflight_tts.run_one(
        preflight_tts.Candidate(
            name="empty",
            base_url="http://127.0.0.1:30011/v1",
            model="kokoro",
        ),
        "hello world",
        1.0,
        capture_dir=None,
    )

    assert result["error"] == "no audio bytes received"
    assert result["ttfa_ms"] is None
    assert result["audio_bytes"] == 0


def test_preflight_tts_main_returns_nonzero_when_candidate_errors(monkeypatch, tmp_path):
    from scripts.voice import preflight_tts

    monkeypatch.setattr(
        preflight_tts,
        "readiness",
        lambda candidate, *, timeout: {"ready": True},
    )
    monkeypatch.setattr(
        preflight_tts,
        "run_one",
        lambda candidate, text, timeout, *, capture_dir: {
            "name": candidate.name,
            "base_url": candidate.base_url,
            "model": candidate.model,
            "ttfa_ms": None,
            "synth_seconds": 0.0,
            "audio_seconds": 0.0,
            "rtf": None,
            "audio_bytes": 0,
            "audio_sanity": None,
            "audio_sanity_note": "not measured; no audio captured",
            "source_sample_rate": candidate.source_sample_rate,
            "capture_path": None,
            "quality": "not measured; human listening pass required",
            "error": "boom",
        },
    )
    report = tmp_path / "tts.json"

    rc = preflight_tts.main([
        "--candidate",
        "name=bad,base_url=http://127.0.0.1:30011/v1,model=bad",
        "--report",
        str(report),
    ])

    assert rc == 1
    assert report.exists()


def test_preflight_tts_main_returns_nonzero_when_readiness_fails(monkeypatch, tmp_path):
    from scripts.voice import preflight_tts

    monkeypatch.setattr(
        preflight_tts,
        "readiness",
        lambda candidate, *, timeout: {"ready": False, "models": {"ready": False}},
    )
    monkeypatch.setattr(
        preflight_tts,
        "run_one",
        lambda candidate, text, timeout, *, capture_dir: {
            "name": candidate.name,
            "base_url": candidate.base_url,
            "model": candidate.model,
            "ttfa_ms": 1.0,
            "synth_seconds": 0.1,
            "audio_seconds": 1.0,
            "rtf": 0.1,
            "audio_bytes": 4,
            "audio_sanity": {"samples": 2, "rms": 1.58, "peak": 2, "nonzero_samples": 2},
            "audio_sanity_note": "automated PCM sanity only; no human listening pass by this script",
            "source_sample_rate": candidate.source_sample_rate,
            "capture_path": None,
            "quality": "not measured; human listening pass required",
            "error": None,
        },
    )
    report = tmp_path / "tts.json"

    rc = preflight_tts.main([
        "--candidate",
        "name=not-ready,base_url=http://127.0.0.1:30011/v1,model=kokoro",
        "--report",
        str(report),
    ])

    assert rc == 1
    payload = report.read_text(encoding="utf-8")
    assert '"ready": false' in payload


def test_preflight_tts_readiness_requires_health_models_and_container(monkeypatch):
    from scripts.voice import preflight_tts

    monkeypatch.setattr(
        preflight_tts,
        "endpoint_health",
        lambda base_url, *, timeout: {"ready": False, "url": "http://127.0.0.1:30011/health"},
    )
    monkeypatch.setattr(
        preflight_tts,
        "models_probe",
        lambda base_url, *, timeout: {"ready": True, "url": "http://127.0.0.1:30011/v1/models"},
    )
    monkeypatch.setattr(
        preflight_tts,
        "container_info",
        lambda container_name: {"status": "running"},
    )

    result = preflight_tts.readiness(
        preflight_tts.Candidate(
            name="kokoro",
            base_url="http://127.0.0.1:30011/v1",
            model="kokoro",
            container_name="kokoro-tts",
        ),
        timeout=1.0,
    )

    assert result["ready"] is False


def test_preflight_tts_readiness_requires_expected_model_id(monkeypatch):
    from scripts.voice import preflight_tts

    monkeypatch.setattr(
        preflight_tts,
        "endpoint_health",
        lambda base_url, *, timeout: {"ready": True, "url": "http://127.0.0.1:30011/health"},
    )
    monkeypatch.setattr(
        preflight_tts,
        "models_probe",
        lambda base_url, *, timeout: {
            "ready": True,
            "url": "http://127.0.0.1:30011/v1/models",
            "payload": {"object": "list", "data": [{"id": "different-tts"}]},
        },
    )
    monkeypatch.setattr(
        preflight_tts,
        "container_info",
        lambda container_name: {"status": "running"},
    )

    result = preflight_tts.readiness(
        preflight_tts.Candidate(
            name="kokoro",
            base_url="http://127.0.0.1:30011/v1",
            model="kokoro",
            container_name="kokoro-tts",
        ),
        timeout=1.0,
    )

    assert result["ready"] is False
    assert result["model_ready"] is False
    assert result["advertised_models"] == ["different-tts"]


def test_preflight_tts_readiness_accepts_openai_models_payload(monkeypatch):
    from scripts.voice import preflight_tts

    monkeypatch.setattr(
        preflight_tts,
        "endpoint_health",
        lambda base_url, *, timeout: {"ready": True, "url": "http://127.0.0.1:30011/health"},
    )
    monkeypatch.setattr(
        preflight_tts,
        "models_probe",
        lambda base_url, *, timeout: {
            "ready": True,
            "url": "http://127.0.0.1:30011/v1/models",
            "payload": {"object": "list", "data": [{"id": "tts-1"}, {"id": "kokoro"}]},
        },
    )
    monkeypatch.setattr(
        preflight_tts,
        "container_info",
        lambda container_name: {"status": "running"},
    )

    result = preflight_tts.readiness(
        preflight_tts.Candidate(
            name="kokoro",
            base_url="http://127.0.0.1:30011/v1",
            model="kokoro",
            container_name="kokoro-tts",
        ),
        timeout=1.0,
    )

    assert result["ready"] is True
    assert result["model_ready"] is True
    assert result["advertised_models"] == ["tts-1", "kokoro"]


def test_preflight_tts_allow_errors_accepts_readiness_failure(monkeypatch):
    from scripts.voice import preflight_tts

    monkeypatch.setattr(
        preflight_tts,
        "readiness",
        lambda candidate, *, timeout: {"ready": False},
    )
    monkeypatch.setattr(
        preflight_tts,
        "run_one",
        lambda candidate, text, timeout, *, capture_dir: {
            "name": candidate.name,
            "base_url": candidate.base_url,
            "model": candidate.model,
            "ttfa_ms": 1.0,
            "synth_seconds": 0.1,
            "audio_seconds": 1.0,
            "rtf": 0.1,
            "audio_bytes": 4,
            "audio_sanity": {"samples": 2, "rms": 1.58, "peak": 2, "nonzero_samples": 2},
            "audio_sanity_note": "automated PCM sanity only; no human listening pass by this script",
            "source_sample_rate": candidate.source_sample_rate,
            "capture_path": None,
            "quality": "not measured; human listening pass required",
            "error": None,
        },
    )

    rc = preflight_tts.main([
        "--candidate",
        "name=not-ready,base_url=http://127.0.0.1:30011/v1,model=kokoro",
        "--allow-errors",
    ])

    assert rc == 0


def test_realtime_sdk_client_demo_run_session_reports_missing_openai_cleanly():
    """Without the `openai` package installed (true in this environment),
    `run_session` must return a clean nonzero exit code and print a helpful
    message -- never raise ImportError out of `main()`."""
    from scripts.voice.realtime_sdk_client_demo import run_session

    try:
        import openai  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("openai is installed in this environment; the negative-path guard isn't exercised")

    rc = run_session(
        ws_url="ws://127.0.0.1:0", text="hello", sample_path=None,
        barge_in_after=None, capture=None, timeout=1.0,
    )
    assert rc == 2


def test_simple_energy_vad_model_is_speech_on_silence_and_tone():
    from scripts.voice._real_pipeline import SimpleEnergyVADModel

    model = SimpleEnergyVADModel(threshold=500.0)
    silence = b"\x00\x00" * 100
    assert model.is_speech(silence) is False

    # A large-amplitude alternating pattern reads as high-energy "speech".
    loud = (b"\x00\x7f" * 100)
    assert model.is_speech(loud) is True
