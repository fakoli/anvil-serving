"""Import-safety guard for the live-hardware harness scripts (anvil tasks
T007/T009/T010/T014/T016 -- UNIT 5).

None of these scripts are exercised end-to-end here (they need an sm_120
GPU, real audio hardware, or a running router+STT/TTS serves -- see each
script's own "NOT YET EXECUTED" module docstring). This test only proves the
one thing that IS verifiable in this dependency-light environment: importing
every harness module -- and the new ``anvil_serving.voice.connections.local_audio``
module -- never raises, even though ``torch``/``sounddevice``/``openai`` are
all absent here (see the module docstrings' guarded-import notes). A regressed
guard (e.g. someone moves a ``sounddevice``/``openai``/``torch`` import to
module level) would turn this into a hard ``ImportError`` at collection time
-- exactly the regression this test exists to catch.

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
    assert info["available"] is False


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
