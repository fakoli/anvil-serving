#!/usr/bin/env python
"""RUN ON fakoli-dark -- official OpenAI SDK client proof for anvil Realtime.

Connects the official ``openai`` Python SDK's Realtime WebSocket client to
anvil's package-owned Realtime server wiring (``anvil-serving voice proxy run`` uses
the same ``anvil_serving.voice.realtime.app`` builder). This is anvil task
T014's live proof harness: SDK connects, sends audio input, prints transcript
events, interrupts the first assistant response with ``response.cancel``, then
captures a completed reply after the interruption.

``--capture [PREFIX]`` writes:

* ``PREFIX.events.jsonl`` -- client and server event log
* ``PREFIX.input.wav`` -- audio sent through ``input_audio_buffer.append``
* ``PREFIX.output.wav`` -- assistant audio received as
  ``response.output_audio.delta``
* ``PREFIX.latency.json`` -- per-response TTFA/latency
* ``PREFIX.session.json`` -- proof summary and artifact paths

With no explicit prefix, ``--capture`` writes under the temp directory
``anvil-voice-captures/realtime-sdk-<timestamp>`` so the task's acceptance
command, ``python scripts/voice/realtime_sdk_client_demo.py --capture``, is
runnable without committing bulky live audio artifacts.

The default capture path synthesizes two user utterances through the configured
TTS serve and feeds that PCM into the SDK as audio input. That keeps the proof
automated while still exercising the audio input, STT transcript, LLM, TTS,
Realtime event, and SDK WebSocket paths end to end.

Guarded import: ``openai`` is imported only inside :func:`run_session`, so this
module and ``--help`` stay importable without the optional dependency. The
repo's ``voice`` extra uses ``openai[realtime]`` because the SDK's WebSocket
transport lives behind that extra in current releases.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anvil_serving.voice import config as voice_config  # noqa: E402
from anvil_serving.voice.pipeline import stage_config_from_table  # noqa: E402
from anvil_serving.voice.realtime.app import (  # noqa: E402
    build_realtime_server_from_manifest,
)
from anvil_serving.voice.realtime.ws import serve_forever_in_background  # noqa: E402
from anvil_serving.voice.stages.tts import (  # noqa: E402
    TTSStageConfig,
    resample_int16,
    stream_speech,
)

DEFAULT_REALTIME_CONFIG = str(_REPO_ROOT / "examples" / "voice" / "fakoli-dark.toml")
if not os.path.isfile(DEFAULT_REALTIME_CONFIG):
    DEFAULT_REALTIME_CONFIG = voice_config.DEFAULT_CONFIG

FINDINGS_DOC = _REPO_ROOT / "docs" / "findings" / "2026-07-voice-realtime-proof.md"
DEFAULT_CAPTURE_DIR = Path(
    os.environ.get(
        "ANVIL_VOICE_CAPTURE_DIR",
        str(Path(tempfile.gettempdir()) / "anvil-voice-captures"),
    )
)
_AUTO_CAPTURE = "__anvil_auto_capture__"

DEFAULT_POOL_SIZE = 2
INPUT_SAMPLE_RATE = 16000
DEFAULT_FIRST_UTTERANCE = "Please count slowly from one to twenty so I can interrupt you."
DEFAULT_BARGE_IN_UTTERANCE = (
    "Interrupting you now. Please answer briefly: how many countries are in Africa?"
)


def default_capture_prefix() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return str(DEFAULT_CAPTURE_DIR / ("realtime-sdk-%s" % stamp))


def resolve_capture_prefix(value: Optional[str]) -> Optional[str]:
    if value == _AUTO_CAPTURE:
        return default_capture_prefix()
    return value


def _write_wav(path: str, pcm: bytes, sample_rate: int) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def _read_wav_pcm16_mono(path: str) -> Tuple[bytes, int]:
    with wave.open(path, "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        sample_rate = w.getframerate()
        if channels != 1 or width != 2:
            raise ValueError("%s must be mono signed-16-bit PCM WAV" % path)
        return w.readframes(w.getnframes()), sample_rate


def _capture_event(events: List[Dict[str, Any]], direction: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    item = dict(payload)
    item["direction"] = direction
    item["captured_monotonic_s"] = round(time.monotonic(), 6)
    item["captured_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    events.append(item)
    return item


def _event_to_dict(event: Any) -> Dict[str, Any]:
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    if hasattr(event, "to_dict"):
        return event.to_dict()
    if isinstance(event, dict):
        return dict(event)
    return dict(event)


def _event_type(raw: Mapping[str, Any]) -> str:
    event_type = raw.get("type")
    return event_type if isinstance(event_type, str) else ""


def _response_id(raw: Mapping[str, Any]) -> Optional[str]:
    response = raw.get("response")
    if isinstance(response, Mapping):
        rid = response.get("id")
        return rid if isinstance(rid, str) else None
    rid = raw.get("response_id")
    return rid if isinstance(rid, str) else None


def _response_status(raw: Mapping[str, Any]) -> Optional[str]:
    response = raw.get("response")
    if isinstance(response, Mapping):
        status = response.get("status")
        return status if isinstance(status, str) else None
    return None


def _sdk_websocket_base_url(ws_url: str) -> str:
    """Return the SDK base URL that makes ``connect`` target ``/v1/realtime``.

    The current OpenAI Python SDK appends ``/realtime`` to
    ``websocket_base_url``. For anvil's server path, the base must therefore
    end at ``/v1``.
    """
    url = ws_url.rstrip("/")
    if url.endswith("/v1/realtime"):
        return url[: -len("/realtime")]
    if url.endswith("/v1"):
        return url
    return url + "/v1"


def build_server(
    *,
    manifest: Mapping[str, Any],
    host: str,
    port: int,
    pool_size: int,
) -> "tuple[Any, Any, Any]":
    voice = dict(manifest.get("voice", {}))
    voice["realtime_host"] = host
    voice["realtime_port"] = port
    voice["pool_size"] = pool_size
    server, pool = build_realtime_server_from_manifest(
        manifest,
        voice,
        pool_size=pool_size,
        session_id_prefix="sdk-demo",
        sender_thread_name_prefix="sdk-demo-sender",
    )
    thread = serve_forever_in_background(server)
    return server, thread, pool


def _realtime_api_key(data: Optional[Mapping[str, Any]]) -> str:
    if data:
        voice = data.get("voice", {})
        if isinstance(voice, Mapping) and voice.get("realtime_token_env"):
            env_name = str(voice["realtime_token_env"])
            token = os.environ.get(env_name)
            if token:
                return token
    return "anvil-local-demo-not-a-real-key"


def _synthesize_input_pcm(text: str, data: Mapping[str, Any]) -> bytes:
    voice = data["voice"]
    config = stage_config_from_table(voice["tts"], TTSStageConfig)
    pcm = b"".join(stream_speech(text, config))
    if not pcm:
        raise RuntimeError("TTS serve returned no audio for synthesized input")
    return resample_int16(pcm, config.source_sample_rate, INPUT_SAMPLE_RATE)


def _build_turns(
    *,
    data: Optional[Mapping[str, Any]],
    text: Optional[str],
    sample_path: Optional[str],
    capture: Optional[str],
    wants_barge_in: bool,
) -> List[Dict[str, Any]]:
    if sample_path:
        pcm, sample_rate = _read_wav_pcm16_mono(sample_path)
        if sample_rate != INPUT_SAMPLE_RATE:
            pcm = resample_int16(pcm, sample_rate, INPUT_SAMPLE_RATE)
        turns = [{"kind": "audio", "label": sample_path, "pcm": pcm}]
    elif text:
        turns = [{"kind": "text", "label": text, "text": text}]
    elif capture:
        if data is None:
            raise ValueError("--capture without --text/--sample requires a manifest for TTS synthesis")
        turns = [
            {
                "kind": "audio",
                "label": DEFAULT_FIRST_UTTERANCE,
                "pcm": _synthesize_input_pcm(DEFAULT_FIRST_UTTERANCE, data),
            }
        ]
    else:
        raise ValueError("pass --text, --sample, or --capture to drive a turn")

    if wants_barge_in:
        if data is not None:
            turns.append(
                {
                    "kind": "audio",
                    "label": DEFAULT_BARGE_IN_UTTERANCE,
                    "pcm": _synthesize_input_pcm(DEFAULT_BARGE_IN_UTTERANCE, data),
                }
            )
        else:
            turns.append(
                {
                    "kind": "text",
                    "label": DEFAULT_BARGE_IN_UTTERANCE,
                    "text": DEFAULT_BARGE_IN_UTTERANCE,
                }
            )
    return turns


async def _send_client_event(connection: Any, events: List[Dict[str, Any]], payload: Dict[str, Any]) -> None:
    _capture_event(events, "client", payload)
    print("-> %s" % payload.get("type"), flush=True)
    await connection.send(payload)


async def _send_turn(connection: Any, events: List[Dict[str, Any]], turn: Mapping[str, Any]) -> bytes:
    if turn["kind"] == "text":
        await _send_client_event(
            connection,
            events,
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": turn["text"]}],
                },
            },
        )
        await _send_client_event(connection, events, {"type": "response.create"})
        return b""

    pcm = bytes(turn["pcm"])
    await _send_client_event(
        connection,
        events,
        {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm).decode("ascii"),
        },
    )
    await _send_client_event(connection, events, {"type": "input_audio_buffer.commit"})
    return pcm


def _summarize_responses(responses: Mapping[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for response_id, item in responses.items():
        created_at = item.get("created_at")
        first_audio_at = item.get("first_audio_at")
        done_at = item.get("done_at")
        out.append(
            {
                "response_id": response_id,
                "status": item.get("status"),
                "ttfa_ms": (
                    round((first_audio_at - created_at) * 1000, 2)
                    if created_at is not None and first_audio_at is not None
                    else None
                ),
                "latency_ms": (
                    round((done_at - created_at) * 1000, 2)
                    if created_at is not None and done_at is not None
                    else None
                ),
                "audio_bytes": item.get("audio_bytes", 0),
                "assistant_transcript": item.get("assistant_transcript"),
                "assistant_transcript_proven": bool(item.get("assistant_transcript_proven")),
            }
        )
    return out


def _validate_capture(summary: Mapping[str, Any]) -> List[str]:
    errors = []
    if not summary.get("connected"):
        errors.append("no session.created/session.updated event observed")
    if not summary.get("input_audio_bytes"):
        errors.append("no input audio was sent")
    if not summary.get("output_audio_bytes"):
        errors.append("no assistant output audio was received")
    if not summary.get("transcripts"):
        errors.append("no live input transcript event was observed")
    if not summary.get("assistant_transcript_proven"):
        errors.append("no correlated terminal assistant transcript was proven")
    errors.extend(str(error) for error in summary.get("assistant_transcript_errors", []))
    if not summary.get("barge_in_sent"):
        errors.append("response.cancel was not sent")
    if not summary.get("cancelled_response_seen"):
        errors.append("no cancelled response.done was observed")
    if summary.get("output_after_cancel_request_events"):
        errors.append(
            "%d response output event(s) arrived after response.cancel was sent"
            % int(summary.get("output_after_cancel_request_events", 0))
        )
    if summary.get("output_after_cancel_events"):
        errors.append(
            "%d response output event(s) arrived after cancellation"
            % int(summary.get("output_after_cancel_events", 0))
        )
    if not summary.get("completed_after_barge_in"):
        errors.append("no completed response after the barge-in was observed")
    return errors


def _markdown_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def append_finding_row(summary: Mapping[str, Any], paths: Mapping[str, str]) -> bool:
    try:
        if not FINDINGS_DOC.exists():
            print("realtime_sdk_client_demo: findings doc missing: %s" % FINDINGS_DOC, file=sys.stderr)
            return False
        transcripts = "; ".join(summary.get("transcripts", []))
        responses = summary.get("responses", [])
        completed = next((r for r in responses if r.get("status") == "completed"), None)
        ttfa = completed.get("ttfa_ms") if completed else None
        latency = completed.get("latency_ms") if completed else None
        row = (
            "| %s | audio/audio | yes | %s | %d | %d | %s / %s | %s |"
            % (
                _markdown_escape(summary.get("timestamp_utc")),
                _markdown_escape(transcripts or "(no transcript)"),
                int(summary.get("event_count", 0)),
                int(summary.get("output_audio_bytes", 0)),
                _markdown_escape(ttfa),
                _markdown_escape(latency),
                _markdown_escape(paths.get("session_json")),
            )
        )

        lines = FINDINGS_DOC.read_text(encoding="utf-8").splitlines()
        header_idx = next(
            (i for i, line in enumerate(lines) if line.startswith("| timestamp (UTC) |")),
            None,
        )
        if header_idx is None or header_idx + 1 >= len(lines) or not lines[header_idx + 1].startswith("|---"):
            print("realtime_sdk_client_demo: findings doc has no session-log table", file=sys.stderr)
            return False
        insert_at = header_idx + 2
        while insert_at < len(lines) and lines[insert_at].startswith("|"):
            if lines[insert_at].startswith("| _TBD_ |"):
                break
            insert_at += 1
        lines.insert(insert_at, row)
        FINDINGS_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        print("realtime_sdk_client_demo: could not update findings doc: %s" % exc, file=sys.stderr)
        return False


def _save_capture(
    prefix: str,
    events: List[Dict[str, Any]],
    input_audio: bytes,
    output_audio: bytes,
    summary: Dict[str, Any],
    *,
    append_finding: bool,
) -> Dict[str, str]:
    prefix_path = Path(prefix)
    prefix_path.parent.mkdir(parents=True, exist_ok=True)

    events_path = str(prefix_path) + ".events.jsonl"
    with open(events_path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    input_wav = str(prefix_path) + ".input.wav"
    output_wav = str(prefix_path) + ".output.wav"
    latency_json = str(prefix_path) + ".latency.json"
    session_json = str(prefix_path) + ".session.json"

    if input_audio:
        _write_wav(input_wav, input_audio, INPUT_SAMPLE_RATE)
    if output_audio:
        _write_wav(output_wav, output_audio, INPUT_SAMPLE_RATE)
    with open(latency_json, "w", encoding="utf-8") as f:
        json.dump(summary.get("responses", []), f, indent=2, sort_keys=True)

    paths = {
        "events_jsonl": events_path,
        "input_wav": input_wav if input_audio else "",
        "output_wav": output_wav if output_audio else "",
        "latency_json": latency_json,
        "session_json": session_json,
    }
    summary["artifacts"] = paths
    with open(session_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)

    if append_finding and not append_finding_row(summary, paths):
        summary["finding_row_written"] = False
    elif append_finding:
        summary["finding_row_written"] = True
        with open(session_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)

    written = [events_path, latency_json, session_json]
    if input_audio:
        written.append(input_wav)
    if output_audio:
        written.append(output_wav)
    print("realtime_sdk_client_demo: wrote %s" % ", ".join(written))
    return paths


def run_session(
    *,
    ws_url: str,
    text: Optional[str],
    sample_path: Optional[str],
    barge_in_after: Optional[float],
    capture: Optional[str],
    timeout: float,
    manifest: Optional[Mapping[str, Any]] = None,
    model: str = "anvil-voice",
    api_key: Optional[str] = None,
) -> int:
    try:
        import openai  # type: ignore
        from openai import AsyncOpenAI  # type: ignore
    except Exception as exc:  # noqa: BLE001 - optional dependency; report clearly, do not crash import
        print(
            "realtime_sdk_client_demo: the `openai[realtime]` package is not installed/importable "
            "(%s). Install the voice extra or `pip install openai[realtime]`." % exc,
            file=sys.stderr,
        )
        return 2

    import asyncio

    events: List[Dict[str, Any]] = []
    output_audio = bytearray()
    input_audio = bytearray()
    transcripts: List[str] = []
    responses: Dict[str, Dict[str, Any]] = {}
    assistant_transcript_errors: List[str] = []
    cancelled_response_ids = set()
    state: Dict[str, Any] = {
        "connected": False,
        "barge_in_sent": False,
        "cancelled_response_seen": False,
        "completed_after_barge_in": False,
        "output_after_cancel_events": 0,
    }

    try:
        turns = _build_turns(
            data=manifest,
            text=text,
            sample_path=sample_path,
            capture=capture,
            wants_barge_in=barge_in_after is not None,
        )
    except Exception as exc:  # noqa: BLE001 - live proof setup failure should be a clean CLI failure
        print("realtime_sdk_client_demo: could not prepare input turn(s): %s" % exc, file=sys.stderr)
        return 1

    async def _run() -> int:
        client = AsyncOpenAI(
            websocket_base_url=_sdk_websocket_base_url(ws_url),
            api_key=api_key or _realtime_api_key(manifest),
        )
        async with client.realtime.connect(model=model) as connection:
            await _send_client_event(
                connection,
                events,
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "output_modalities": ["audio", "text"],
                        "input_audio_format": "pcm16",
                        "output_audio_format": "pcm16",
                    },
                },
            )

            input_audio.extend(await _send_turn(connection, events, turns[0]))
            next_turn_index = 1
            first_audio_at: Optional[float] = None
            active_response_id: Optional[str] = None
            deadline = time.monotonic() + timeout
            event_index = 0

            async def maybe_send_barge_in() -> None:
                nonlocal next_turn_index
                if (
                    barge_in_after is not None
                    and first_audio_at is not None
                    and not state["barge_in_sent"]
                    and not state.get("first_response_done")
                    and time.monotonic() - first_audio_at >= barge_in_after
                ):
                    state["cancel_requested_response_id"] = active_response_id
                    await _send_client_event(connection, events, {"type": "response.cancel"})
                    state["barge_in_sent"] = True
                    if next_turn_index < len(turns):
                        input_audio.extend(await _send_turn(connection, events, turns[next_turn_index]))
                        next_turn_index += 1

            while time.monotonic() < deadline:
                await maybe_send_barge_in()

                try:
                    event = await asyncio.wait_for(connection.recv(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                raw = _event_to_dict(event)
                event_index += 1
                _capture_event(events, "server", raw)
                etype = _event_type(raw)
                print("<- %s" % etype, flush=True)

                if etype in ("session.created", "session.updated"):
                    state["connected"] = True

                if etype == "conversation.item.input_audio_transcription.completed":
                    transcript = raw.get("transcript")
                    if isinstance(transcript, str):
                        transcripts.append(transcript)
                        print("transcript: %s" % transcript, flush=True)

                if etype == "response.output_audio_transcript.delta":
                    rid = _response_id(raw)
                    delta = raw.get("delta")
                    if not rid or not isinstance(delta, str):
                        assistant_transcript_errors.append(
                            "assistant transcript delta was missing response correlation or text"
                        )
                    else:
                        item = responses.setdefault(rid, {"created_at": None, "audio_bytes": 0})
                        item.setdefault("assistant_transcript_parts", []).append(delta)
                        for field in ("item_id", "output_index", "content_index"):
                            if raw.get(field) in (None, ""):
                                assistant_transcript_errors.append(
                                    "%s assistant transcript delta missing %s" % (rid, field)
                                )
                        print("assistant transcript delta: %s" % delta, flush=True)

                if etype == "response.output_audio_transcript.done":
                    rid = _response_id(raw)
                    transcript = raw.get("transcript")
                    if not rid or not isinstance(transcript, str):
                        assistant_transcript_errors.append(
                            "assistant transcript terminal was missing response correlation or text"
                        )
                    else:
                        item = responses.setdefault(rid, {"created_at": None, "audio_bytes": 0})
                        assembled = "".join(item.get("assistant_transcript_parts", []))
                        if assembled != transcript:
                            assistant_transcript_errors.append(
                                "%s assistant transcript terminal did not match streamed deltas" % rid
                            )
                        for field in ("item_id", "output_index", "content_index"):
                            if raw.get(field) in (None, ""):
                                assistant_transcript_errors.append(
                                    "%s assistant transcript terminal missing %s" % (rid, field)
                                )
                        item["assistant_transcript"] = transcript
                        item["assistant_transcript_done_index"] = event_index

                rid = _response_id(raw)
                if etype in (
                    "response.output_audio.delta",
                    "response.output_audio_transcript.delta",
                    "response.output_audio_transcript.done",
                ):
                    if rid in cancelled_response_ids:
                        state["output_after_cancel_events"] = int(
                            state.get("output_after_cancel_events", 0)
                        ) + 1
                    if state.get("barge_in_sent") and rid == state.get(
                        "cancel_requested_response_id"
                    ):
                        state["output_after_cancel_request_events"] = int(
                            state.get("output_after_cancel_request_events", 0)
                        ) + 1
                if etype == "response.created":
                    response = raw.get("response")
                    if isinstance(response, Mapping):
                        rid = response.get("id") if isinstance(response.get("id"), str) else rid
                    if rid:
                        active_response_id = rid
                        responses.setdefault(rid, {"created_at": time.monotonic(), "audio_bytes": 0})

                if etype == "response.output_audio.delta":
                    delta = raw.get("delta")
                    if isinstance(delta, str) and delta:
                        try:
                            chunk = base64.b64decode(delta)
                        except Exception:  # noqa: BLE001 - malformed server event should fail capture validation
                            chunk = b""
                        output_audio.extend(chunk)
                        rid = _response_id(raw)
                        if rid:
                            item = responses.setdefault(rid, {"created_at": None, "audio_bytes": 0})
                            item["audio_bytes"] = int(item.get("audio_bytes", 0)) + len(chunk)
                            if item.get("first_audio_at") is None:
                                item["first_audio_at"] = time.monotonic()
                        if first_audio_at is None:
                            first_audio_at = time.monotonic()
                        await maybe_send_barge_in()

                if etype == "response.done":
                    rid = _response_id(raw)
                    if rid:
                        item = responses.setdefault(rid, {"created_at": None, "audio_bytes": 0})
                        item["done_at"] = time.monotonic()
                        item["status"] = _response_status(raw)
                    status = _response_status(raw)
                    if status == "cancelled":
                        state["cancelled_response_seen"] = True
                        if rid:
                            cancelled_response_ids.add(rid)
                    if status == "completed" and not state["barge_in_sent"]:
                        state["first_response_done"] = True
                    if status == "completed" and rid:
                        item = responses[rid]
                        terminal_index = item.get("assistant_transcript_done_index")
                        if terminal_index is None or terminal_index >= event_index:
                            assistant_transcript_errors.append(
                                "%s completed without an earlier assistant transcript terminal" % rid
                            )
                        else:
                            item["assistant_transcript_proven"] = True
                    if state["barge_in_sent"] and status == "completed":
                        state["completed_after_barge_in"] = True
                        break
                    if barge_in_after is None and status == "completed":
                        break

            if time.monotonic() >= deadline:
                print("realtime_sdk_client_demo: timed out waiting for response.done", file=sys.stderr)
                return 1
        return 0

    rc = asyncio.run(_run())
    summary: Dict[str, Any] = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "openai_version": getattr(openai, "__version__", "unknown"),
        "ws_url": ws_url.rstrip("/") + "/v1/realtime",
        "model": model,
        "connected": bool(state["connected"]),
        "barge_in_sent": bool(state["barge_in_sent"]),
        "cancelled_response_seen": bool(state["cancelled_response_seen"]),
        "completed_after_barge_in": bool(state["completed_after_barge_in"]),
        "output_after_cancel_events": int(state.get("output_after_cancel_events", 0)),
        "output_after_cancel_request_events": int(
            state.get("output_after_cancel_request_events", 0)
        ),
        "transcripts": transcripts,
        "event_count": len(events),
        "input_audio_bytes": len(input_audio),
        "output_audio_bytes": len(output_audio),
        "responses": _summarize_responses(responses),
        "assistant_transcript_proven": any(
            bool(item.get("assistant_transcript_proven")) for item in responses.values()
        ),
        "assistant_transcript_errors": assistant_transcript_errors,
    }
    errors = _validate_capture(summary) if capture else []
    summary["acceptance_errors"] = errors

    if capture:
        _save_capture(
            capture,
            events,
            bytes(input_audio),
            bytes(output_audio),
            summary,
            append_finding=rc == 0 and not errors,
        )
    if errors:
        print("realtime_sdk_client_demo: capture failed acceptance: %s" % "; ".join(errors), file=sys.stderr)
        return 1
    if assistant_transcript_errors:
        print(
            "realtime_sdk_client_demo: assistant transcript proof failed: %s"
            % "; ".join(assistant_transcript_errors),
            file=sys.stderr,
        )
        return 1
    return rc


def build_parser():
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=DEFAULT_REALTIME_CONFIG, help="voice manifest TOML")
    p.add_argument("--host", default="127.0.0.1", help="anvil realtime server bind host (127.0.0.1, never localhost)")
    p.add_argument("--port", type=int, default=0, help="anvil realtime server port (0 = ephemeral)")
    p.add_argument("--pool-size", type=int, default=DEFAULT_POOL_SIZE)
    p.add_argument("--model", default="anvil-voice", help="Realtime model query value sent by the SDK")
    p.add_argument("--text", default=None, help="send one text-only turn instead of synthesized audio")
    p.add_argument("--sample", default=None, help="send one audio turn from a mono 16-bit PCM WAV file")
    p.add_argument(
        "--barge-in-after",
        type=float,
        default=None,
        help="seconds after first assistant audio to send response.cancel",
    )
    p.add_argument("--timeout", type=float, default=60.0, help="max seconds for the proof session")
    p.add_argument(
        "--capture",
        nargs="?",
        const=_AUTO_CAPTURE,
        default=None,
        help="optional artifact path prefix; with no value, writes under temp anvil-voice-captures",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    capture_prefix = resolve_capture_prefix(args.capture)
    barge_in_after = args.barge_in_after
    if capture_prefix and barge_in_after is None:
        barge_in_after = 0.0

    try:
        data = voice_config.load_manifest(args.config)
    except voice_config.ConfigError as exc:
        print("realtime_sdk_client_demo: %s" % exc, file=sys.stderr)
        return 2

    try:
        server, thread, pool = build_server(
            manifest=data,
            host=args.host,
            port=args.port,
            pool_size=args.pool_size,
        )
    except ValueError as exc:
        print("realtime_sdk_client_demo: %s" % exc, file=sys.stderr)
        return 2

    host, port = server.server_address[:2]
    ws_url = "ws://%s:%d" % (host, port)
    print(
        "realtime_sdk_client_demo: manifest OK -- %s" % voice_config.describe(data)
    )
    print(
        "realtime_sdk_client_demo: anvil realtime server up at %s/v1/realtime "
        "(pool size %d)" % (ws_url, pool.size)
    )
    try:
        return run_session(
            ws_url=ws_url,
            text=args.text,
            sample_path=args.sample,
            barge_in_after=barge_in_after,
            capture=capture_prefix,
            timeout=args.timeout,
            manifest=data,
            model=args.model,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


if __name__ == "__main__":
    raise SystemExit(main())
