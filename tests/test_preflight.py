"""Tests for local-serve preflight validation."""

import json
from base64 import b64decode
from io import BytesIO
from urllib.error import HTTPError

import pytest

from anvil_serving import preflight as pf


def test_chat_capture_rejects_oversized_request_before_sending(monkeypatch):
    monkeypatch.setattr(
        pf.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("request sent")
    )
    capture = {}
    with pytest.raises(RuntimeError, match="request exceeded capture bound"):
        pf.chat(
            "http://127.0.0.1:30000/v1", "candidate",
            [{"role": "user", "content": "x" * pf.MAX_TOOL_TRANSPORT_CAPTURE_BYTES}],
            tools=pf.TOOLS, tool_choice="required", native_tool_transport=capture,
        )

    assert capture["capture_refusal"] == "request_json_exceeds_capture_bound"
    assert capture["request_bytes"] > pf.MAX_TOOL_TRANSPORT_CAPTURE_BYTES


def test_chat_capture_retains_http_error_status_and_body(monkeypatch):
    response_body = b'{"error":"tool rejected"}'

    def reject(*_args, **_kwargs):
        raise HTTPError(
            "http://127.0.0.1:30000/v1/chat/completions", 400, "Bad Request",
            {"request-id": "server-request-18"}, BytesIO(response_body),
        )

    monkeypatch.setattr(pf.urllib.request, "urlopen", reject)
    capture = {}
    with pytest.raises(RuntimeError, match="HTTP 400"):
        pf.chat(
            "http://127.0.0.1:30000/v1", "candidate", [{"role": "user", "content": "test"}],
            tools=pf.TOOLS, tool_choice="required", native_tool_transport=capture,
        )

    assert capture["response_http_status"] == 400
    assert b64decode(capture["response_body_base64"]) == response_body
    assert capture["server_request_ids"] == {"request-id": "server-request-18"}


def test_preflight_force_nonempty_content_dry_run_and_invalid_modes(monkeypatch, capsys):
    assert pf.main([
        "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
        "--checks", "tools", "--thinking-mode", "enabled",
        "--force-nonempty-content", "--dry-run",
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["thinking"]["chat_template_kwargs"] == {
        "enable_thinking": True, "force_nonempty_content": True,
    }

    monkeypatch.setattr(pf, "t_smoke", lambda *_args, **_kwargs: pytest.fail("request made"))
    for controls in ([], ["--thinking-mode", "disabled"], ["--thinking-mode", "unsupported"],
                     ["--reasoning-effort", "max"]):
        with pytest.raises(SystemExit) as exc:
            pf.main([
                "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
                "--checks", "smoke", "--force-nonempty-content", *controls,
            ])
        assert exc.value.code == 2


@pytest.mark.parametrize("choice, expected_detail", [
    ({"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"type": "function", "id": "call-1", "function": {
            "name": "get_weather", "arguments": '{"city":"Berkeley"}',
        }},
    ]}}, "wrong city"),
    ({"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"type": "function", "id": "call-1", "function": {
            "name": "get_weather", "arguments": '{"city":"Oakland"}',
        }},
        {"type": "function", "id": "call-2", "function": {
            "name": "get_weather", "arguments": '{"city":"Oakland"}',
        }},
    ]}}, "exactly one"),
    ({"finish_reason": "stop", "message": {"tool_calls": [
        {"type": "function", "id": "call-1", "function": {
            "name": "get_weather", "arguments": '{"city":"Oakland"}',
        }},
    ]}}, "expected 'tool_calls'"),
])
def test_validate_tool_response_rejects_wrong_city_multiple_calls_and_wrong_finish(
    choice, expected_detail,
):
    ok, detail = pf.validate_tool_response(
        choice, expected_argument_values={"city": "Oakland"},
    )
    assert ok is False
    assert expected_detail in detail


