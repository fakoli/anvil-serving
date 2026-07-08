"""Tests for `anvil-serving benchmark` — the request-replay throughput probe.

Covers the two dogfooding bugs:
  BUG 1: small-context serves 400 because sampled/rendered ctx ignores max_model_len.
  BUG 2: thinking-by-default models report a FALSE 0 tok/s with no chat_template_kwargs.

Pure / no network: we exercise the clamp + body-builder seams directly, and inject a
fake urlopen for the /v1/models probe.
"""
import io
import json
import random

import pytest

from anvil_serving import benchmark as bm


# ---- BUG 1: context clamp keeps prompts under a small serve's max_model_len -------

def test_ctx_cap_leaves_headroom_below_max_model_len():
    cap = bm.ctx_cap(16384, 64, bm.DEFAULT_CTX_MARGIN)
    assert cap == 16384 - 64 - bm.DEFAULT_CTX_MARGIN
    assert cap < 16384


def test_ctx_cap_none_when_no_limit_known():
    # 0 / None max_model_len -> no clamp -> legacy behavior preserved.
    assert bm.ctx_cap(0, 64) is None
    assert bm.ctx_cap(None, 64) is None


def test_clamp_ctx_is_noop_without_cap():
    # When no cap is in effect, the sampled/fixed ctx passes through unchanged.
    assert bm.clamp_ctx(262144, None) == 262144


def test_sampled_and_fixed_ctx_never_exceed_max_model_len_minus_headroom():
    max_model_len, max_tokens, margin = 16384, 64, bm.DEFAULT_CTX_MARGIN
    cap = bm.ctx_cap(max_model_len, max_tokens, margin)
    headroom_limit = max_model_len - max_tokens - margin

    rng = random.Random(0)
    for _ in range(2000):
        # mirror main(): sample the measured distribution, then clamp.
        r = rng.random()
        raw = next((v for p, v in bm.SUBAGENT_CTX if r <= p), 262144)
        ctx = bm.clamp_ctx(raw, cap)
        assert ctx <= headroom_limit
        assert ctx < max_model_len

    # a huge fixed --ctx-tokens is clamped too.
    assert bm.clamp_ctx(262144, cap) <= headroom_limit


def test_make_prompt_truncates_to_keep_real_tokens_under_cap():
    cap = bm.ctx_cap(16384, 64, bm.DEFAULT_CTX_MARGIN)
    # an oversized request that would otherwise blow past the window
    prompt = bm.make_prompt("shared prefix", ctx_tokens=131072, uniq=0, max_prompt_tokens=cap)
    assert bm.est_tokens(prompt) <= cap
    assert cap is not None and bm.est_tokens(prompt) < 16384


def test_make_prompt_unclamped_behavior_preserved():
    # No cap -> no truncation, keeps the trailing instruction (legacy behavior).
    prompt = bm.make_prompt("shared prefix", ctx_tokens=4000, uniq=7)
    assert prompt.endswith("summarize the above in one line.")
    assert "request 7" in prompt


