#!/usr/bin/env python
"""RUN ON fakoli-dark — NOT YET EXECUTED (requires the `openai` package, a
running anvil router, and the STT/TTS serves reachable)

Connects the OFFICIAL ``openai`` Python SDK's Realtime client to anvil's own
Realtime WebSocket server (anvil task T014) -- proving genuine wire
compatibility the way the reference design's own server was verified (see
``docs/findings/2026-07-04-hf-speech-to-speech-review.md`` s5: "verified with
the official OpenAI Python SDK as client (``client.realtime.connect``) --
genuine wire compatibility, not a lookalike").

``anvil-serving voice run`` does not yet wire the realtime server together
(``anvil_serving/voice/cli.py``'s ``cmd_run`` is still a TODO stub) -- this
script assembles the same pieces standalone (``anvil_serving.voice.realtime.ws``
+ ``.pool`` + ``.service``, plus this unit's own
:class:`~scripts.voice._real_pipeline.RealVoicePipeline`) so T014 can be
exercised end-to-end without waiting on that CLI wire-up. The server-wiring
function below (:func:`build_server`) is a reasonable template for what
``cmd_run`` will eventually do; it is deliberately kept in this script rather
than added to ``anvil_serving/voice/cli.py`` (outside this unit's assigned
files).

Exchanges one text-only turn (``--text``) or one audio turn (``--sample``,
a mono 16-bit PCM WAV) over the connection, prints every received Realtime
event, and demonstrates barge-in by sending ``response.cancel`` shortly after
the reply starts (``--barge-in-after``). ``--capture PREFIX`` saves every
received event as JSON-lines (``PREFIX.events.jsonl``) and the assistant's
decoded audio as a WAV (``PREFIX.wav``).

HONESTY NOTE / verify-before-running: the exact ``openai`` SDK Realtime
client surface (``client.realtime.connect(...)``, the async context-manager
protocol, ``connection.session.update``/``conversation.item.create``/
``response.create``/``response.cancel`` method names and the
``async for event in connection`` iteration shape) is written from the
documented usage pattern the reference design's README/tests describe -- it
has NOT been checked against a specific installed ``openai`` package version
here, and that SDK's realtime surface has been evolving upstream. VERIFY the
exact method/attribute names against ``python -c "import openai; print(openai.__version__)"``
and that version's own examples before trusting this script to run
unmodified. Nothing in this module has been executed.

Guarded import: ``openai`` is imported ONLY inside :func:`run_session`, so
this module (and ``--help``) stays importable without the package installed.
``openai`` is an OPTIONAL dependency (the ``voice`` extra in ``pyproject.toml``)
-- never required for the core router/substrate.
"""
from __future__ import annotations

import base64
import json
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anvil_serving.voice import config as voice_config  # noqa: E402
from anvil_serving.voice.realtime.pool import SessionPool  # noqa: E402
from anvil_serving.voice.realtime.service import RealtimeService  # noqa: E402
from anvil_serving.voice.realtime.ws import (  # noqa: E402
    WebSocketConnection,
    make_ws_server,
    serve_forever_in_background,
)

from scripts.voice._real_pipeline import real_pipeline_config_from_manifest  # noqa: E402

DEFAULT_POOL_SIZE = 2
#: How often the sender thread polls `service.drain_pipeline_events()` and
#: forwards anything buffered to the client -- a plain sleep-poll, matching
#: `RealtimeService.drain_pipeline_events`'s own non-blocking contract (see
#: that module's docstring) rather than adding a new blocking wakeup
#: mechanism to a shared module outside this unit's assigned files.
SENDER_POLL_INTERVAL_S = 0.05


def build_server(
    *, manifest: Dict[str, Any], host: str, port: int, pool_size: int,
) -> "tuple[Any, threading.Thread, SessionPool]":
    """Wire ``ws.py`` + ``pool.py`` + ``service.py`` into one running Realtime
    WebSocket server backed by real out-of-process STT/LLM/TTS stages.

    Returns ``(server, serve_thread, pool)``; caller is responsible for
    ``server.shutdown()``/``server.server_close()`` on teardown.

    THREAD-SAFETY NOTE: ``on_connect`` below writes to ONE ``conn`` (the same
    :class:`WebSocketConnection`) from TWO threads -- this function's own
    recv-loop thread (sending immediate replies via ``service.send_event`` ==
    ``conn.send_json``) and ``sender_loop``'s own background thread (sending
    drained pipeline events via ``conn.send_text``). This is safe because
    ``WebSocketConnection._send_frame`` serializes every send behind its own
    internal lock (``anvil_serving/voice/realtime/ws.py``) -- so the two
    threads' frames can never interleave on the wire -- rather than this
    script rolling its own separate lock around each call site.
    """
    pipeline_config = real_pipeline_config_from_manifest(manifest)

    def pipeline_factory():
        from scripts.voice._real_pipeline import RealVoicePipeline

        return RealVoicePipeline(pipeline_config)

    pool = SessionPool(size=pool_size, pipeline_factory=pipeline_factory)
    session_counter = [0]

    def on_connect(conn: WebSocketConnection, path: str) -> None:
        session_counter[0] += 1
        session_id = "sdk-demo-%d" % session_counter[0]
        try:
            unit = pool.claim(session_id)
        except Exception:  # noqa: BLE001 - SessionPoolExhausted: reject cleanly, matching the Realtime API's own behavior
            conn.send_json({"type": "error", "event_id": "evt_reject", "error": {
                "type": "session_limit_reached", "message": "no free session slot",
            }})
            conn.close(code=1008, reason="session_limit_reached")
            return

        service = RealtimeService(pipeline=unit.pipeline, send_event=conn.send_json, session_id=session_id)
        unit.service = service
        conn.send_json({"type": "session.created", "event_id": "evt_session", "session": {"id": session_id}})

        stop_sender = threading.Event()

        def sender_loop() -> None:
            while not stop_sender.is_set():
                for event in service.drain_pipeline_events():
                    try:
                        conn.send_text(json.dumps(event))
                    except OSError:
                        return
                time.sleep(SENDER_POLL_INTERVAL_S)

        sender_thread = threading.Thread(target=sender_loop, daemon=True, name="realtime-sender-%s" % session_id)
        sender_thread.start()
        try:
            while True:
                text = conn.recv_text()
                if text is None:
                    break
                service.handle_client_message(text)
        finally:
            stop_sender.set()
            sender_thread.join(timeout=2.0)
            pool.release(unit)

    server = make_ws_server(host, port, on_connect, extra_routes={
        "/pool": pool.pool_status, "/usage": pool.usage_stats,
    })
    thread = serve_forever_in_background(server)
    return server, thread, pool


