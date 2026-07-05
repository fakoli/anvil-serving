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

``--capture PREFIX`` saves the full session's played-back assistant audio to
``PREFIX.wav``, per-turn TTFA/turn-latency to ``PREFIX.latency.json``, and
appends one row to ``docs/findings/2026-07-voice-local-loop-proof.md``.

HONESTY NOTE: this script has NEVER been run. :class:`SimpleEnergyVADModel`
is an energy-threshold placeholder, not a real acoustic VAD model (see its
docstring) -- expect false turn boundaries on real audio until a proper
detector is wired in. Nothing here is proven against real hardware; see
CLAUDE.md's "never claim a live capability is proven" rule.

Usage::

    python scripts/voice/local_loop_demo.py --config examples/voice/voice.example.toml \\
        --duration 60 --capture /tmp/local-loop-run1
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from anvil_serving.voice.messages import AudioOut, EndOfResponse  # noqa: E402
from anvil_serving.voice.stages.base import PIPELINE_END  # noqa: E402
from anvil_serving.voice.stages.vad import VADConfig  # noqa: E402

from scripts.voice._real_pipeline import (  # noqa: E402
    RealVoicePipeline,
    SimpleEnergyVADModel,
    real_pipeline_config_from_manifest,
)

FINDINGS_DOC = _REPO_ROOT / "docs" / "findings" / "2026-07-voice-local-loop-proof.md"


@dataclass
class TurnMetric:
    turn_index: int
    ttfa_ms: Optional[float]
    turn_latency_ms: float


def build_parser():
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=voice_config.DEFAULT_CONFIG, help="voice manifest TOML")
    p.add_argument("--duration", type=float, default=60.0, help="session duration cap, seconds (Ctrl+C also stops early)")
    p.add_argument("--frame-ms", type=int, default=20, help="mic frame duration, ms (must match VADConfig.frame_ms)")
    p.add_argument("--silence-ms", type=int, default=200, help="end-of-turn silence threshold, ms (150-250 per VADConfig)")
    p.add_argument("--vad-threshold", type=float, default=500.0, help="SimpleEnergyVADModel RMS speech threshold")
    p.add_argument("--input-device", default=None, help="sounddevice input device index/name")
    p.add_argument("--output-device", default=None, help="sounddevice output device index/name")
    p.add_argument("--capture", default=None, help="path prefix to save a WAV + latency JSON + append a findings row")
    return p


def load_manifest_or_die(path: str):
    try:
        return voice_config.load_manifest(path), None
    except voice_config.ConfigError as exc:
        return None, str(exc)


def append_finding_row(row: str) -> None:
    """Append one markdown table row to the local-loop-proof findings doc.

    Best-effort: a missing/unwritable findings doc must not crash a run that
    otherwise completed -- print a warning and move on.
    """
    try:
        with open(FINDINGS_DOC, "a", encoding="utf-8") as f:
            f.write(row.rstrip("\n") + "\n")
    except OSError as exc:
        print("local_loop_demo: could not append to %s: %s" % (FINDINGS_DOC, exc), file=sys.stderr)


def write_capture(prefix: str, pcm_frames: List[bytes], sample_rate: int, turns: List[TurnMetric]) -> None:
    wav_path = prefix + ".wav"
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"".join(pcm_frames))

    latency_path = prefix + ".latency.json"
    payload: Dict[str, Any] = {
        "turns": [asdict(t) for t in turns],
        "turns_completed": len(turns),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(latency_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print("local_loop_demo: wrote %s and %s" % (wav_path, latency_path))
    avg_ttfa = [t.ttfa_ms for t in turns if t.ttfa_ms is not None]
    avg_ttfa_str = ("%.1f" % (sum(avg_ttfa) / len(avg_ttfa))) if avg_ttfa else "n/a"
    append_finding_row(
        "| %s | %d | %s | %s | %s |" % (
            payload["captured_at"], len(turns), avg_ttfa_str, wav_path, latency_path,
        )
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    data, err = load_manifest_or_die(args.config)
    if err:
        print("local_loop_demo: %s" % err, file=sys.stderr)
        return 2

    vad_config = VADConfig(frame_ms=args.frame_ms, silence_ms=args.silence_ms)
    pipeline_config = real_pipeline_config_from_manifest(
        data, vad_config=vad_config, vad_model=SimpleEnergyVADModel(threshold=args.vad_threshold),
    )
    pipeline = RealVoicePipeline(pipeline_config)

    try:
        audio = LocalAudioDuplex(
            LocalAudioConfig(
                sample_rate=16000, frame_ms=args.frame_ms,
                input_device=args.input_device, output_device=args.output_device,
            )
        )
    except LocalAudioUnavailable as exc:
        print("local_loop_demo: %s" % exc, file=sys.stderr)
        return 2

    capture_frames: Optional[List[bytes]] = [] if args.capture else None
    turn_metrics: List[TurnMetric] = []
    stop_event = threading.Event()

    def playback_loop() -> None:
        turn_start = time.perf_counter()
        first_audio: Optional[float] = None
        turn_index = 0
        while not stop_event.is_set():
            try:
                item = pipeline.audio_out.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is PIPELINE_END:
                break
            if isinstance(item, AudioOut):
                if first_audio is None:
                    first_audio = time.perf_counter()
                audio.play(item.pcm)
                if capture_frames is not None:
                    capture_frames.append(item.pcm)
            elif isinstance(item, EndOfResponse):
                now = time.perf_counter()
                turn_metrics.append(
                    TurnMetric(
                        turn_index=turn_index,
                        ttfa_ms=round((first_audio - turn_start) * 1000, 2) if first_audio else None,
                        turn_latency_ms=round((now - turn_start) * 1000, 2),
                    )
                )
                turn_index += 1
                turn_start = now
                first_audio = None
                # The response has fully drained: clear this flag so the NEXT
                # speech onset starts a clean new turn instead of being read
                # as a barge-in (mirrors VADStage's own comment on `responding`).
                pipeline.vad.responding = False
                # Also drop any mic frames that queued up while we were still
                # speaking (echo/self-hearing risk on an open mic setup without
                # echo cancellation -- a real deployment wants AEC upstream of
                # this; flagged, not solved, here).
                audio.clear_pending_input()

    playback_thread = threading.Thread(target=playback_loop, daemon=True, name="local-loop-playback")

    pipeline.start()
    print(
        "local_loop_demo: manifest OK -- %s\nlocal_loop_demo: speak into the mic "
        "(Ctrl+C to stop early; duration cap %.0fs)" % (voice_config.describe(data), args.duration)
    )
    t_end = time.time() + args.duration
    try:
        with audio:
            playback_thread.start()
            while time.time() < t_end:
                frame = audio.read_frame(timeout=0.5)
                if frame is None:
                    continue
                pipeline.audio_in.put(frame)
    except KeyboardInterrupt:
        print("\nlocal_loop_demo: interrupted -- shutting down")
    finally:
        stop_event.set()
        pipeline.shutdown_gracefully()
        playback_thread.join(timeout=2.0)

    print("local_loop_demo: %d turn(s) completed" % len(turn_metrics))
    if args.capture and capture_frames is not None:
        write_capture(args.capture, capture_frames, pipeline_config.tts.target_sample_rate, turn_metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
