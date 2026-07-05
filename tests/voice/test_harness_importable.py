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
    assert info["available"] is False


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
