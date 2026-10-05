"""Tests for local-serve preflight validation."""

import json
import sys
from base64 import b64decode
from io import BytesIO
from urllib.error import HTTPError

import pytest

from anvil_serving import preflight as pf


def test_chat_preserves_bounded_http_error_body(monkeypatch):
    body = json.dumps({
        "error": {
            "message": "maximum context length is 131072 tokens",
            "type": "BadRequestError",
        }
    }).encode()

    def reject(*_args, **_kwargs):
        raise HTTPError(
            "http://127.0.0.1:30000/v1/chat/completions",
            400,
            "Bad Request",
            {},
            BytesIO(body),
        )

    monkeypatch.setattr(pf.urllib.request, "urlopen", reject)

    with pytest.raises(RuntimeError, match="HTTP 400.*maximum context length"):
        pf.chat(
            "http://127.0.0.1:30000/v1",
            "candidate",
            [{"role": "user", "content": "test"}],
        )


def test_response_observation_records_finish_and_reasoning_evidence():
    observation = pf.response_observation({
        "choices": [{
            "finish_reason": "length",
            "message": {"content": "", "reasoning_content": "still working"},
        }],
        "usage": {"completion_tokens_details": {"reasoning_tokens": 256}},
    })

    assert observation["finish_reason"] == "length"
    assert observation["content_chars"] == 0
    assert observation["content"] == ""
    assert observation["reasoning_field"] == "reasoning_content"
    assert observation["reasoning_chars"] == len("still working")
    assert observation["reasoning_tokens"] == 256


def test_preflight_main_records_model_controls_and_budget(monkeypatch, tmp_path):
    calls = []

    def fake_smoke(base, model, key, ctk, max_tokens, reasoning_effort, evidence, timeout):
        calls.append((ctk, max_tokens, reasoning_effort))
        assert timeout == 60.0
        evidence.append({"test": "smoke", "finish_reason": "stop", "reasoning_chars": 12})
        return True, "ok"

    monkeypatch.setattr(pf, "t_smoke", fake_smoke)
    out = tmp_path / "preflight.json"
    rc = pf.main([
        "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
        "--checks", "smoke", "--thinking-mode", "enabled",
        "--visible-answer-tokens", "256", "--reasoning-headroom-tokens", "4096",
        "--timeout", "60",
        "--json-out", str(out),
    ])

    assert rc == 0
    assert calls == [({"enable_thinking": True}, 4352, None)]
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert b"\r\n" not in out.read_bytes()
    assert artifact["schema_version"] == "preflight/v2"
    assert artifact["thinking"]["mode"] == "enabled"
    assert artifact["budget"]["max_completion_tokens"] == 4352
    assert artifact["observations"][0]["finish_reason"] == "stop"


def test_preflight_forwards_deepseek_max_reasoning_effort(monkeypatch):
    calls = []

    def fake_smoke(base, model, key, ctk, max_tokens, reasoning_effort, evidence, timeout):
        calls.append(reasoning_effort)
        evidence.append({"test": "smoke", "finish_reason": "stop", "reasoning_chars": 12})
        return True, "ok"

    monkeypatch.setattr(pf, "t_smoke", fake_smoke)
    assert pf.main([
        "--base-url", "http://127.0.0.1:30000/v1",
        "--model", "deepseek-v4-flash-0731",
        "--checks", "smoke",
        "--reasoning-effort", "max",
    ]) == 0
    assert calls == ["max"]


def test_preflight_forwards_qwen38_xhigh_reasoning_effort(monkeypatch):
    calls = []

    def fake_smoke(base, model, key, ctk, max_tokens, reasoning_effort, evidence, timeout):
        calls.append(reasoning_effort)
        evidence.append({"test": "smoke", "finish_reason": "stop", "reasoning_chars": 12})
        return True, "ok"

    monkeypatch.setattr(pf, "t_smoke", fake_smoke)
    assert pf.main([
        "--base-url", "http://127.0.0.1:39080/v1",
        "--model", "qwen38-27b-bf16-262k",
        "--checks", "smoke",
        "--reasoning-effort", "xhigh",
    ]) == 0
    assert calls == ["xhigh"]


