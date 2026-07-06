#!/usr/bin/env python
"""RUN ON fakoli-dark (or any machine with a real mic/speaker) — NOT YET EXECUTED
(requires ``sounddevice`` + a real audio device + the STT/TTS serves and the
anvil router all running)

Local mic -> VAD -> STT -> anvil-routed LLM -> TTS -> speakers loop with
barge-in (anvil task T010), driven entirely by
:class:`anvil_serving.voice.connections.local_audio.LocalAudioDuplex` and the
REAL out-of-process stages (see ``scripts/voice/_real_pipeline.py``'s
:class:`~scripts.voice._real_pipeline.RealVoicePipeline`, which wires
:class:`~anvil_serving.voice.stages.stt.STTStage`/
:class:`~anvil_serving.voice.stages.tts.TTSStage` instead of the pipeline
module's ``Echo*`` stubs -- see that module's docstring for a flagged
followup on why this isn't just ``anvil_serving.voice.pipeline.VoicePipeline``
directly).

Barge-in: while the assistant's reply is being spoken (``pipeline.vad.responding
is True``), a fresh speech onset detected by
:class:`~scripts.voice._real_pipeline.SimpleEnergyVADModel` bumps the shared
:class:`~anvil_serving.voice.cancel_scope.CancelScope` INSIDE
:meth:`~anvil_serving.voice.stages.vad.VADStage.process` itself (see
``stages/vad.py`` -- this script does not need to implement that half); this
script's only barge-in responsibility is clearing already-buffered mic input
right after a turn ends so stale frames don't bleed into the next one (see
:meth:`~anvil_serving.voice.connections.local_audio.LocalAudioDuplex.clear_pending_input`).

``--capture [PREFIX]`` saves the session's mic input to ``PREFIX.input.wav``,
played assistant audio to ``PREFIX.output.wav``, event evidence to
``PREFIX.events.jsonl``, per-turn TTFA/turn-latency to
``PREFIX.latency.json``, a complete proof bundle to ``PREFIX.session.json``,
and appends one row to ``docs/findings/2026-07-voice-local-loop-proof.md``.
With no explicit prefix, ``--capture`` writes under
the temp-directory ``anvil-voice-captures/local-loop-<timestamp>`` so the
task's acceptance command (``python scripts/voice/local_loop_demo.py --capture``)
is runnable without committing bulky live audio artifacts.

HONESTY NOTE: this script has NEVER been run. :class:`SimpleEnergyVADModel`
is an energy-threshold placeholder, not a real acoustic VAD model (see its
docstring) -- expect false turn boundaries on real audio until a proper
detector is wired in. Nothing here is proven against real hardware; see
CLAUDE.md's "never claim a live capability is proven" rule.

Usage::

    python scripts/voice/local_loop_demo.py --config examples/voice/fakoli-dark.toml \\
        --duration 60 --capture
"""
from __future__ import annotations

import json
import math
import os
import queue
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Bootstrap the repo root onto sys.path so this file works BOTH as a directly
# executed script (`python scripts/voice/local_loop_demo.py`, where Python
# only puts the script's OWN directory -- scripts/voice -- on sys.path) and as
# an imported module (`import scripts.voice.local_loop_demo`, e.g. from
# tests/voice/test_harness_importable.py, where pytest's own
# `pythonpath = ["."]` already covers it -- this insert is then a harmless
# no-op). `scripts/` is deliberately NOT part of the installed wheel (see
# pyproject.toml), so it can't be relied on to already be importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anvil_serving.voice import config as voice_config  # noqa: E402
from anvil_serving.voice.connections.local_audio import (  # noqa: E402
    LocalAudioConfig,
    LocalAudioDuplex,
    LocalAudioUnavailable,
)
from anvil_serving.voice.messages import AudioOut, EndOfResponse, Transcription  # noqa: E402
from anvil_serving.voice.stages.base import PIPELINE_END  # noqa: E402
from anvil_serving.voice.stages.tts import resample_int16  # noqa: E402
from anvil_serving.voice.stages.vad import SpeechEvent, VADConfig  # noqa: E402

from scripts.voice._real_pipeline import (  # noqa: E402
    RealVoicePipeline,
    SimpleEnergyVADModel,
    real_pipeline_config_from_manifest,
)

FINDINGS_DOC = _REPO_ROOT / "docs" / "findings" / "2026-07-voice-local-loop-proof.md"
DEFAULT_LOCAL_LOOP_CONFIG = str(_REPO_ROOT / "examples" / "voice" / "fakoli-dark.toml")
PIPELINE_INPUT_SAMPLE_RATE = 16000
DEFAULT_CAPTURE_DIR = Path(
    os.environ.get(
        "ANVIL_VOICE_CAPTURE_DIR",
        str(Path(tempfile.gettempdir()) / "anvil-voice-captures"),
    )
)
_AUTO_CAPTURE = "__anvil_auto_capture__"


