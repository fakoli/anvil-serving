"""Hermetic tests for the local-loop live-proof harness.

No real audio device, network, Docker, or GPU is touched here. The live proof
itself remains manual/hardware-backed; these tests pin the capture artifacts and
manifest shape so the acceptance command is runnable on fakoli-dark.
"""
from __future__ import annotations

import json
from pathlib import Path

from anvil_serving.voice import config as voice_config
from scripts.voice import local_loop_demo


def test_capture_flag_without_value_uses_default_prefix():
    args = local_loop_demo.build_parser().parse_args(["--capture"])

    prefix = local_loop_demo.resolve_capture_prefix(args.capture)

    assert prefix is not None
    assert "anvil-voice-captures" in prefix
    assert "local-loop-" in prefix


def test_audio_diagnostics_flags_are_early_modes():
    args = local_loop_demo.build_parser().parse_args(["--list-devices"])
    assert args.list_devices is True

    args = local_loop_demo.build_parser().parse_args([
        "--meter-inputs",
        "--meter-seconds",
        "0.25",
        "--input-sample-rate",
        "48000",
    ])
    assert args.meter_inputs is True
    assert args.meter_seconds == 0.25
    assert args.input_sample_rate == 48000


def test_list_devices_main_exits_before_manifest_or_pipeline_setup(monkeypatch):
    monkeypatch.setattr(local_loop_demo, "list_audio_devices", lambda: 0)
    monkeypatch.setattr(
        local_loop_demo,
        "load_manifest_or_die",
        lambda _path: (_ for _ in ()).throw(AssertionError("manifest should not load")),
    )
    monkeypatch.setattr(
        local_loop_demo,
        "RealVoicePipeline",
        lambda _config: (_ for _ in ()).throw(AssertionError("pipeline should not construct")),
    )

    assert local_loop_demo.main(["--list-devices"]) == 0


def test_meter_inputs_main_exits_before_manifest_or_pipeline_setup(monkeypatch):
    def fake_meter_inputs(**kwargs):
        assert kwargs["input_device"] == 6
        assert kwargs["sample_rate"] == 48000
        return 0

    monkeypatch.setattr(local_loop_demo, "meter_inputs", fake_meter_inputs)
    monkeypatch.setattr(
        local_loop_demo,
        "load_manifest_or_die",
        lambda _path: (_ for _ in ()).throw(AssertionError("manifest should not load")),
    )
    monkeypatch.setattr(
        local_loop_demo,
        "RealVoicePipeline",
        lambda _config: (_ for _ in ()).throw(AssertionError("pipeline should not construct")),
    )

    assert local_loop_demo.main(["--meter-inputs", "--input-device", "6", "--input-sample-rate", "48000"]) == 0


def test_local_audio_config_separates_input_and_output_rates():
    cfg = local_loop_demo.LocalAudioConfig(
        sample_rate=16000,
        input_sample_rate=48000,
        output_sample_rate=16000,
        frame_ms=20,
    )

    assert cfg.effective_input_sample_rate == 48000
    assert cfg.effective_output_sample_rate == 16000
    assert cfg.frame_samples == 960


def test_local_audio_duplex_opens_input_and_output_at_separate_rates(monkeypatch):
    calls = []

    class FakeStream:
        def __init__(self, kind, **kwargs):
            calls.append((kind, kwargs))

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

        def write(self, _pcm):
            return None

    class FakeSoundDevice:
        @staticmethod
        def RawInputStream(**kwargs):
            return FakeStream("input", **kwargs)

        @staticmethod
        def RawOutputStream(**kwargs):
            return FakeStream("output", **kwargs)

    monkeypatch.setattr(
        local_loop_demo.LocalAudioDuplex,
        "_import_sounddevice",
        staticmethod(lambda: FakeSoundDevice),
    )
    audio = local_loop_demo.LocalAudioDuplex(
        local_loop_demo.LocalAudioConfig(
            sample_rate=16000,
            input_sample_rate=48000,
            output_sample_rate=16000,
            frame_ms=20,
            input_device=79,
            output_device=8,
        )
    )

    audio.start()

    assert calls[0][0] == "input"
    assert calls[0][1]["samplerate"] == 48000
    assert calls[0][1]["blocksize"] == 960
    assert calls[0][1]["device"] == 79
    assert calls[1][0] == "output"
    assert calls[1][1]["samplerate"] == 16000
    assert calls[1][1]["device"] == 8