def test_preflight_rejects_length_finish_and_missing_required_reasoning(monkeypatch):
    def fake_smoke(base, model, key, ctk, max_tokens, reasoning_effort, evidence, timeout):
        assert timeout == 60.0
        evidence.append({
            "test": "smoke", "finish_reason": "length", "reasoning_chars": 0,
            "reasoning_tokens": None,
        })
        return True, "structural output happened to pass"

    monkeypatch.setattr(pf, "t_smoke", fake_smoke)
    assert pf.main([
        "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
        "--checks", "smoke", "--thinking-mode", "enabled",
        "--reasoning-headroom-tokens", "4096", "--reasoning-evidence", "required",
        "--timeout", "60",
    ]) == 1


def test_validate_tool_call_accepts_schema_valid_function_call():
    ok, detail = pf.validate_tool_call({
        "tool_calls": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": '{"city": "Oakland"}',
            },
        }]
    })

    assert ok is True
    assert "Oakland" in detail


def test_validate_tool_call_rejects_plain_text_claim():
    ok, detail = pf.validate_tool_call({
        "content": "I will call get_weather for Oakland."
    })

    assert ok is False
    assert detail == "response did not include tool_calls"


def test_validate_tool_call_rejects_missing_required_argument():
    ok, detail = pf.validate_tool_call({
        "tool_calls": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": "{}",
            },
        }]
    })

    assert ok is False
    assert "missing required string argument" in detail


def test_t_tool_one_rejects_text_only_response(monkeypatch):
    def fake_chat(*args, **kwargs):
        return {
            "choices": [{
                "message": {"content": "The weather in Oakland is sunny."}
            }]
        }, 0.01

    monkeypatch.setattr(pf, "chat", fake_chat)

    ok, detail = pf.t_tool_one(
        "http://127.0.0.1:30000/v1",
        "candidate",
        None,
        "shared prefix",
    )

    assert ok is False
    assert "did not include tool_calls" in detail


def test_long_tool_requires_valid_call_and_measured_100k_prompt(monkeypatch):
    def fake_chat(*_args, **_kwargs):
        return {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"Oakland"}',
                        },
                    }],
                },
            }],
            "usage": {"prompt_tokens": 100123, "completion_tokens": 12},
        }, 1.25

    monkeypatch.setattr(pf, "chat", fake_chat)
    evidence = []
    passed, detail = pf.t_long_tool(
        "http://127.0.0.1:30000/v1", "candidate", None, 131072, evidence=evidence
    )

    assert passed is True
    assert "measured_prompt=100123" in detail
    assert evidence[0]["measured_prompt_tokens"] == 100123
    assert evidence[0]["passed"] is True


def test_long_tool_fails_when_usage_does_not_prove_100k(monkeypatch):
    def fake_chat(*_args, **_kwargs):
        return {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": {"city": "Oakland"},
                        },
                    }],
                },
            }],
            "usage": {"prompt_tokens": 99999, "completion_tokens": 12},
        }, 1.25

    monkeypatch.setattr(pf, "chat", fake_chat)
    passed, _detail = pf.t_long_tool(
        "http://127.0.0.1:30000/v1", "candidate", None, 131072
    )

    assert passed is False


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


def test_preflight_rejects_invalid_output_before_live_probe(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("invalid output must fail before a live probe")

    monkeypatch.setattr(pf, "t_smoke", boom)
    out = tmp_path / "missing" / "preflight.json"
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "candidate",
            "--checks", "smoke",
            "--output", str(out),
        ])
    assert exc.value.code == 2


@pytest.mark.parametrize("flag,value", [
    ("--needle-ctx", "0"),
    ("--long-tool-ctx", "99999"),
    ("--tool-batch", "129"),
    ("--timeout", "0"),
    ("--visible-answer-tokens", "0"),
])
def test_preflight_rejects_unsafe_bounds(flag, value):
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "candidate",
            flag, value,
        ])
    assert exc.value.code == 2


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


def test_preflight_rejects_wrong_model_family_control(capsys):
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "openai/gpt-oss-120b",
            "--thinking-mode", "disabled",
            "--dry-run",
        ])
    assert exc.value.code == 2
    assert "does not use Qwen" in capsys.readouterr().err


def test_console_safe_escapes_model_text_for_legacy_windows_console(monkeypatch):
    monkeypatch.setattr(sys, "stdout", type("LegacyStdout", (), {"encoding": "cp1252"})())
    assert pf._console_safe("model returned snowman \u2603") == (
        "model returned snowman \\u2603"
    )


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