@dataclass
class TurnMetric:
    turn_index: int
    turn_id: str
    generation: int
    ttfa_ms: Optional[float]
    turn_latency_ms: Optional[float]
    transcript: Optional[str]
    barge_in: bool = False
    stale_audio_dropped: int = 0
    output_bytes: int = 0


def build_parser():
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=DEFAULT_LOCAL_LOOP_CONFIG, help="voice manifest TOML")
    p.add_argument("--duration", type=float, default=60.0, help="session duration cap, seconds (Ctrl+C also stops early)")
    p.add_argument("--frame-ms", type=int, default=20, help="mic frame duration, ms (must match VADConfig.frame_ms)")
    p.add_argument("--silence-ms", type=int, default=200, help="end-of-turn silence threshold, ms (150-250 per VADConfig)")
    p.add_argument("--vad-threshold", type=float, default=500.0, help="SimpleEnergyVADModel RMS speech threshold")
    p.add_argument("--input-device", default=None, help="sounddevice input device index/name")
    p.add_argument("--output-device", default=None, help="sounddevice output device index/name")
    p.add_argument("--input-sample-rate", type=int, default=16000, help="PortAudio input sample rate")
    p.add_argument(
        "--shutdown-drain-seconds",
        type=float,
        default=15.0,
        help="seconds to keep audio open while draining in-flight responses during shutdown",
    )
    p.add_argument("--list-devices", action="store_true", help="list PortAudio devices and exit")
    p.add_argument("--meter-inputs", action="store_true", help="measure input-device RMS/peak briefly and exit")
    p.add_argument("--meter-seconds", type=float, default=2.0, help="seconds per input device for --meter-inputs")
    p.add_argument(
        "--capture",
        nargs="?",
        const=_AUTO_CAPTURE,
        default=None,
        help=(
            "optional path prefix for proof artifacts; with no value, writes "
            "under the temp anvil-voice-captures directory"
        ),
    )
    p.add_argument("--min-turns", type=int, default=1, help="minimum completed turns required for exit 0")
    return p


def load_manifest_or_die(path: str):
    try:
        return voice_config.load_manifest(path), None
    except voice_config.ConfigError as exc:
        return None, str(exc)


def configured_auth_env_errors(data: Dict[str, Any]) -> List[str]:
    """Return missing/invalid auth-env problems for endpoints that require auth.

    The live LLM/STT/TTS stages resolve `api_key_env` lazily right before each
    HTTP call. For a hands-on mic proof, waiting until the first spoken turn to
    discover a missing router token wastes the attempt, so the harness checks
    the configured env vars up front without exposing their values.
    """
    voice = data.get("voice", {}) if isinstance(data, dict) else {}
    errors: List[str] = []
    for name in ("llm", "stt", "tts"):
        table = voice.get(name, {}) if isinstance(voice, dict) else {}
        if not isinstance(table, dict) or not table.get("api_key_env"):
            continue
        try:
            token = voice_config.resolve_secret(table, "api_key")
        except voice_config.ConfigError as exc:
            errors.append("voice.%s.%s" % (name, exc))
            continue
        if token is not None and not token.strip():
            errors.append(
                "voice.%s.api_key_env names %s, which is empty in the environment"
                % (name, table["api_key_env"])
            )
    return errors


def append_finding_row(row: str) -> bool:
    """Insert one markdown table row into the local-loop-proof findings doc.

    Best-effort: a missing/unwritable findings doc must not crash a run that
    otherwise completed -- print a warning and move on.
    """
    try:
        row = row.rstrip("\n")
        if not FINDINGS_DOC.exists():
            print("local_loop_demo: findings doc does not exist: %s" % FINDINGS_DOC, file=sys.stderr)
            return False

        lines = FINDINGS_DOC.read_text(encoding="utf-8").splitlines()
        try:
            session_idx = lines.index("## Session log")
        except ValueError:
            print("local_loop_demo: findings doc has no Session log heading", file=sys.stderr)
            return False
        header_idx = next(
            (
                i for i in range(session_idx + 1, len(lines))
                if lines[i].startswith("| timestamp (UTC) |")
            ),
            None,
        )
        if header_idx is None or header_idx + 1 >= len(lines) or not lines[header_idx + 1].startswith("|---"):
            print("local_loop_demo: findings doc has no session-log markdown table", file=sys.stderr)
            return False

        table_end = header_idx + 2
        while table_end < len(lines) and lines[table_end].startswith("|"):
            if lines[table_end].startswith("| _TBD_ |"):
                lines.insert(table_end, row)
                FINDINGS_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True
            table_end += 1
        lines.insert(table_end, row)
        FINDINGS_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        print("local_loop_demo: could not append to %s: %s" % (FINDINGS_DOC, exc), file=sys.stderr)
        return False


def default_capture_prefix() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return str(DEFAULT_CAPTURE_DIR / ("local-loop-%s" % stamp))