def test_normalize_input_frame_downsamples_to_pipeline_rate():
    samples = [0, 1000, 2000, 3000, 4000, 5000]
    frame = b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples)

    normalized = local_loop_demo.normalize_input_frame(frame, 48000)

    assert len(normalized) == 4  # 6 samples at 48 kHz -> 2 samples at 16 kHz.
    assert local_loop_demo.normalize_input_frame(frame, local_loop_demo.PIPELINE_INPUT_SAMPLE_RATE) == frame


def test_numeric_device_args_are_resolved_to_indices():
    assert local_loop_demo.resolve_device_arg(None) is None
    assert local_loop_demo.resolve_device_arg("6") == 6
    assert local_loop_demo.resolve_device_arg(" 26 ") == 26
    assert local_loop_demo.resolve_device_arg("SteelSeries Sonar - Microphone") == "SteelSeries Sonar - Microphone"


def test_pcm_int16_stats_reports_rms_peak_and_nonzero_samples():
    pcm = b"\x00\x00" + b"\x00\x04" + b"\x00\xfc"  # 0, 1024, -1024

    stats = local_loop_demo.pcm_int16_stats(pcm)

    assert stats["samples"] == 3
    assert stats["rms"] == 836.09
    assert stats["peak"] == 1024
    assert stats["nonzero_samples"] == 2


def test_meter_input_device_uses_guarded_sounddevice_import(monkeypatch):
    class FakeStream:
        def __init__(self, *, callback, **_kwargs):
            self.callback = callback

        def __enter__(self):
            self.callback(b"\x00\x00" + b"\x00\x04", 2, None, None)
            return self

        def __exit__(self, *_args):
            return False

    class FakeSoundDevice:
        RawInputStream = FakeStream

    monkeypatch.setattr(
        local_loop_demo.LocalAudioDuplex,
        "_import_sounddevice",
        staticmethod(lambda: FakeSoundDevice),
    )
    monkeypatch.setattr(local_loop_demo.time, "sleep", lambda _seconds: None)

    result = local_loop_demo.meter_input_device(
        3,
        seconds=0.25,
        sample_rate=16000,
        frame_ms=20,
        threshold=500.0,
    )

    assert result["ok"] is True
    assert result["device"] == 3
    assert result["frames"] == 1
    assert result["peak"] == 1024
    assert result["above_threshold"] is True


def test_fakoli_dark_manifest_is_valid_and_pins_fast_tier():
    data = voice_config.load_manifest("examples/voice/fakoli-dark.toml")

    assert data["voice"]["llm"]["base_url"] == "http://100.87.34.66:8000/v1"
    assert data["voice"]["llm"]["model"] == "fast-local"
    assert data["voice"]["llm"]["expected_route_provider"] == "fast-local"
    assert data["voice"]["llm"]["expected_route_model"] == "qwen36-27b"
    assert data["voice"]["llm"]["expected_route_tier"] == "local"
    assert data["voice"]["stt"]["base_url"] == "http://127.0.0.1:30010/v1"
    assert data["voice"]["stt"]["stream"] is False
    assert data["voice"]["tts"]["base_url"] == "http://127.0.0.1:30011/v1"