def test_load_image_data_is_bounded_and_records_identity(tmp_path):
    image = tmp_path / "sample.png"
    raw = b"\x89PNG\r\n\x1a\nbounded"
    image.write_bytes(raw)

    data_url, identity = pf.load_image_data(image)

    assert data_url.startswith("data:image/png;base64,")
    assert b64decode(data_url.split(",", 1)[1]) == raw
    assert identity["bytes"] == len(raw)
    assert len(identity["sha256"]) == 64


def test_load_video_data_is_bounded_and_records_identity(tmp_path):
    video = tmp_path / "sample.mp4"
    raw = b"\x00\x00\x00\x18ftypmp42bounded"
    video.write_bytes(raw)

    data_url, identity = pf.load_video_data(video)

    assert data_url.startswith("data:video/mp4;base64,")
    assert b64decode(data_url.split(",", 1)[1]) == raw
    assert identity["bytes"] == len(raw)
    assert identity["mime"] == "video/mp4"
    assert len(identity["sha256"]) == 64


@pytest.mark.parametrize(
    ("validator", "filename", "expected"),
    [
        (pf.validate_image_path, "missing.png", "image path is not a regular file"),
        (pf.validate_video_path, "missing.mp4", "video path is not a regular file"),
    ],
)
def test_validate_media_path_rejects_missing_fixture(
    tmp_path, validator, filename, expected
):
    with pytest.raises(ValueError, match=expected):
        validator(tmp_path / filename)


def test_parse_checks_rejects_unknown_or_non_string_values():
    with pytest.raises(ValueError, match=r"unknown=\['bogus'\]"):
        pf.parse_checks("smoke,bogus")
    with pytest.raises(ValueError, match="comma-separated string"):
        pf.parse_checks(["smoke"])


def test_multimodal_check_requires_all_independent_expectations(monkeypatch):
    def fake_chat(*args, **kwargs):
        messages = args[2]
        assert messages[0]["content"][0]["type"] == "image_url"
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "Anvil Serving Dashboard. GPU: RTX 5090."},
            }]
        }, 0.01

    monkeypatch.setattr(pf, "chat", fake_chat)
    evidence = []
    ok, detail = pf.t_multimodal(
        "http://127.0.0.1:30000/v1",
        "candidate",
        None,
        "data:image/png;base64,AA==",
        {"bytes": 1, "mime": "image/png", "sha256": "a" * 64},
        ["Anvil Serving Dashboard", "Error 503"],
        check="ocr",
        evidence=evidence,
    )

    assert ok is False
    assert "Error 503" in detail
    assert evidence[0]["image"]["sha256"] == "a" * 64


def test_preflight_multimodal_selection_requires_image_and_expectations(tmp_path):
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "candidate",
            "--checks", "image,ocr",
            "--dry-run",
        ])
    assert exc.value.code == 2


def test_video_check_uses_official_openai_video_url_shape(monkeypatch):
    def fake_chat(*args, **kwargs):
        messages = args[2]
        assert messages[0]["content"][0] == {
            "type": "video_url",
            "video_url": {"url": "data:video/mp4;base64,AA=="},
        }
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "The light changes from red to green."},
            }]
        }, 0.01

    monkeypatch.setattr(pf, "chat", fake_chat)
    evidence = []
    ok, detail = pf.t_video(
        "http://127.0.0.1:30000/v1",
        "candidate",
        None,
        "data:video/mp4;base64,AA==",
        {"bytes": 1, "mime": "video/mp4", "sha256": "b" * 64},
        ["red", "green"],
        evidence=evidence,
    )

    assert ok is True
    assert "all expected text present" in detail
    assert evidence[0]["video"]["sha256"] == "b" * 64


def test_preflight_video_selection_requires_video_and_expectations():
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "candidate",
            "--checks", "video",
            "--dry-run",
        ])
    assert exc.value.code == 2