def test_default_16384_serve_does_not_overflow_window():
    # End-to-end of the sizing path main() takes for a 16384-ctx serve.
    cap = bm.ctx_cap(16384, 64, bm.DEFAULT_CTX_MARGIN)
    shared = (bm.FILLER % (0, 0)) * max(1, int(8000 * 0.75) // 6)
    for ctx in (16000, 32768, 65536, 131072, 262144):
        ctx = bm.clamp_ctx(ctx, cap)
        prompt = bm.make_prompt(shared, ctx, 0, max_prompt_tokens=cap)
        assert bm.est_tokens(prompt) <= cap < 16384


# ---- BUG 2: --no-thinking injects enable_thinking:false into the request body ------

def test_no_thinking_puts_enable_thinking_false_in_body():
    ctk = {"enable_thinking": False}  # exactly what main() builds for --no-thinking
    body = bm.build_body("m", "hi", 64, chat_template_kwargs=ctk)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_body_has_no_chat_template_kwargs_by_default():
    body = bm.build_body("m", "hi", 64)
    assert "chat_template_kwargs" not in body
    assert body["stream"] is True and body["max_tokens"] == 64


# ---- bonus: /v1/models auto-detect of max_model_len --------------------------------

def _fake_urlopen(payload):
    class _CM:
        def __enter__(self):
            return io.BytesIO(json.dumps(payload).encode())

        def __exit__(self, *a):
            return False

    return lambda req, timeout=15: _CM()


def test_detect_max_model_len_reads_model_card(monkeypatch):
    payload = {"object": "list", "data": [{"id": "coder", "max_model_len": 16384}]}
    monkeypatch.setattr(bm.urllib.request, "urlopen", _fake_urlopen(payload))
    assert bm.detect_max_model_len("http://x/v1", "coder") == 16384


def test_detect_max_model_len_returns_none_on_error(monkeypatch):
    def boom(req, timeout=15):
        raise OSError("connection refused")

    monkeypatch.setattr(bm.urllib.request, "urlopen", boom)
    assert bm.detect_max_model_len("http://x/v1", "coder") is None


def test_benchmark_artifact_json_out_writes_summary_and_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr(bm, "stream_chat",
                        lambda *a, **k: dict(
                            ttft=0.1,
                            e2e=0.2,
                            out_toks=8,
                            usage={"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 40}},
                        ))

    out = tmp_path / "benchmark.json"
    rc = bm.main([
        "--base-url", "http://127.0.0.1:30002/v1",
        "--model", "local-heavy",
        "--requests", "2",
        "--concurrency", "2",
        "--max-model-len", "131072",
        "--json-out", str(out),
    ])

    assert rc in (0, None)
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["schema"] == "anvil-serving.benchmark/v1"
    assert summary["model"] == "local-heavy"
    assert summary["requests"] == 2
    assert summary["completed"] == 2
    assert summary["metrics"]["ttft_p50_ms"] == 100.0
    assert summary["metrics"]["e2e_p50_ms"] == 200.0
    assert summary["metrics"]["throughput_tok_s"] > 0
    assert summary["metrics"]["output_tokens"] == 16
    assert summary["metrics"]["prefix_cache_hit_avg"] == 0.4


# ---- GENERATE: benchmarking a serve ALSO records its recipe (--recipe-out) ---------
# Hermetic: capture_from_container / capture_hardware are injected as fakes, so no
# real docker / GPU / network is touched. The emitted block is proven parseable.
import argparse       # noqa: E402
import tomllib        # noqa: E402


def _recipe_args(**over):
    base = dict(
        model="local-heavy", recipe_model=None, recipe_status="verified",
        recipe_from_container="heavy-serve", recipe_intent="flexibility,quality",
        recipe_mode="flexibility", recipe_out="-",
    )
    base.update(over)
    return argparse.Namespace(**base)


_STUB_SUMMARY = {
    "run_id": "benchmark-20260703T000000Z",
    "max_context_tokens": 131072,
    "context_tokens": None,
    "metrics": {"throughput_tok_s": 183.24, "ttft_p50_ms": 412.7},
}

_FAKE_CAP = {
    "serve": {
        "engine": "vllm",
        "image": "vllm/vllm-openai:nightly",
        "port": 30002,
        "env": ["FLASHINFER_CUDA_ARCH_LIST=12.0f"],
        "flags": ["--kv-cache-dtype fp8"],
    },
    "hardware": {"gpu_uuid": "GPU-d0f446cf-1771-414c-e116-a39138798a8c"},
}


def _fake_capture(name):
    assert name == "heavy-serve"
    return _FAKE_CAP


def _fake_hardware(gpu_uuid=None):
    assert gpu_uuid == "GPU-d0f446cf-1771-414c-e116-a39138798a8c"
    return {"gpu": "NVIDIA RTX PRO 6000 Blackwell Max-Q", "vram_total_gb": 96}


def test_build_recipe_assembles_measured_serve_and_intent():
    r = bm.build_recipe(_recipe_args(), _STUB_SUMMARY,
                        capture=_fake_capture, hardware=_fake_hardware)
    assert r["model"] == "local-heavy"  # defaults to --model
    assert r["status"] == "verified"
    assert r["hardware"] == {
        "gpu": "NVIDIA RTX PRO 6000 Blackwell Max-Q",
        "vram_total_gb": 96,
        "gpu_uuid": "GPU-d0f446cf-1771-414c-e116-a39138798a8c",
    }
    assert r["serve"]["image"] == "vllm/vllm-openai:nightly"
    assert r["serve"]["context_tokens"] == 131072
    assert r["measured"]["throughput_single_tok_s"] == 183.2  # rounded from THIS run
    assert r["measured"]["context_tokens"] == 131072
    assert r["intent"] == {"suited": ["flexibility", "quality"], "mode": "flexibility"}


def test_build_recipe_labels_concurrent_throughput_as_aggregate():
    """At concurrency>1, throughput_tok_s is an AGGREGATE across streams — it must NOT
    be recorded under the single-stream field the registry treats as its headline
    (critic SHOULD-FIX: default benchmark concurrency is 20)."""
    summary = dict(_STUB_SUMMARY, concurrency=20)
    r = bm.build_recipe(_recipe_args(), summary,
                        capture=_fake_capture, hardware=_fake_hardware)
    m = r["measured"]
    assert "throughput_single_tok_s" not in m
    assert m["throughput_aggregate_tok_s"] == 183.2
    assert m["concurrency"] == 20


def test_recipe_model_overrides_model_field():
    r = bm.build_recipe(_recipe_args(recipe_model="openai/gpt-oss-120b"), _STUB_SUMMARY,
                        capture=_fake_capture, hardware=_fake_hardware)
    assert r["model"] == "openai/gpt-oss-120b"


def test_emit_recipe_to_stdout_is_a_parseable_recipe_block(capsys):
    bm.emit_recipe(_recipe_args(recipe_out="-"), _STUB_SUMMARY,
                   capture=_fake_capture, hardware=_fake_hardware)
    block = capsys.readouterr().out
    assert block.startswith("[[recipe]]")
    parsed = tomllib.loads("schema='x'\n" + block)["recipe"][0]
    assert parsed["model"] == "local-heavy"
    assert parsed["measured"]["throughput_single_tok_s"] == 183.2
    # the reconstructed docker run works off exactly this captured block.
    cmd = bm._serve_recipes().reconstruct_docker_run(parsed)
    assert "vllm/vllm-openai:nightly local-heavy --kv-cache-dtype fp8" in cmd


def test_emit_recipe_appends_to_file(tmp_path):
    reg = tmp_path / "serve-recipes.toml"
    reg.write_text('schema = "v1"\n', encoding="utf-8")
    bm.emit_recipe(_recipe_args(recipe_out=str(reg)), _STUB_SUMMARY,
                   capture=_fake_capture, hardware=_fake_hardware)
    data = tomllib.loads(reg.read_text(encoding="utf-8"))
    assert data["recipe"][0]["model"] == "local-heavy"


def test_main_recipe_out_end_to_end_is_hermetic(monkeypatch, capsys):
    """`benchmark ... --recipe-out -` through main(): the benchmark itself is stubbed
    (fake stream_chat, no /v1/models probe) and capture is faked -> ZERO network."""
    from anvil_serving import serve_recipes as sr

    monkeypatch.setattr(bm, "stream_chat",
                        lambda *a, **k: dict(ttft=0.1, e2e=0.2, out_toks=64, usage=None))
    monkeypatch.setattr(sr, "capture_from_container", _fake_capture)
    monkeypatch.setattr(sr, "capture_hardware", _fake_hardware)

    rc = bm.main([
        "--base-url", "http://127.0.0.1:30002/v1", "--model", "local-heavy",
        "--requests", "2", "--concurrency", "2", "--max-model-len", "131072",
        "--recipe-out", "-", "--recipe-from-container", "heavy-serve",
        "--recipe-intent", "flexibility", "--recipe-mode", "flexibility",
    ])
    assert rc in (0, None)
    out = capsys.readouterr().out
    assert "[[recipe]]" in out
    block = out[out.index("[[recipe]]"):]
    parsed = tomllib.loads("schema='x'\n" + block)["recipe"][0]
    assert parsed["model"] == "local-heavy"
    assert parsed["intent"]["mode"] == "flexibility"


def test_main_incomplete_run_skips_verified_recipe(monkeypatch, tmp_path, capsys):
    outcomes = iter([
        RuntimeError("boom"),
        dict(ttft=0.1, e2e=0.2, out_toks=64, usage=None),
    ])

    def fake_stream_chat(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    recipe_path = tmp_path / "serve-recipes.toml"
    monkeypatch.setattr(bm, "stream_chat", fake_stream_chat)

    with pytest.raises(SystemExit) as exc:
        bm.main([
            "--base-url", "http://127.0.0.1:30002/v1", "--model", "local-heavy",
            "--requests", "2", "--concurrency", "1", "--max-model-len", "131072",
            "--recipe-out", str(recipe_path),
        ])

    assert exc.value.code == 1
    assert not recipe_path.exists()
    captured = capsys.readouterr()
    assert "skipping serve recipe" in captured.err


# ---- Fast-tier bakeoff evidence mode -------------------------------------------

def test_parse_context_targets_rejects_non_positive():
    assert bm.parse_context_targets("32768, 65536") == [32768, 65536]
    with pytest.raises(ValueError):
        bm.parse_context_targets("0")


def test_bakeoff_evidence_records_identity_context_score_and_failures(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_stream_chat(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("second context failed")
        return dict(ttft=0.05, e2e=0.20, out_toks=12, usage=None)

    monkeypatch.setattr(bm, "stream_chat", fake_stream_chat)
    out = tmp_path / "bakeoff.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39010/v1",
        "--model", "qwen36-35b-a3b-nvfp4",
        "--candidate-id", "qwen36-35b-a3b",
        "--config-id", "vllm-nvfp4-32k",
        "--context-targets", "1024,2048",
        "--suite", "chat,context",
        "--max-model-len", "4096",
        "--evidence-out", str(out),
        "--source-recipe", "configs/serve-recipes.toml#qwen36-35b-a3b",
        "--serve-command", "anvil-serving serves up fast-qwen36-35b-a3b",
    ])

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["schema"] == "anvil-serving.fast-tier-bakeoff/v1"
    assert evidence["identity"] == {
        "candidate_id": "qwen36-35b-a3b",
        "config_id": "vllm-nvfp4-32k",
        "model": "qwen36-35b-a3b-nvfp4",
        "base_url": "http://127.0.0.1:39010/v1",
        "started_at": evidence["identity"]["started_at"],
    }
    assert evidence["source_recipe"]["serve_command"].startswith("anvil-serving serves up")
    assert [r["status"] for r in evidence["context"]["targets"]] == ["passed", "failed"]
    assert evidence["score_inputs"]["usable_context_tokens"] == 1024
    assert evidence["score_inputs"]["ttft_p50_ms"] == 50.0
    assert evidence["failures"] == [{
        "suite": "context",
        "target_tokens": 2048,
        "error": "second context failed",
    }]


def test_bakeoff_tool_suite_records_tool_call(monkeypatch, tmp_path):
    def fake_post_chat(*args, **kwargs):
        return {
            "latency_s": 0.12,
            "response": {
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "record_weather_zip", "arguments": '{"zip": "98101"}'},
                        }]
                    }
                }]
            },
        }

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out = tmp_path / "tool.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39012/v1",
        "--model", "glm-4.7-flash",
        "--candidate-id", "glm47-flash",
        "--config-id", "sglang-32k",
        "--suite", "tool",
        "--evidence-out", str(out),
    ])

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["tool"]["status"] == "passed"
    assert evidence["tool"]["checks"][0]["tool_call_count"] == 1
    assert evidence["tool"]["checks"][0]["valid_tool_call_count"] == 1
    assert evidence["tool"]["checks"][0]["arguments"] == {"zip": "98101"}
    assert evidence["failures"] == []