def test_write_capture_writes_full_bundle_and_finding_row(tmp_path, monkeypatch):
    findings_doc = tmp_path / "local-loop-proof.md"
    findings_doc.write_text(
        "\n".join(
            [
                "# Proof",
                "",
                "## Session log",
                "",
                "| timestamp (UTC) | turns completed | barge-in observed? | avg TTFA (ms) | avg turn latency (ms) | route probe provider | mic recording | assistant recording | session JSON |",
                "|---|---:|---|---:|---:|---|---|---|---|",
                "| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |",
                "",
                "## Findings",
                "",
                "_TBD_",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(local_loop_demo, "FINDINGS_DOC", findings_doc)
    prefix = str(tmp_path / "proof")
    turns = [
        local_loop_demo.TurnMetric(
            turn_index=0,
            turn_id="turn-1",
            generation=2,
            ttfa_ms=12.3,
            turn_latency_ms=45.6,
            transcript="hello route proof",
            barge_in=True,
            stale_audio_dropped=1,
            output_bytes=4,
        )
    ]
    events = [
        {"kind": "vad_started", "turn_id": "turn-1", "generation": 2, "barge_in": True},
        {"kind": "first_audio", "turn_id": "turn-1", "generation": 2, "ttfa_ms": 12.3},
    ]
    route_proof = {
        "ok": True,
        "status": 200,
        "response": {"provider": "fast-local", "model": "qwen36-27b", "tier": "local"},
    }
    finding_status = {"row_written": False}

    artifacts = local_loop_demo.write_capture(
        prefix,
        [b"\x00\x00\x01\x00"],
        [b"\x01\x00\x02\x00"],
        16000,
        16000,
        turns,
        events,
        route_proof,
        "test manifest",
        finding_status=finding_status,
    )

    for path in artifacts.values():
        assert Path(path).exists()
    latency = json.loads((tmp_path / "proof.latency.json").read_text(encoding="utf-8"))
    session = json.loads((tmp_path / "proof.session.json").read_text(encoding="utf-8"))
    event_lines = (tmp_path / "proof.events.jsonl").read_text(encoding="utf-8").splitlines()

    assert latency["turns_completed"] == 1
    assert latency["barge_in_observed"] is True
    assert session["artifacts"]["input_wav"].endswith(".input.wav")
    assert session["route_proof"]["response"]["provider"] == "fast-local"
    assert [json.loads(line)["kind"] for line in event_lines] == ["vad_started", "first_audio"]
    row = findings_doc.read_text(encoding="utf-8")
    assert "fast-local" in row
    assert ".session.json" in row
    assert row.index("fast-local") < row.index("## Findings")
    assert sum(1 for line in row.splitlines() if line.startswith("| _TBD_ |")) == 1
    assert finding_status["row_written"] is True


def test_write_capture_can_skip_finding_row_for_failed_diagnostic_bundle(tmp_path, monkeypatch):
    findings_doc = tmp_path / "local-loop-proof.md"
    monkeypatch.setattr(local_loop_demo, "FINDINGS_DOC", findings_doc)

    artifacts = local_loop_demo.write_capture(
        str(tmp_path / "failed"),
        [b"\x00\x00"],
        [],
        16000,
        16000,
        [],
        [],
        {"ok": False, "error": "route failed"},
        "test manifest",
        append_finding=False,
    )

    assert Path(artifacts["session_json"]).exists()
    assert not findings_doc.exists()


def test_append_finding_row_requires_session_log_table(tmp_path, monkeypatch):
    findings_doc = tmp_path / "local-loop-proof.md"
    findings_doc.write_text("# Proof\n\n## Findings\n", encoding="utf-8")
    monkeypatch.setattr(local_loop_demo, "FINDINGS_DOC", findings_doc)

    assert local_loop_demo.append_finding_row("| now | 1 |") is False
    assert "| now | 1 |" not in findings_doc.read_text(encoding="utf-8")


def test_capture_acceptance_requires_route_barge_in_latency_and_audio():
    route_proof = {"ok": True}

    def metric(**overrides):
        data = {
            "turn_index": 0,
            "turn_id": "turn-1",
            "generation": 2,
            "ttfa_ms": 1.0,
            "turn_latency_ms": 2.0,
            "transcript": "hello",
            "barge_in": True,
            "output_bytes": 4,
        }
        data.update(overrides)
        return local_loop_demo.TurnMetric(**data)

    assert local_loop_demo.capture_acceptance_passed(
        "proof", [metric()], [], route_proof, 1, [b"\x00\x00"]
    )
    assert not local_loop_demo.capture_acceptance_passed(
        "proof", [metric(ttfa_ms=None)], [], route_proof, 1, [b"\x00\x00"]
    )
    assert not local_loop_demo.capture_acceptance_passed(
        "proof", [metric(output_bytes=0)], [], route_proof, 1, [b"\x00\x00"]
    )
    assert not local_loop_demo.capture_acceptance_passed(
        "proof", [metric(barge_in=False)], [], route_proof, 1, [b"\x00\x00"]
    )
    assert not local_loop_demo.capture_acceptance_passed(
        "proof", [metric(barge_in=False)], [{"barge_in": True}], route_proof, 1, [b"\x00\x00"]
    )
    assert not local_loop_demo.capture_acceptance_passed(
        "proof", [metric()], [], route_proof, 1, []
    )
    assert not local_loop_demo.capture_acceptance_passed(
        "proof", [metric()], [], {"ok": False}, 1, [b"\x00\x00"]
    )
    assert not local_loop_demo.capture_acceptance_passed(
        "proof", [metric()], [], {}, 1, [b"\x00\x00"]
    )


def test_playback_generations_at_uses_detection_time_not_drain_time():
    intervals = [
        {"generation": 1, "start": 20.0, "end": 30.0},
        {"generation": 2, "start": 40.0, "end": None},
    ]

    assert local_loop_demo.playback_generations_at(intervals, 10.0) == []
    assert local_loop_demo.playback_generations_at(intervals, 25.0) == [1]
    assert local_loop_demo.playback_generations_at(intervals, 35.0) == []
    assert local_loop_demo.playback_generations_at(intervals, 45.0) == [2]


def test_route_decision_probe_uses_authorization_header_only(monkeypatch):
    data = {
        "voice": {
            "llm": {
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "fast-local",
                "api_key_env": "ANVIL_ROUTER_TOKEN",
                "expected_route_provider": "fast-local",
                "expected_route_model": "qwen36-27b",
                "expected_route_tier": "local",
            }
        }
    }
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "test-token")

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"provider":"fast-local","model":"qwen36-27b","tier":"local"}'

        def getcode(self):
            return 200

    def fake_urlopen(req, timeout):
        headers = {key.lower(): value for key, value in req.header_items()}
        assert timeout == 10
        assert headers["authorization"] == "Bearer test-token"
        assert "x-api-key" not in headers
        return Response()

    monkeypatch.setattr(local_loop_demo.urllib.request, "urlopen", fake_urlopen)

    result = local_loop_demo.route_decision_probe(data)

    assert result["ok"] is True
    assert result["response"]["provider"] == "fast-local"
    assert result["validation_errors"] == []


def test_route_decision_probe_requires_explicit_expected_route(monkeypatch):
    data = {
        "voice": {
            "llm": {
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "chat-fast",
            }
        }
    }

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"provider":"fast-local","model":"qwen36-27b","tier":"local"}'

        def getcode(self):
            return 200

    monkeypatch.setattr(local_loop_demo.urllib.request, "urlopen", lambda _req, timeout: Response())

    result = local_loop_demo.route_decision_probe(data)

    assert result["ok"] is False
    assert any(
        "voice.llm.expected_route_provider is required" in error
        for error in result["validation_errors"]
    )


def test_route_decision_probe_rejects_unexpected_route_shape(monkeypatch):
    data = {
        "voice": {
            "llm": {
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "fast-local",
                "expected_route_provider": "fast-local",
                "expected_route_model": "qwen36-27b",
                "expected_route_tier": "local",
            }
        }
    }

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"provider":"heavy-local","model":"gpt-oss-120b","tier":"local"}'

        def getcode(self):
            return 200

    monkeypatch.setattr(local_loop_demo.urllib.request, "urlopen", lambda _req, timeout: Response())

    result = local_loop_demo.route_decision_probe(data, prompt="hello from transcript")

    assert result["ok"] is False
    assert result["prompt_source"] == "captured transcript"
    assert "expected provider fast-local" in result["validation_errors"][0]