def test_preflight_dry_run_never_requests_or_writes(monkeypatch, tmp_path, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("dry-run crossed a deferred boundary")

    monkeypatch.setattr(pf, "t_smoke", boom)
    monkeypatch.setattr(pf, "_atomic_write_json", boom)
    out = tmp_path / "preflight.json"
    assert pf.main([
        "--base-url", "http://127.0.0.1:30000/v1",
        "--model", "candidate",
        "--checks", "smoke",
        "--output", str(out),
        "--timeout", "15",
        "--dry-run",
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["workload"] == "preflight"
    assert plan["timeout_seconds"] == 15.0
    assert not out.exists()


def test_preflight_rejects_missing_api_key_environment(monkeypatch):
    monkeypatch.delenv("MISSING_PREFLIGHT_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "candidate",
            "--api-key-env", "MISSING_PREFLIGHT_TOKEN",
            "--dry-run",
        ])
    assert exc.value.code == 2


def test_atomic_preflight_write_preserves_existing_target_on_replace_failure(
    monkeypatch, tmp_path
):
    out = tmp_path / "preflight.json"
    out.write_text("old evidence\n", encoding="utf-8")

    def fail_replace(*args, **kwargs):
        raise OSError("replace denied")

    monkeypatch.setattr(pf.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace denied"):
        pf._atomic_write_json(out, {"passed": True})

    assert out.read_text(encoding="utf-8") == "old evidence\n"
    assert list(tmp_path.glob(".preflight.json.*.tmp")) == []


@pytest.mark.parametrize("temperature", [0.0, 1.0])
def test_preflight_sampling_reaches_all_text_transports(monkeypatch, tmp_path, temperature):
    bodies = []

    def reject(request, **kwargs):
        bodies.append(json.loads(request.data))
        raise RuntimeError("synthetic transport failure")

    monkeypatch.setattr(pf.urllib.request, "urlopen", reject)
    out = tmp_path / "sampling.json"
    assert pf.main([
        "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
        "--checks", "smoke,json,needle,tools,long-tools,streaming-tools,tool-result,responses",
        "--tool-batch", "2", "--needle-ctx", "32", "--temperature", str(temperature),
        "--output", str(out),
    ]) == 1
    assert len(bodies) == 9
    assert all(body["temperature"] == temperature for body in bodies)
    assert json.loads(out.read_text())["sampling"] == {"temperature": temperature}


def test_preflight_sampling_reaches_image_and_video_probes(monkeypatch):
    temperatures = []

    def chat(*args, **kwargs):
        temperatures.append(kwargs["temperature"])
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": "IMAGE_OK VIDEO_OK",
        }}]}, 0.1

    monkeypatch.setattr(pf, "chat", chat)
    assert pf.t_multimodal(
        "http://127.0.0.1:30000/v1", "candidate", None, "data:image/png;base64,AA==",
        {"sha256": "a" * 64}, ("IMAGE_OK",), check="image", temperature=1.0,
    )[0]
    assert pf.t_video(
        "http://127.0.0.1:30000/v1", "candidate", None, "data:video/mp4;base64,AA==",
        {"sha256": "b" * 64}, ("VIDEO_OK",), temperature=1.0,
    )[0]
    assert temperatures == [1.0, 1.0]


def test_tool_result_forces_only_the_tool_call_producing_request(monkeypatch):
    requests = []

    def chat(*_args, **kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "tool_calls": [{"type": "function", "id": "call-1", "function": {
                    "name": "get_weather", "arguments": '{"city":"Oakland"}',
                }}],
            }}]}, 0.1
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": "OAKLAND 72F",
        }}]}, 0.1

    monkeypatch.setattr(pf, "chat", chat)
    evidence = []
    passed, _ = pf.t_tool_result(
        "http://127.0.0.1:30000/v1", "candidate", None, evidence=evidence,
        tool_choice_mode="required", seed=17, capture_tool_transport=True,
    )

    assert passed is True
    assert requests[0]["tool_choice"] == "required"
    assert requests[0]["seed"] == 17
    assert requests[0]["native_tool_transport"] == {}
    assert "tool_choice" not in requests[1]
    assert requests[1]["seed"] == 17
    assert "native_tool_transport" not in requests[1]
    assert evidence[1]["request_controls"]["tool_choice"] is None


def test_tool_choice_dry_run_and_seed_validation(capsys):
    assert pf.main([
        "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
        "--checks", "tools", "--tool-choice-mode", "required", "--seed", "17",
        "--capture-tool-transport", "--dry-run",
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["sampling"] == {
        "temperature": 0.0, "seed": 17, "seed_scope": "tool_protocol_requests",
    }
    assert plan["tool_choice"] == {
        "oracle_revision": "tool-call/v2-exact-weather",
        "mode": "required",
        "transport_capture": "bounded_nonstreaming_tool_calls",
        "raw_sse_capture": "unsupported",
    }
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
            "--checks", "tools", "--seed", str(2 ** 63), "--dry-run",
        ])
    assert exc.value.code == 2


def test_streaming_tool_rejects_content_beyond_evidence_bound(monkeypatch):
    def stream(*_args, **_kwargs):
        return [{"choices": [{"finish_reason": "tool_calls", "delta": {
            "content": "x" * (pf.MAX_STREAM_TOOL_CONTENT_CHARS + 1),
            "tool_calls": [{"index": 0, "id": "call-", "type": "function", "function": {
                "name": "get_weather", "arguments": '{"city":"Oakland"}',
            }}],
        }}]}], True, 0.1

    monkeypatch.setattr(pf, "chat_stream", stream)
    evidence = []
    passed, detail = pf.t_streaming_tool(
        "http://127.0.0.1:30000/v1", "candidate", None, evidence=evidence,
    )
    assert passed is False
    assert detail.startswith("0.1s")
    assert evidence[0]["content_truncated"] is True
    assert evidence[0]["validation_detail"] == "stream content exceeded evidence bound"