def test_bakeoff_tool_suite_rejects_plain_text_tool_claim(monkeypatch, tmp_path):
    def fake_post_chat(*args, **kwargs):
        return {
            "latency_s": 0.12,
            "response": {
                "choices": [{
                    "message": {"content": "I called record_weather_zip with 98101."}
                }]
            },
        }

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out = tmp_path / "tool-fail.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39012/v1",
        "--model", "glm-4.7-flash",
        "--candidate-id", "glm47-flash",
        "--config-id", "sglang-32k",
        "--suite", "tool",
        "--evidence-out", str(out),
    ])

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["tool"]["status"] == "failed"
    assert evidence["tool"]["checks"][0]["tool_call_count"] == 0
    assert evidence["failures"] == [{
        "suite": "tool",
        "error": "response did not include tool_calls",
    }]


def test_bakeoff_session_intelligence_and_thinking_evidence(monkeypatch, tmp_path):
    responses = iter([
        "RIVER-918",
        "--- a/app.py\n+++ b/app.py\n@@\n-timeout = 30\n+timeout = 45\n retries = 2\n",
        "The three stages exceed the timeout budget, so use a faster LLM or reduce output.",
    ])

    def fake_post_chat(*args, **kwargs):
        assert kwargs["chat_template_kwargs"] == {"enable_thinking": False}
        return {
            "latency_s": 0.10,
            "response": {
                "choices": [{
                    "message": {"content": next(responses)}
                }]
            },
        }

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out = tmp_path / "quality.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39013/v1",
        "--model", "glm-4.7-flash",
        "--candidate-id", "glm47-flash",
        "--config-id", "llamacpp-q6-32k",
        "--suite", "session,intelligence",
        "--thinking-mode", "disabled",
        "--evidence-out", str(out),
    ])

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["session"]["status"] == "passed"
    assert evidence["intelligence"]["status"] == "passed"
    assert evidence["score_inputs"]["session_recall_passed"] is True
    assert evidence["score_inputs"]["intelligence_pass_rate"] == 1.0
    assert evidence["thinking"] == {
        "mode": "disabled",
        "chat_template_kwargs": {"enable_thinking": False},
        "unsupported": False,
    }
    assert evidence["failures"] == []