def _save_capture(prefix: str, events: List[Dict[str, Any]], sample_rate: int = 16000) -> None:
    events_path = prefix + ".events.jsonl"
    with open(events_path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")

    audio_bytes = bytearray()
    for event in events:
        if event.get("type") == "response.output_audio.delta" and event.get("delta"):
            audio_bytes += base64.b64decode(event["delta"])
    wav_path = None
    if audio_bytes:
        wav_path = prefix + ".wav"
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(bytes(audio_bytes))

    print("realtime_sdk_client_demo: wrote %s%s" % (events_path, (" and %s" % wav_path) if wav_path else ""))


def run_session(
    *, ws_url: str, text: Optional[str], sample_path: Optional[str],
    barge_in_after: Optional[float], capture: Optional[str], timeout: float,
) -> int:
    try:
        from openai import AsyncOpenAI  # type: ignore
    except Exception as exc:  # noqa: BLE001 - the SDK is an optional dependency; report clearly, don't crash import
        print(
            "realtime_sdk_client_demo: the `openai` package is not installed/importable (%s). "
            "Install it (pip install openai) to run this demo -- see the module docstring's "
            "'voice' extra note in pyproject.toml." % exc, file=sys.stderr,
        )
        return 2

    import asyncio

    events: List[Dict[str, Any]] = []

    async def _run() -> int:
        client = AsyncOpenAI(base_url=ws_url, api_key="anvil-local-demo-not-a-real-key")
        # NOTE (see module HONESTY NOTE): verify `client.realtime.connect(...)`
        # against your installed `openai` SDK version before running for real.
        async with client.realtime.connect(model="anvil-voice") as connection:
            await connection.session.update(session={"modalities": ["text", "audio"]})

            if text:
                await connection.conversation.item.create(
                    item={"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
                )
                await connection.response.create()
            elif sample_path:
                with wave.open(sample_path, "rb") as w:
                    pcm = w.readframes(w.getnframes())
                await connection.input_audio_buffer.append(audio=base64.b64encode(pcm).decode("ascii"))
                await connection.input_audio_buffer.commit()
            else:
                print("realtime_sdk_client_demo: pass --text or --sample to drive a turn", file=sys.stderr)
                return 2

            deadline = time.monotonic() + timeout
            barge_in_sent = barge_in_after is None
            turn_start = time.monotonic()
            async for event in connection:
                raw = event.model_dump() if hasattr(event, "model_dump") else dict(event)
                events.append(raw)
                print("<- %s" % raw.get("type"))
                if not barge_in_sent and (time.monotonic() - turn_start) >= barge_in_after:
                    print("-> response.cancel (barge-in test)")
                    await connection.response.cancel()
                    barge_in_sent = True
                if raw.get("type") == "response.done" or time.monotonic() > deadline:
                    break
        return 0

    rc = asyncio.run(_run())
    if capture:
        _save_capture(capture, events)
    return rc


def build_parser():
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=voice_config.DEFAULT_CONFIG, help="voice manifest TOML (STT/LLM/TTS endpoints)")
    p.add_argument("--host", default="127.0.0.1", help="anvil realtime server bind host (127.0.0.1, never localhost)")
    p.add_argument("--port", type=int, default=0, help="anvil realtime server port (0 = ephemeral)")
    p.add_argument("--pool-size", type=int, default=DEFAULT_POOL_SIZE)
    p.add_argument("--text", default=None, help="send one text-only turn")
    p.add_argument("--sample", default=None, help="send one audio turn from a mono 16-bit PCM WAV file")
    p.add_argument("--barge-in-after", type=float, default=None, help="seconds after turn start to send response.cancel")
    p.add_argument("--timeout", type=float, default=20.0, help="max seconds to wait for response.done")
    p.add_argument("--capture", default=None, help="path prefix to save received events (.events.jsonl) + audio (.wav)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        data = voice_config.load_manifest(args.config)
    except voice_config.ConfigError as exc:
        print("realtime_sdk_client_demo: %s" % exc, file=sys.stderr)
        return 2

    server, thread, pool = build_server(manifest=data, host=args.host, port=args.port, pool_size=args.pool_size)
    host, port = server.server_address[:2]
    ws_url = "ws://%s:%d" % (host, port)
    print(
        "realtime_sdk_client_demo: anvil realtime server up at %s/v1/realtime "
        "(pool size %d)" % (ws_url, args.pool_size)
    )
    try:
        return run_session(
            ws_url=ws_url, text=args.text, sample_path=args.sample,
            barge_in_after=args.barge_in_after, capture=args.capture, timeout=args.timeout,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


if __name__ == "__main__":
    raise SystemExit(main())