def resolve_capture_prefix(value: Optional[str]) -> Optional[str]:
    if value == _AUTO_CAPTURE:
        return default_capture_prefix()
    return value


def resolve_device_arg(value: Optional[str]) -> Optional[Any]:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        return value


def _write_wav(path: str, pcm_frames: List[bytes], sample_rate: int) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"".join(pcm_frames))


def normalize_input_frame(frame: bytes, input_sample_rate: int) -> bytes:
    if input_sample_rate == PIPELINE_INPUT_SAMPLE_RATE:
        return frame
    return resample_int16(frame, input_sample_rate, PIPELINE_INPUT_SAMPLE_RATE)


def pcm_int16_stats(pcm: bytes) -> Dict[str, Any]:
    sample_count = len(pcm) // 2
    if sample_count <= 0:
        return {"samples": 0, "rms": 0, "peak": 0, "nonzero_samples": 0}
    samples = struct.unpack("<%dh" % sample_count, pcm[:sample_count * 2])
    squares = sum(sample * sample for sample in samples)
    return {
        "samples": sample_count,
        "rms": round(math.sqrt(squares / sample_count), 2),
        "peak": max((abs(sample) for sample in samples), default=0),
        "nonzero_samples": sum(1 for sample in samples if sample),
    }


def _input_device_indices(sd: Any) -> List[int]:
    return [
        idx for idx, device in enumerate(sd.query_devices())
        if int(device.get("max_input_channels", 0)) > 0
    ]


def list_audio_devices() -> int:
    sd = LocalAudioDuplex._import_sounddevice()
    print("default devices: %s" % (sd.default.device,))
    for idx, device in enumerate(sd.query_devices()):
        input_channels = int(device.get("max_input_channels", 0))
        output_channels = int(device.get("max_output_channels", 0))
        if input_channels or output_channels:
            print(
                "%3d | in=%d out=%d | default_sr=%s | %s"
                % (
                    idx,
                    input_channels,
                    output_channels,
                    device.get("default_samplerate"),
                    device.get("name"),
                )
            )
    return 0


def meter_input_device(
    device: Any,
    *,
    seconds: float,
    sample_rate: int,
    frame_ms: int,
    threshold: float,
) -> Dict[str, Any]:
    sd = LocalAudioDuplex._import_sounddevice()
    cfg = LocalAudioConfig(sample_rate=sample_rate, frame_ms=frame_ms, input_device=device)
    frames: List[bytes] = []

    def on_input(indata, _frames, _time_info, _status) -> None:
        frames.append(bytes(indata))

    try:
        stream = sd.RawInputStream(
            samplerate=cfg.sample_rate,
            channels=cfg.channels,
            dtype=cfg.dtype,
            blocksize=cfg.frame_samples,
            device=device,
            callback=on_input,
        )
        with stream:
            time.sleep(max(0.0, seconds))
    except Exception as exc:  # noqa: BLE001 - diagnostics should report every failing device
        return {
            "device": device,
            "ok": False,
            "sample_rate": sample_rate,
            "frame_ms": frame_ms,
            "seconds": seconds,
            "error": "%s: %s" % (type(exc).__name__, exc),
        }

    stats = pcm_int16_stats(b"".join(frames))
    stats.update(
        {
            "device": device,
            "ok": True,
            "sample_rate": sample_rate,
            "frame_ms": frame_ms,
            "seconds": seconds,
            "frames": len(frames),
            "above_threshold": stats["rms"] >= threshold or stats["peak"] >= threshold,
            "threshold": threshold,
        }
    )
    return stats


def meter_inputs(
    *,
    seconds: float,
    sample_rate: int,
    frame_ms: int,
    threshold: float,
    input_device: Optional[Any] = None,
) -> int:
    sd = LocalAudioDuplex._import_sounddevice()
    devices = [input_device] if input_device is not None else _input_device_indices(sd)
    for device in devices:
        info = sd.query_devices(device)
        result = meter_input_device(
            device,
            seconds=seconds,
            sample_rate=sample_rate,
            frame_ms=frame_ms,
            threshold=threshold,
        )
        result["name"] = info.get("name")
        print(json.dumps(result, sort_keys=True))
    return 0