def test_bakeoff_thinking_unsupported_is_recorded(tmp_path):
    out = tmp_path / "thinking.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39014/v1",
        "--model", "devstral-small-2-24b",
        "--candidate-id", "devstral-small2",
        "--config-id", "vllm-fp8-32k",
        "--suite", "voice",
        "--thinking-mode", "unsupported",
        "--voice-latency-ms", "1234",
        "--evidence-out", str(out),
    ])

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["thinking"] == {
        "mode": "unsupported",
        "chat_template_kwargs": None,
        "unsupported": True,
    }


def test_bakeoff_voice_suite_records_supplied_metrics(tmp_path):
    out = tmp_path / "voice.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39014/v1",
        "--model", "devstral-small-2-24b",
        "--candidate-id", "devstral-small2",
        "--config-id", "vllm-fp8-32k",
        "--suite", "voice",
        "--voice-latency-ms", "1234",
        "--stt-latency-ms", "100",
        "--tts-latency-ms", "300",
        "--evidence-out", str(out),
    ])

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["voice"] == {
        "status": "recorded",
        "stt_latency_ms": 100.0,
        "llm_latency_ms": None,
        "tts_latency_ms": 300.0,
        "total_turn_latency_ms": 1234.0,
    }
    assert evidence["score_inputs"]["voice_latency_ms"] == 1234.0