def test_failed_batch_attempt_keeps_index_elapsed_and_safe_detail(monkeypatch, tmp_path, capsys):
    from http.client import RemoteDisconnected
    from threading import local

    state = local()
    original = pf.t_tool_one
    credential = "synthetic-test-credential"
    monkeypatch.setenv("PREFLIGHT_TEST_AUTH", credential)

    def indexed(*args, **kwargs):
        state.index = args[8]
        state.ticks = 0
        return original(*args, **kwargs)

    def clock():
        state.ticks += 1
        return 100 + (state.ticks - 1) * 1.25

    def chat(*args, **kwargs):
        assert args[3] == credential
        assert kwargs["timeout"] == 17
        if state.index == 13:
            raise RemoteDisconnected(
                credential + " Authorization: Bearer synthetic-other-secret " + "x" * 3000
            )
        return {"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"function": {
                "name": "get_weather", "arguments": '{"city":"Oakland"}',
            }}],
        }}]}, 0.5

    monkeypatch.setattr(pf, "t_tool_one", indexed)
    monkeypatch.setattr(pf, "chat", chat)
    monkeypatch.setattr(pf.time, "perf_counter", clock)
    target = tmp_path / "preflight.json"
    assert pf.main([
        "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
        "--checks", "tools", "--tool-batch", "20", "--timeout", "17",
        "--api-key-env", "PREFLIGHT_TEST_AUTH", "--output", str(target),
    ]) == 1
    raw = target.read_text(encoding="utf-8")
    artifact = json.loads(raw)
    rows = artifact["observations"]
    assert len(rows) == 20
    assert sorted(row["request_index"] for row in rows) == list(range(20))
    failed = [row for row in rows if row.get("status") == "request_failed"]
    assert len(failed) == 1
    assert failed[0] == {
        "test": "tools", "seconds": 1.25, "passed": False,
        "status": "request_failed", "request_index": 13,
        "error_type": "RemoteDisconnected",
        "error_message": failed[0]["error_message"], "error_message_truncated": True,
    }
    assert len(failed[0]["error_message"]) == 2048
    assert "<redacted>" in failed[0]["error_message"]
    assert artifact["passed"] is False
    assert artifact["evidence_policy"]["errors"] == []
    assert artifact["results"][0]["detail"].startswith("19/20 clean")
    output = capsys.readouterr().out
    assert credential not in raw + output
    assert "synthetic-other-secret" not in raw + output


@pytest.mark.parametrize("probe,request_name,test", [
    (pf.t_streaming_tool, "chat_stream", "streaming-tools"),
    (pf.t_responses, "responses_request", "responses"),
])
def test_protocol_request_failures_retain_observation(monkeypatch, probe, request_name, test):
    def fail(*args, **kwargs):
        raise TimeoutError("request timed out")

    ticks = iter([5.0, 7.5])
    monkeypatch.setattr(pf.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(pf, request_name, fail)
    evidence = []
    passed, detail = probe("http://127.0.0.1:30000/v1", "candidate", None, evidence=evidence)
    assert passed is False
    assert detail == "error: request timed out"
    assert evidence == [{
        "test": test, "seconds": 2.5, "passed": False, "status": "request_failed",
        "error_type": "TimeoutError", "error_message": "request timed out",
        "error_message_truncated": False,
    }]


def test_continuation_failure_keeps_initial_response_and_exact_stage(monkeypatch):
    calls = 0

    def chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionResetError("peer reset")
        return {"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "call-1", "function": {
                "name": "get_weather", "arguments": '{"city":"Oakland"}',
            }}],
        }}]}, 0.1

    monkeypatch.setattr(pf, "chat", chat)
    evidence = []
    passed, _ = pf.t_tool_result(
        "http://127.0.0.1:30000/v1", "candidate", None, evidence=evidence,
    )
    assert passed is False
    assert calls == 2
    assert len(evidence) == 2
    assert evidence[0]["test"] == "tool-result-initial"
    assert evidence[0]["passed"] is True
    assert evidence[1]["test"] == "tool-result-continuation"
    assert evidence[1]["error_type"] == "ConnectionResetError"
    assert "finish_reason" not in evidence[1]


def test_validation_error_does_not_duplicate_or_mislabel_request(monkeypatch):
    monkeypatch.setattr(pf, "chat", lambda *args, **kwargs: ({"choices": [{
        "finish_reason": "stop", "message": {"content": "not JSON"},
    }]}, 0.1))
    evidence = []
    passed, _ = pf.t_json(
        "http://127.0.0.1:30000/v1", "candidate", None, evidence=evidence,
    )
    assert passed is False
    assert len(evidence) == 1
    assert evidence[0]["finish_reason"] == "stop"
    assert "error_type" not in evidence[0]