def route_decision_probe(data: Dict[str, Any], *, prompt: str = "voice local-loop route proof") -> Dict[str, Any]:
    """Capture a content-light `/v1/route` decision for the manifest's LLM endpoint.

    This is a decision-only corroboration. The actual live turn still flows through
    `voice.llm.base_url` via `LLMStage`; this probe records the same router target,
    model preset, and returned provider/model without dumping secrets.
    """
    llm = data["voice"]["llm"]
    base_url = llm["base_url"].rstrip("/")
    url = base_url + "/route"
    body = {
        "model": llm["model"],
        "messages": [{"role": "user", "content": prompt}],
        "modality": "voice",
    }
    headers = {"Content-Type": "application/json"}
    token = None
    env_name = llm.get("api_key_env")
    if env_name:
        token = os.environ.get(env_name)
        if token:
            headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - manifest-validated private/local URL
            raw = resp.read().decode("utf-8", "replace")
            try:
                parsed: Any = json.loads(raw or "{}")
            except ValueError:
                parsed = {"raw": raw[:1000]}
            validation_errors = route_validation_errors(llm, parsed)
            return {
                "ok": not validation_errors,
                "url": url,
                "status": getattr(resp, "status", resp.getcode()),
                "request_model": llm["model"],
                "auth_env": env_name,
                "prompt_source": "captured transcript" if prompt != "voice local-loop route proof" else "default probe",
                "response": parsed,
                "validation_errors": validation_errors,
            }
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read(1000).decode("utf-8", "replace")
        except Exception:
            body_text = ""
        return {
            "ok": False,
            "url": url,
            "status": exc.code,
            "request_model": llm["model"],
            "auth_env": env_name,
            "prompt_source": "captured transcript" if prompt != "voice local-loop route proof" else "default probe",
            "error": body_text or str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - evidence capture should report, not crash import paths
        return {
            "ok": False,
            "url": url,
            "request_model": llm["model"],
            "auth_env": env_name,
            "prompt_source": "captured transcript" if prompt != "voice local-loop route proof" else "default probe",
            "error": "%s: %s" % (type(exc).__name__, exc),
        }


def route_validation_errors(llm: Dict[str, Any], parsed: Any) -> List[str]:
    if not isinstance(parsed, dict):
        return ["route response is not a JSON object"]
    errors: List[str] = []
    provider = parsed.get("provider")
    served_model = parsed.get("model")
    tier = parsed.get("tier")
    if not provider:
        errors.append("route response missing provider")
    if not served_model:
        errors.append("route response missing model")

    expected_provider = llm.get("expected_route_provider")
    expected_model = llm.get("expected_route_model")
    expected_tier = llm.get("expected_route_tier")
    if not expected_provider:
        errors.append("voice.llm.expected_route_provider is required for capture route proof")
    if not expected_model:
        errors.append("voice.llm.expected_route_model is required for capture route proof")
    if not expected_tier:
        errors.append("voice.llm.expected_route_tier is required for capture route proof")

    if expected_provider and provider != expected_provider:
        errors.append("expected provider %s, got %s" % (expected_provider, provider))
    if expected_model and served_model != expected_model:
        errors.append("expected model %s, got %s" % (expected_model, served_model))
    if expected_tier and tier != expected_tier:
        errors.append("expected tier %s, got %s" % (expected_tier, tier))
    return errors


def route_failure_summary(route_proof: Dict[str, Any]) -> str:
    if not route_proof:
        return "unknown error"
    route_issue = route_proof.get("error")
    if not route_issue:
        route_issue = "; ".join(route_proof.get("validation_errors", []))
    return route_issue or "unknown error"


def write_capture(
    prefix: str,
    input_frames: List[bytes],
    output_frames: List[bytes],
    input_sample_rate: int,
    output_sample_rate: int,
    turns: List[TurnMetric],
    events: List[Dict[str, Any]],
    route_proof: Dict[str, Any],
    manifest_summary: str,
    *,
    append_finding: bool = True,
    finding_status: Optional[Dict[str, bool]] = None,
) -> Dict[str, str]:
    input_wav_path = prefix + ".input.wav"
    output_wav_path = prefix + ".output.wav"
    latency_path = prefix + ".latency.json"
    events_path = prefix + ".events.jsonl"
    session_path = prefix + ".session.json"
    _write_wav(input_wav_path, input_frames, input_sample_rate)
    _write_wav(output_wav_path, output_frames, output_sample_rate)
    Path(events_path).parent.mkdir(parents=True, exist_ok=True)
    with open(events_path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    completed_turns = [asdict(t) for t in turns]
    barge_in_observed = any(t.barge_in for t in turns) or any(e.get("barge_in") for e in events)
    payload: Dict[str, Any] = {
        "turns": completed_turns,
        "turns_completed": len(turns),
        "barge_in_observed": barge_in_observed,
        "route_proof": route_proof,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    Path(latency_path).parent.mkdir(parents=True, exist_ok=True)
    with open(latency_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    session_payload = {
        **payload,
        "manifest": manifest_summary,
        "artifacts": {
            "input_wav": input_wav_path,
            "output_wav": output_wav_path,
            "latency_json": latency_path,
            "events_jsonl": events_path,
            "session_json": session_path,
        },
        "notes": [
            "Recorded mic input and assistant playback are raw PCM WAV containers.",
            "Route proof is a decision-only /v1/route probe; live turns use the same voice.llm endpoint.",
            "Automated proof does not replace human listening quality review.",
        ],
    }
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(session_payload, f, indent=2)

    print(
        "local_loop_demo: wrote %s, %s, %s, %s, and %s"
        % (input_wav_path, output_wav_path, latency_path, events_path, session_path)
    )
    avg_ttfa = [t.ttfa_ms for t in turns if t.ttfa_ms is not None]
    avg_ttfa_str = ("%.1f" % (sum(avg_ttfa) / len(avg_ttfa))) if avg_ttfa else "n/a"
    latencies = [t.turn_latency_ms for t in turns if t.turn_latency_ms is not None]
    avg_latency_str = ("%.1f" % (sum(latencies) / len(latencies))) if latencies else "n/a"
    route_response = route_proof.get("response") if route_proof.get("ok") else None
    route_provider = route_response.get("provider") if isinstance(route_response, dict) else "unproven"
    finding_row_written = False
    if append_finding:
        finding_row_written = append_finding_row(
            "| %s | %d | %s | %s | %s | %s | %s | %s | %s |" % (
                payload["captured_at"],
                len(turns),
                "yes" if barge_in_observed else "no",
                avg_ttfa_str,
                avg_latency_str,
                route_provider,
                input_wav_path,
                output_wav_path,
                session_path,
            )
        )
    if finding_status is not None:
        finding_status["row_written"] = finding_row_written
    return session_payload["artifacts"]


def has_acceptance_turn(turns: List[TurnMetric], *, require_barge_in: bool = False) -> bool:
    return any(
        (not require_barge_in or t.barge_in)
        and
        t.ttfa_ms is not None
        and t.turn_latency_ms is not None
        and t.output_bytes > 0
        for t in turns
    )


def capture_acceptance_passed(
    capture_prefix: Optional[str],
    turns: List[TurnMetric],
    events: List[Dict[str, Any]],
    route_proof: Dict[str, Any],
    min_turns: int,
    output_frames: Optional[List[bytes]],
) -> bool:
    if not capture_prefix:
        return False
    barge_in_observed = any(t.barge_in for t in turns) or any(e.get("barge_in") for e in events)
    if not barge_in_observed:
        return False
    if len(turns) < min_turns:
        return False
    if not route_proof or not route_proof.get("ok"):
        return False
    if not has_acceptance_turn(turns, require_barge_in=True):
        return False
    return bool(output_frames and any(output_frames))


def should_append_successful_finding(acceptance_capture: bool, playback_errors: List[str]) -> bool:
    return bool(acceptance_capture and not playback_errors)


def capture_barge_in_hint(turns: List[TurnMetric], events: List[Dict[str, Any]]) -> str:
    """Explain the most likely operator timing issue for a missing barge-in."""
    if any(t.barge_in for t in turns) or any(e.get("barge_in") for e in events):
        return ""
    early_interrupts = [
        e for e in events
        if e.get("kind") == "vad_started" and e.get("vad_barge_in") and not e.get("barge_in")
    ]
    if early_interrupts:
        return (
            "speech was detected while a response was pending, but before assistant playback "
            "was active; wait until the assistant voice is audible, then speak over it"
        )
    if turns:
        return (
            "assistant audio completed, but no speech onset was detected during playback; "
            "speak over the audible assistant voice and let the interrupted reply finish"
        )
    if any(e.get("kind") == "vad_started" for e in events):
        return (
            "speech reached VAD, but no assistant reply completed; stop speaking long enough "
            "for the assistant to start, then barge in during audible playback"
        )
    return "no speech onset reached VAD; check the microphone route or input device"


def playback_generations_at(playback_intervals: List[Dict[str, Any]], monotonic_s: float) -> List[int]:
    return sorted(
        {
            int(interval["generation"])
            for interval in playback_intervals
            if interval["start"] <= monotonic_s
            and (interval["end"] is None or monotonic_s <= interval["end"])
        }
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_devices:
        try:
            return list_audio_devices()
        except LocalAudioUnavailable as exc:
            print("local_loop_demo: %s" % exc, file=sys.stderr)
            return 2
    if args.meter_inputs:
        try:
            return meter_inputs(
                seconds=args.meter_seconds,
                sample_rate=args.input_sample_rate,
                frame_ms=args.frame_ms,
                threshold=args.vad_threshold,
                input_device=resolve_device_arg(args.input_device),
            )
        except LocalAudioUnavailable as exc:
            print("local_loop_demo: %s" % exc, file=sys.stderr)
            return 2
    capture_prefix = resolve_capture_prefix(args.capture)

    data, err = load_manifest_or_die(args.config)
    if err:
        print("local_loop_demo: %s" % err, file=sys.stderr)
        return 2
    auth_errors = configured_auth_env_errors(data)
    if auth_errors:
        print(
            "local_loop_demo: cannot start live loop; %s" % "; ".join(auth_errors),
            file=sys.stderr,
        )
        return 2
    if capture_prefix:
        startup_route_proof = route_decision_probe(data)
        if not startup_route_proof.get("ok"):
            print(
                "local_loop_demo: route preflight failed before audio: %s"
                % route_failure_summary(startup_route_proof),
                file=sys.stderr,
            )
            return 2

    vad_config = VADConfig(frame_ms=args.frame_ms, silence_ms=args.silence_ms)
    pipeline_config = real_pipeline_config_from_manifest(
        data, vad_config=vad_config, vad_model=SimpleEnergyVADModel(threshold=args.vad_threshold),
    )
    pipeline = RealVoicePipeline(pipeline_config)

    try:
        audio = LocalAudioDuplex(
            LocalAudioConfig(
                sample_rate=16000,
                input_sample_rate=args.input_sample_rate,
                output_sample_rate=pipeline_config.tts.target_sample_rate,
                frame_ms=args.frame_ms,
                input_device=resolve_device_arg(args.input_device),
                output_device=resolve_device_arg(args.output_device),
            )
        )
    except LocalAudioUnavailable as exc:
        print("local_loop_demo: %s" % exc, file=sys.stderr)
        return 2

    input_frames: Optional[List[bytes]] = [] if capture_prefix else None
    output_frames: Optional[List[bytes]] = [] if capture_prefix else None
    events: List[Dict[str, Any]] = []
    playback_errors: List[str] = []
    turn_state: Dict[str, Dict[str, Any]] = {}
    turn_metrics: List[TurnMetric] = []
    stop_event = threading.Event()
    route_proof = {}
    active_playback_lock = threading.Lock()
    active_playback_generations: Set[int] = set()
    playback_intervals: List[Dict[str, Any]] = []
    def _start_playback_interval(generation: int) -> Dict[str, Any]:
        interval = {"generation": generation, "start": time.perf_counter(), "end": None}
        with active_playback_lock:
            active_playback_generations.add(generation)
            playback_intervals.append(interval)
        return interval

    def _finish_playback_interval(interval: Dict[str, Any]) -> None:
        with active_playback_lock:
            interval["end"] = time.perf_counter()
            active_playback_generations.discard(int(interval["generation"]))

    def _playback_generations_at(monotonic_s: float) -> List[int]:
        with active_playback_lock:
            return playback_generations_at(playback_intervals, monotonic_s)

    def _event(kind: str, **fields: Any) -> None:
        if not capture_prefix:
            return
        event = {
            "kind": kind,
            "monotonic_ms": round(time.perf_counter() * 1000, 2),
            "wall_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        event.update(fields)
        events.append(event)

    def _state_for(turn_id: str) -> Dict[str, Any]:
        return turn_state.setdefault(turn_id, {})

    def drain_sidebands() -> None:
        while True:
            try:
                item = pipeline.vad_events.get_nowait()
            except queue.Empty:
                break
            if not isinstance(item, SpeechEvent):
                continue
            state = _state_for(item.turn_id)
            now = time.perf_counter()
            if item.kind == "started":
                detected_at = item.detected_monotonic_s or now
                interrupted_generations = _playback_generations_at(detected_at)
                barge_in = bool(item.barge_in and interrupted_generations)
                state["speech_started_at"] = now
                state["speech_detected_at"] = detected_at
                state["barge_in"] = barge_in
                state["vad_barge_in"] = bool(item.barge_in)
                state["interrupted_playback_generations"] = interrupted_generations
                if barge_in:
                    _event(
                        "barge_in",
                        turn_id=item.turn_id,
                        generation=item.generation,
                        audio_ms=item.audio_ms,
                        detected_monotonic_s=detected_at,
                        interrupted_generations=interrupted_generations,
                    )
                    print(
                        "local_loop_demo: playback barge-in observed; wait for the new reply to finish",
                        flush=True,
                    )
                elif item.barge_in and capture_prefix:
                    print(
                        "local_loop_demo: speech detected before playback was active; "
                        "wait for audible assistant audio, then speak over it",
                        flush=True,
                    )
            elif item.kind == "stopped":
                state["speech_stopped_at"] = now
            _event(
                "vad_%s" % item.kind,
                turn_id=item.turn_id,
                generation=item.generation,
                audio_ms=item.audio_ms,
                detected_monotonic_s=item.detected_monotonic_s,
                vad_barge_in=bool(item.barge_in),
                barge_in=bool(state.get("barge_in")),
                interrupted_playback_generations=state.get("interrupted_playback_generations", []),
            )

        while True:
            try:
                item = pipeline.transcript_events.get_nowait()
            except queue.Empty:
                break
            if not isinstance(item, Transcription):
                continue
            state = _state_for(item.turn_id)
            now = time.perf_counter()
            state["last_transcript_at"] = now
            state["transcript"] = item.text
            if item.is_final:
                state["final_transcript_at"] = now
                state["final_transcript"] = item.text
            _event(
                "transcription",
                turn_id=item.turn_id,
                generation=item.generation,
                is_final=item.is_final,
                text=item.text,
            )

    def playback_loop() -> None:
        while not stop_event.is_set():
            drain_sidebands()
            try:
                item = pipeline.audio_out.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is PIPELINE_END:
                break
            if isinstance(item, AudioOut):
                state = _state_for(item.turn_id)
                if pipeline.cancel_scope.is_stale(item.generation):
                    state["stale_audio_dropped"] = int(state.get("stale_audio_dropped", 0)) + 1
                    state["stale_audio_bytes"] = int(state.get("stale_audio_bytes", 0)) + len(item.pcm)
                    _event(
                        "audio_dropped_stale",
                        turn_id=item.turn_id,
                        generation=item.generation,
                        bytes=len(item.pcm),
                    )
                    continue
                now = time.perf_counter()
                if "first_audio_at" not in state:
                    state["first_audio_at"] = now
                    response_start = state.get("speech_stopped_at") or state.get("final_transcript_at")
                    if response_start is not None:
                        state["ttfa_ms"] = round((now - response_start) * 1000, 2)
                    _event(
                        "first_audio",
                        turn_id=item.turn_id,
                        generation=item.generation,
                        ttfa_ms=state.get("ttfa_ms"),
                    )
                    if capture_prefix and not (
                        any(t.barge_in for t in turn_metrics) or any(e.get("barge_in") for e in events)
                    ):
                        print(
                            "local_loop_demo: assistant audio started; speak over it now for barge-in proof",
                            flush=True,
                        )
                state["output_bytes"] = int(state.get("output_bytes", 0)) + len(item.pcm)
                interval = _start_playback_interval(item.generation)
                try:
                    try:
                        audio.play(item.pcm)
                    except Exception as exc:  # noqa: BLE001 - live audio failures must be captured, not thread-fatal
                        error = "%s: %s" % (type(exc).__name__, exc)
                        playback_errors.append(error)
                        _event(
                            "playback_error",
                            turn_id=item.turn_id,
                            generation=item.generation,
                            bytes=len(item.pcm),
                            error=error,
                        )
                        print(
                            "local_loop_demo: playback failed: %s" % error,
                            file=sys.stderr,
                            flush=True,
                        )
                        stop_event.set()
                        break
                finally:
                    _finish_playback_interval(interval)
                if output_frames is not None:
                    output_frames.append(item.pcm)
            elif isinstance(item, EndOfResponse):
                state = _state_for(item.turn_id)
                if pipeline.cancel_scope.is_stale(item.generation):
                    _event(
                        "end_response_stale",
                        turn_id=item.turn_id,
                        generation=item.generation,
                    )
                    continue
                now = time.perf_counter()
                response_start = state.get("speech_stopped_at") or state.get("final_transcript_at")
                latency = round((now - response_start) * 1000, 2) if response_start is not None else None
                turn_metrics.append(
                    TurnMetric(
                        turn_index=len(turn_metrics),
                        turn_id=item.turn_id,
                        generation=item.generation,
                        ttfa_ms=state.get("ttfa_ms"),
                        turn_latency_ms=latency,
                        transcript=state.get("final_transcript") or state.get("transcript"),
                        barge_in=bool(state.get("barge_in")),
                        stale_audio_dropped=int(state.get("stale_audio_dropped", 0)),
                        output_bytes=int(state.get("output_bytes", 0)),
                    )
                )
                if capture_prefix:
                    completed = turn_metrics[-1]
                    print(
                        "local_loop_demo: turn %d completed (barge-in=%s, output_bytes=%d)"
                        % (
                            len(turn_metrics),
                            "yes" if completed.barge_in else "no",
                            completed.output_bytes,
                        ),
                        flush=True,
                    )
                _event(
                    "end_response",
                    turn_id=item.turn_id,
                    generation=item.generation,
                    turn_latency_ms=latency,
                )
                # The response has fully drained: clear this flag so the NEXT
                # speech onset starts a clean new turn instead of being read
                # as a barge-in (mirrors VADStage's own comment on `responding`).
                pipeline.vad.responding = False
                pipeline.cancel_scope.mark_settled()
                # Also drop any mic frames that queued up while we were still
                # speaking (echo/self-hearing risk on an open mic setup without
                # echo cancellation -- a real deployment wants AEC upstream of
                # this; flagged, not solved, here).
                dropped_input = audio.clear_pending_input()
                if dropped_input:
                    _event(
                        "pending_input_cleared",
                        turn_id=item.turn_id,
                        generation=item.generation,
                        frames=dropped_input,
                    )

    playback_thread = threading.Thread(target=playback_loop, daemon=True, name="local-loop-playback")
    shutdown_done = False

    def shutdown_pipeline_and_playback() -> None:
        nonlocal shutdown_done
        if shutdown_done:
            return
        drain_timeout = max(0.0, args.shutdown_drain_seconds)
        try:
            pipeline.shutdown_gracefully(join_timeout=drain_timeout)
        except KeyboardInterrupt:
            print("\nlocal_loop_demo: interrupted during shutdown -- finishing cleanup")
        finally:
            drain_sidebands()
            try:
                if playback_thread.is_alive():
                    playback_thread.join(timeout=drain_timeout)
            except KeyboardInterrupt:
                print("\nlocal_loop_demo: interrupted during playback drain -- stopping playback")
            finally:
                stop_event.set()
                try:
                    if playback_thread.is_alive():
                        playback_thread.join(timeout=0.5)
                except KeyboardInterrupt:
                    pass
                shutdown_done = True

    pipeline.start()
    print(
        "local_loop_demo: manifest OK -- %s\nlocal_loop_demo: speak into the mic "
        "(Ctrl+C to stop early; duration cap %.0fs)" % (voice_config.describe(data), args.duration)
    )
    if capture_prefix:
        print(
            "local_loop_demo: capture mode -- speak once, wait for audible assistant audio, "
            "then speak over it; wait for the interrupted reply to finish before Ctrl+C",
            flush=True,
        )
    t_end = time.time() + args.duration
    try:
        with audio:
            playback_thread.start()
            try:
                while time.time() < t_end and not stop_event.is_set():
                    frame = audio.read_frame(timeout=0.5)
                    if frame is None:
                        drain_sidebands()
                        continue
                    frame = normalize_input_frame(frame, args.input_sample_rate)
                    if input_frames is not None:
                        input_frames.append(frame)
                    pipeline.audio_in.put(frame)
                    drain_sidebands()
            except KeyboardInterrupt:
                print("\nlocal_loop_demo: interrupted -- shutting down")
            finally:
                # Keep PortAudio open while the pipeline and playback thread
                # drain. Closing the audio context first can stop the output
                # stream while a final TTS chunk is still being written.
                shutdown_pipeline_and_playback()
    except KeyboardInterrupt:
        print("\nlocal_loop_demo: interrupted -- shutting down")
    finally:
        shutdown_pipeline_and_playback()

    print("local_loop_demo: %d turn(s) completed" % len(turn_metrics))
    if capture_prefix:
        route_prompt = next((t.transcript for t in turn_metrics if t.transcript), None)
        route_proof = route_decision_probe(data, prompt=route_prompt or "voice local-loop route proof")
    barge_in_observed = any(t.barge_in for t in turn_metrics) or any(e.get("barge_in") for e in events)
    acceptance_capture = should_append_successful_finding(
        capture_acceptance_passed(
            capture_prefix,
            turn_metrics,
            events,
            route_proof,
            args.min_turns,
            output_frames,
        ),
        playback_errors,
    )
    if capture_prefix and input_frames is not None and output_frames is not None:
        finding_status = {"row_written": False}
        write_capture(
            capture_prefix,
            input_frames,
            output_frames,
            PIPELINE_INPUT_SAMPLE_RATE,
            pipeline_config.tts.target_sample_rate,
            turn_metrics,
            events,
            route_proof,
            voice_config.describe(data),
            append_finding=acceptance_capture,
            finding_status=finding_status,
        )
        if acceptance_capture and not finding_status["row_written"]:
            print(
                "local_loop_demo: capture succeeded but could not append the findings row",
                file=sys.stderr,
            )
            return 1
    if playback_errors:
        print(
            "local_loop_demo: playback failed during capture: %s" % "; ".join(playback_errors),
            file=sys.stderr,
        )
        return 1
    if capture_prefix and not barge_in_observed:
        hint = capture_barge_in_hint(turn_metrics, events)
        print(
            "local_loop_demo: capture requires a successful barge-in; none was observed"
            + (". Hint: %s" % hint if hint else ""),
            file=sys.stderr,
        )
        return 1
    if len(turn_metrics) < args.min_turns:
        print(
            "local_loop_demo: expected at least %d completed turn(s), got %d"
            % (args.min_turns, len(turn_metrics)),
            file=sys.stderr,
        )
        return 1
    if capture_prefix and not has_acceptance_turn(turn_metrics, require_barge_in=True):
        if barge_in_observed:
            print(
                "local_loop_demo: capture observed playback barge-in, but no interrupted "
                "reply completed with TTFA, turn latency, and assistant output audio; "
                "after barge-in, stop speaking and let the new reply finish",
                file=sys.stderr,
            )
            return 1
        print(
            "local_loop_demo: capture requires at least one playback-interrupting "
            "barge-in turn with TTFA, turn latency, and assistant output audio",
            file=sys.stderr,
        )
        return 1
    if capture_prefix and not (output_frames and any(output_frames)):
        print(
            "local_loop_demo: capture requires a non-empty assistant output recording",
            file=sys.stderr,
        )
        return 1
    if capture_prefix and (not route_proof or not route_proof.get("ok")):
        print(
            "local_loop_demo: route proof failed: %s" % route_failure_summary(route_proof),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
