"""Focused tests for the official-SDK realtime proof harness.

The live T014 acceptance still runs ``scripts/voice/realtime_sdk_client_demo.py
--capture`` against real services. These tests only guard local harness
behavior: URL shaping and SDK control-flow compatibility.
"""
from __future__ import annotations

import base64
import json
import threading

import pytest

from anvil_serving.voice.realtime.ws import make_ws_server, serve_forever_in_background
from anvil_serving.voice.realtime import app as realtime_app
from scripts.voice import realtime_sdk_client_demo as demo


def test_sdk_websocket_base_url_targets_anvil_realtime_path():
    assert demo._sdk_websocket_base_url("ws://127.0.0.1:8765") == "ws://127.0.0.1:8765/v1"
    assert demo._sdk_websocket_base_url("ws://127.0.0.1:8765/v1") == "ws://127.0.0.1:8765/v1"
    assert (
        demo._sdk_websocket_base_url("ws://127.0.0.1:8765/v1/realtime")
        == "ws://127.0.0.1:8765/v1"
    )


def test_realtime_app_outbound_queue_prunes_queued_output_on_cancel():
    outbound = realtime_app._OutboundEvents()
    outbound.enqueue({"type": "response.created", "response": {"id": "resp_1"}})
    outbound.enqueue({"type": "response.output_audio.delta", "response_id": "resp_1", "delta": "AAAA"})
    outbound.enqueue(
        {
            "type": "response.output_audio_transcript.delta",
            "response_id": "resp_1",
            "delta": "Stale text.",
        }
    )
    outbound.enqueue(
        {
            "type": "response.output_audio_transcript.done",
            "response_id": "resp_1",
            "transcript": "Stale text.",
        }
    )
    outbound.enqueue(
        {
            "type": "error",
            "response_id": "resp_1",
            "error": {"type": "assistant_transcript_unavailable", "message": "stale"},
        }
    )
    outbound.enqueue({"type": "response.done", "response": {"id": "resp_1", "status": "completed"}})
    outbound.enqueue(
        {"type": "response.done", "response": {"id": "resp_1", "status": "cancelled"}},
    )

    got = []
    while True:
        item = outbound.pop(timeout=0.0)
        if item is None:
            break
        got.append(item)

    assert [event["type"] for event in got] == ["response.created", "response.done"]
    assert got[-1]["response"]["status"] == "cancelled"


def test_capture_validation_rejects_output_after_cancel_request():
    errors = demo._validate_capture(
        {
            "connected": True,
            "input_audio_bytes": 4,
            "output_audio_bytes": 4,
            "transcripts": ["hello"],
            "assistant_transcript_proven": True,
            "assistant_transcript_errors": [],
            "barge_in_sent": True,
            "cancelled_response_seen": True,
            "completed_after_barge_in": True,
            "output_after_cancel_request_events": 1,
            "output_after_cancel_events": 0,
        }
    )

    assert any("after response.cancel" in error for error in errors)


def test_run_session_with_official_sdk_barges_in_against_scripted_server():
    pytest.importorskip("openai")
    pytest.importorskip("websockets")

    received = []
    paths = []
    done = threading.Event()

    def on_connect(conn, path):
        paths.append(path)
        conn.send_json({"type": "session.created", "event_id": "evt_session", "session": {"id": "s1"}})
        response_ids = []
        try:
            while True:
                text = conn.recv_text()
                if text is None:
                    break
                raw = json.loads(text)
                received.append(raw)
                etype = raw.get("type")
                if etype == "session.update":
                    conn.send_json({"type": "session.updated", "event_id": "evt_update", "session": {}})
                elif etype == "conversation.item.create":
                    conn.send_json(
                        {
                            "type": "conversation.item.created",
                            "event_id": "evt_item_%d" % len(received),
                            "item": raw.get("item", {}),
                        }
                    )
                elif etype == "response.create":
                    response_id = "resp_%d" % (len(response_ids) + 1)
                    response_ids.append(response_id)
                    conn.send_json(
                        {
                            "type": "response.created",
                            "event_id": "evt_created_%s" % response_id,
                            "response": {"id": response_id, "status": "in_progress"},
                        }
                    )
                    conn.send_json(
                        {
                            "type": "response.output_audio.delta",
                            "event_id": "evt_audio_%s" % response_id,
                            "response_id": response_id,
                            "delta": base64.b64encode(b"\x01\x00\x02\x00").decode("ascii"),
                        }
                    )
                    if len(response_ids) == 2:
                        conn.send_json(
                            {
                                "type": "response.output_audio_transcript.delta",
                                "event_id": "evt_transcript_delta_%s" % response_id,
                                "response_id": response_id,
                                "item_id": "turn-2",
                                "output_index": 0,
                                "content_index": 0,
                                "delta": "There are 54 countries in Africa.",
                            }
                        )
                        conn.send_json(
                            {
                                "type": "response.output_audio_transcript.done",
                                "event_id": "evt_transcript_done_%s" % response_id,
                                "response_id": response_id,
                                "item_id": "turn-2",
                                "output_index": 0,
                                "content_index": 0,
                                "transcript": "There are 54 countries in Africa.",
                            }
                        )
                        conn.send_json(
                            {
                                "type": "response.done",
                                "event_id": "evt_done_%s" % response_id,
                                "response": {"id": response_id, "status": "completed"},
                            }
                        )
                        break
                elif etype == "response.cancel" and response_ids:
                    conn.send_json(
                        {
                            "type": "response.done",
                            "event_id": "evt_cancel",
                            "response": {"id": response_ids[-1], "status": "cancelled"},
                        }
                    )
        finally:
            done.set()

    server = make_ws_server("127.0.0.1", 0, on_connect)
    thread = serve_forever_in_background(server)
    host, port = server.server_address[:2]
    try:
        rc = demo.run_session(
            ws_url="ws://%s:%d" % (host, port),
            text="please count slowly",
            sample_path=None,
            barge_in_after=0.0,
            capture=None,
            timeout=5.0,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)

    assert rc == 0
    assert done.wait(timeout=1.0)
    assert paths and paths[0].split("?", 1)[0] == "/v1/realtime"
    assert "response.cancel" in [event.get("type") for event in received]
    session_update = next(event for event in received if event.get("type") == "session.update")
    assert session_update["session"]["output_modalities"] == ["audio", "text"]
