"""Tests for `anvil-serving benchmark` — the request-replay throughput probe.

Covers the two dogfooding bugs:
  BUG 1: small-context serves 400 because sampled/rendered ctx ignores max_model_len.
  BUG 2: thinking-by-default models report a FALSE 0 tok/s with no chat_template_kwargs.

Pure / no network: we exercise the clamp + body-builder seams directly, and inject a
fake urlopen for the /v1/models probe.
"""
import hashlib
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


def test_make_prompt_unclamped_still_sizes_to_target():
    # No cap known -> still sized to the TARGET (BUG 3: an uncapped prompt used to
    # balloon ~2x past the target and 400 on serves that don't advertise max_model_len,
    # e.g. the recorded llama.cpp context-row failure). Trailing instruction survives.
    prompt = bm.make_prompt("shared prefix", ctx_tokens=4000, uniq=7)
    assert prompt.endswith("summarize the above in one line.")
    assert "request 7" in prompt
    assert abs(bm.est_tokens(prompt) - 4000) <= 400


def test_make_prompt_sub_window_targets_scale_with_target():
    """BUG 3: with a 262144 window, --context-targets 131072,262144 sent IDENTICAL
    ~window-sized prompts (candidate-qwen36-27b-mtp-vllm-nvfp4-262k.bakeoff.json
    records usage.prompt_tokens=261949 on BOTH rows) because filler was sized by a
    word heuristic that under-counts ~2x and then truncated at the WINDOW cap, not
    the per-target size. Each target must now produce its own prompt size."""
    cap = bm.ctx_cap(262144, 64, bm.DEFAULT_CTX_MARGIN)
    shared = (bm.FILLER % (0, 0)) * max(1, int(8000 * 0.75) // 6)
    prompts = {}
    for target in (131072, 262144):
        clamped = bm.clamp_ctx(target, cap)
        prompt = bm.make_prompt(shared, clamped, target, max_prompt_tokens=cap)
        # estimated size lands within 10% of the clamped per-target budget
        assert abs(bm.est_tokens(prompt) - clamped) <= 0.10 * clamped
        # the shared prefix must survive verbatim at the START (prefix-cache contract)
        assert prompt.startswith(shared)
        prompts[target] = prompt
    assert len(prompts[131072]) < 0.6 * len(prompts[262144])


def test_make_prompt_tiny_target_never_returns_whole_prefix():
    # A target smaller than the tail line must not flip the truncation slice index
    # negative (s[:-k] keeps everything BUT k chars — i.e. ~the whole 68k prefix).
    shared = (bm.FILLER % (0, 0)) * 1000
    prompt = bm.make_prompt(shared, 5, 0)
    assert len(prompt) < 100


def test_make_prompt_calibrated_chars_per_token_resizes():
    # A calibrated (larger) chars/token rate must grow the char budget so the REAL
    # token count lands on target instead of ~15% under the conservative default.
    default = bm.make_prompt("x", 4000, 0)
    calibrated = bm.make_prompt("x", 4000, 0, chars_per_token=3.5)
    assert len(default) == pytest.approx(4000 * 3.0, abs=100)
    assert len(calibrated) == pytest.approx(4000 * 3.5, abs=100)


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


def test_reasoning_effort_uses_top_level_openai_field():
    body = bm.build_body("m", "hi", 640, reasoning_effort="high")
    assert body["reasoning_effort"] == "high"
    assert "chat_template_kwargs" not in body


def test_deterministic_regex_check_tolerates_harmless_answer_spacing():
    result = bm.evaluate_text_checks(
        "The answer is **FINAL = D**.",
        [{"name": "answer", "matches_regex": r"\bFINAL\s*=\s*D\b"}],
    )
    assert result == [{"name": "answer", "passed": True}]


def test_final_answer_regex_rejects_conflicting_later_marker():
    check = {"name": "answer", "matches_regex": r"\bFINAL\s*=\s*B\b[*]*\s*$"}
    assert bm.evaluate_text_checks("FINAL=B is not correct; FINAL=C", [check]) == [
        {"name": "answer", "passed": False}
    ]


def test_failure_class_distinguishes_visible_answer_budget_exhaustion():
    observation = {
        "content": "partial explanation without a final",
        "finish_reason": "length",
        "reasoning_chars": 0,
        "reasoning_tokens": None,
    }
    assert bm._failure_class(observation, checks_passed=False) == (
        "visible_answer_budget_exhausted"
    )

    observation["reasoning_chars"] = 400
    assert bm._failure_class(observation, checks_passed=False) == (
        "completion_budget_exhausted_after_visible_output"
    )


def test_response_observation_prefers_nonempty_reasoning_alias_and_ignores_whitespace_content():
    observation = bm.response_observation({
        "choices": [{
            "finish_reason": "length",
            "message": {
                "content": "  \n",
                "reasoning": "",
                "reasoning_content": "actual hidden reasoning",
            },
        }],
    })
    assert observation["reasoning_field"] == "reasoning_content"
    assert observation["reasoning_chars"] == len("actual hidden reasoning")
    assert bm._failure_class(observation, checks_passed=False) == (
        "reasoning_budget_exhausted"
    )


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

    rc = bm.main([
        "--base-url", "http://127.0.0.1:30002/v1", "--model", "local-heavy",
        "--requests", "2", "--concurrency", "1", "--max-model-len", "131072",
        "--recipe-out", str(recipe_path),
    ])

    assert rc == 1
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


def test_bakeoff_context_rows_calibrate_from_usage_and_hit_targets(monkeypatch, tmp_path):
    """The first context row's usage.prompt_tokens calibrates chars/token (here the
    fake serve tokenizes at 3.5 chars/token), so the SECOND row is sized with the
    real rate and its recorded prompt tokens land within 10% of its target —
    instead of both rows sending the same window-sized prompt (BUG 3)."""
    def fake_stream_chat(base, model, prompt, key, max_tokens, timeout=900,
                         chat_template_kwargs=None):
        return dict(ttft=0.05, e2e=0.2, out_toks=8,
                    usage={"prompt_tokens": int(len(prompt) / 3.5)})

    monkeypatch.setattr(bm, "stream_chat", fake_stream_chat)
    out = tmp_path / "calibration.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39010/v1",
        "--model", "qwen36-27b",
        "--candidate-id", "qwen36-27b",
        "--config-id", "vllm-nvfp4-262k",
        "--context-targets", "32768,65536",
        "--suite", "context",
        "--max-model-len", "262144",
        "--evidence-out", str(out),
    ])

    assert rc == 0
    rows = json.loads(out.read_text(encoding="utf-8"))["context"]["targets"]
    assert rows[0]["chars_per_token"] == 3.0  # conservative default, first row
    assert rows[1]["chars_per_token"] == pytest.approx(3.5, abs=0.05)  # calibrated
    # two targets -> meaningfully different prompts; calibrated row hits its target
    assert rows[0]["usage"]["prompt_tokens"] < 0.6 * rows[1]["usage"]["prompt_tokens"]
    assert rows[1]["usage"]["prompt_tokens"] == pytest.approx(65536, rel=0.10)
    # the recorded estimate uses the SAME calibrated rate the prompt was sized with —
    # it is the failure diagnostic, so it must not drift once calibration advances
    # (estimating with the fixed 3.0 constant would report ~76k here, 17% over).
    assert rows[1]["estimated_prompt_tokens"] == pytest.approx(65536, rel=0.10)


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
    assert evidence["thinking"]["mode"] == "disabled"
    assert evidence["thinking"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert evidence["thinking"]["control_mechanism"] == "chat_template_kwargs"
    assert evidence["thinking"]["control_status"] == "requested_unverified"
    assert evidence["thinking"]["unsupported"] is False
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
    assert evidence["thinking"]["mode"] == "unsupported"
    assert evidence["thinking"]["chat_template_kwargs"] is None
    assert evidence["thinking"]["control_status"] == "unsupported"
    assert evidence["thinking"]["unsupported"] is True


# ---- --suite-file: externally-authored eval specs through the check engine -------

_SUITE_SPEC = {
    "suite": "planning-regression",
    "date": "2026-07-11",
    "work_class": "planning",
    "evals": [
        {
            "id": "diff_edit",
            "prompt": "Return a unified diff changing timeout to 45.",
            "max_tokens": 64,
            "checks": [
                {"name": "diff_shape", "contains_all": ["---", "+++"]},
                {"name": "new_value", "contains": "+timeout = 45"},
            ],
        },
        {
            "id": "weather_tool",
            "messages": [{"role": "user", "content": "Record zip 98101."}],
            "tools": [{"type": "function", "function": {"name": "record_weather_zip"}}],
            "expect_tool": {"name": "record_weather_zip", "required_args": {"zip": "98101"}},
            "checks": [],
        },
    ],
}


def _write_spec(tmp_path, spec):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return str(path)


def _suite_main_args(tmp_path, spec_path, out_name="suite-evidence.json"):
    out = tmp_path / out_name
    return out, [
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39015/v1",
        "--model", "glm-4.7-flash",
        "--candidate-id", "glm47-flash",
        "--config-id", "sglang-32k",
        "--suite", "tool",  # built-in suites still run alongside the external spec
        "--suite-file", spec_path,
        "--evidence-out", str(out),
    ]


def test_suite_file_runs_external_evals_into_evidence(monkeypatch, tmp_path):
    seen = []

    def fake_post_chat(base, model, key, messages, max_tokens=128, timeout=120,
                       tools=None, chat_template_kwargs=None):
        seen.append({"messages": messages, "max_tokens": max_tokens, "tools": tools})
        if tools and tools[0]["function"]["name"] == "record_weather_zip":
            message = {"tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "record_weather_zip", "arguments": '{"zip": "98101"}'},
            }]}
        else:
            message = {"content": "--- a/app.py\n+++ b/app.py\n@@\n-timeout = 30\n+timeout = 45\n"}
        return {"latency_s": 0.1, "response": {"choices": [{"message": message}]}}

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    spec_path = _write_spec(tmp_path, _SUITE_SPEC)
    out, argv = _suite_main_args(tmp_path, spec_path)
    rc = bm.main(argv)

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    section = evidence["suites"]["planning-regression"]
    assert section["status"] == "passed"
    assert section["work_class"] == "planning"
    assert section["date"] == "2026-07-11"
    assert section["source_sha256"] == hashlib.sha256(
        (tmp_path / "suite.json").read_bytes()
    ).hexdigest()
    assert [c["status"] for c in section["checks"]] == ["passed", "passed"]
    assert section["checks"][0]["text_checks"] == [
        {"name": "diff_shape", "passed": True},
        {"name": "new_value", "passed": True},
    ]
    assert section["checks"][1]["tool_call"]["valid"] is True
    assert section["checks"][1]["tool_call"]["arguments"] == {"zip": "98101"}
    assert evidence["failures"] == []
    # per-eval validator labels reflect what actually graded the eval
    assert section["checks"][0]["validator"] == "deterministic_text_checks"
    assert section["checks"][1]["validator"] == "tool_call"
    # built-in tool suite ran too (--suite tool) and the external evals forwarded
    # their own max_tokens / tools / messages verbatim (external evals run LAST,
    # after the selected built-in suites).
    assert evidence["tool"]["status"] == "passed"
    external = seen[-2:]
    assert external[0]["max_tokens"] == 64
    assert external[1]["max_tokens"] == 256  # spec default
    assert external[1]["messages"] == [{"role": "user", "content": "Record zip 98101."}]
    assert external[1]["tools"][0]["function"]["name"] == "record_weather_zip"


def test_suite_file_failed_checks_land_in_failures(monkeypatch, tmp_path):
    def fake_post_chat(*args, **kwargs):
        # plain text: fails the diff checks on eval 1 AND the expect_tool on eval 2
        return {"latency_s": 0.1,
                "response": {"choices": [{"message": {"content": "no diff here"}}]}}

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out, argv = _suite_main_args(tmp_path, _write_spec(tmp_path, _SUITE_SPEC))
    rc = bm.main(argv)

    assert rc == 0  # failures are evidence, not a crash
    evidence = json.loads(out.read_text(encoding="utf-8"))
    section = evidence["suites"]["planning-regression"]
    assert section["status"] == "failed"
    assert [c["status"] for c in section["checks"]] == ["failed", "failed"]
    assert "diff_shape" in section["checks"][0]["error"]
    assert section["checks"][1]["tool_call"]["valid"] is False
    suite_failures = [f for f in evidence["failures"] if f["suite"] == "planning-regression"]
    assert [f["eval_id"] for f in suite_failures] == ["diff_edit", "weather_tool"]


def test_suite_file_request_error_is_recorded_per_eval(monkeypatch, tmp_path):
    def fake_post_chat(*args, **kwargs):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    spec = dict(_SUITE_SPEC, evals=[_SUITE_SPEC["evals"][0]])
    out, argv = _suite_main_args(tmp_path, _write_spec(tmp_path, spec))
    rc = bm.main(argv)

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    check = evidence["suites"]["planning-regression"]["checks"][0]
    assert check["status"] == "failed"
    assert check["error"] == "endpoint down"


def test_repaired_suite_classifies_reasoning_budget_exhaustion(monkeypatch, tmp_path):
    spec = _one_eval(
        visible_answer_tokens=128,
        reasoning_headroom_tokens=512,
    )

    def fake_post_chat(*args, **kwargs):
        assert kwargs["max_tokens"] == 640
        assert kwargs["reasoning_effort"] == "high"
        return {
            "latency_s": 0.2,
            "response": {
                "choices": [{
                    "finish_reason": "length",
                    "message": {"content": None, "reasoning": "thinking " * 40},
                }],
                "usage": {
                    "completion_tokens": 640,
                    "completion_tokens_details": {"reasoning_tokens": 640},
                },
            },
        }

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out = tmp_path / "reasoning-exhaustion.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39033/v1",
        "--model", "reasoner",
        "--candidate-id", "reasoner",
        "--config-id", "repaired-v2",
        "--suite-file", _write_spec(tmp_path, spec),
        "--reasoning-effort", "high",
        "--eval-repetitions", "2",
        "--evidence-out", str(out),
    ])

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    check = evidence["suites"]["s"]["checks"][0]
    assert check["status"] == "failed"
    assert check["pass_count"] == 0
    assert len(check["attempts"]) == 2
    for attempt in check["attempts"]:
        assert attempt["content"] == ""
        assert attempt["finish_reason"] == "length"
        assert attempt["reasoning_field"] == "reasoning"
        assert attempt["reasoning_tokens"] == 640
        assert attempt["failure_class"] == "reasoning_budget_exhausted"
        assert attempt["budget"] == {
            "visible_answer_tokens": 128,
            "reasoning_headroom_tokens": 512,
            "max_completion_tokens": 640,
            "legacy_total_budget": False,
        }
    assert evidence["thinking"]["control_mechanism"] == "reasoning_effort"
    assert evidence["thinking"]["reasoning_effort"] == "high"
    assert evidence["evaluation_protocol"]["records_finish_reason"] is True
    assert evidence["failures"][0]["failure_classes"] == ["reasoning_budget_exhausted"]


def test_tool_eval_preserves_reasoning_budget_exhaustion_class(monkeypatch, tmp_path):
    spec = _one_eval(
        checks=[],
        tools=[{"type": "function", "function": {"name": "record_result"}}],
        expect_tool={"name": "record_result", "required_args": {}},
        visible_answer_tokens=64,
        reasoning_headroom_tokens=64,
    )

    def fake_post_chat(*args, **kwargs):
        return {
            "latency_s": 0.1,
            "response": {
                "choices": [{
                    "finish_reason": "length",
                    "message": {"content": "   ", "reasoning_content": "still thinking"},
                }],
                "usage": {"completion_tokens": 128},
            },
        }

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out = tmp_path / "tool-reasoning-exhaustion.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39033/v1",
        "--model", "reasoner",
        "--candidate-id", "reasoner",
        "--config-id", "repaired-v2",
        "--suite-file", _write_spec(tmp_path, spec),
        "--evidence-out", str(out),
    ])

    assert rc == 0
    attempt = json.loads(out.read_text(encoding="utf-8"))["suites"]["s"]["checks"][0]["attempts"][0]
    assert attempt["tool_call"]["valid"] is False
    assert attempt["failure_class"] == "reasoning_budget_exhausted"


def test_repaired_suite_repeats_and_retains_full_visible_answers(monkeypatch, tmp_path):
    answers = iter(["x" + "a" * 300, "wrong", "x" + "b" * 300])

    def fake_post_chat(*args, **kwargs):
        return {
            "latency_s": 0.1,
            "response": {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": next(answers)},
                }]
            },
        }

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out = tmp_path / "repeated.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39033/v1",
        "--model", "candidate",
        "--candidate-id", "candidate",
        "--config-id", "repaired-v2",
        "--suite-file", _write_spec(tmp_path, _one_eval()),
        "--eval-repetitions", "3",
        "--eval-min-pass-rate", "0.66",
        "--evidence-out", str(out),
    ])

    assert rc == 0
    check = json.loads(out.read_text(encoding="utf-8"))["suites"]["s"]["checks"][0]
    assert check["status"] == "passed"
    assert check["pass_count"] == 2
    assert check["pass_rate"] == pytest.approx(2 / 3)
    assert len(check["attempts"][0]["content"]) == 301
    assert len(check["attempts"][0]["content_excerpt"]) == 200
    assert [attempt["finish_reason"] for attempt in check["attempts"]] == [
        "stop", "stop", "stop"
    ]


@pytest.mark.parametrize("extra", [
    ["--visible-answer-tokens", "0"],
    ["--visible-answer-tokens", "65537"],
    ["--reasoning-headroom-tokens", "-1"],
    ["--reasoning-headroom-tokens", "65537"],
    ["--visible-answer-tokens", "32769", "--reasoning-headroom-tokens", "32768"],
    ["--eval-repetitions", "0"],
    ["--eval-repetitions", "21"],
    ["--eval-min-pass-rate", "0"],
    ["--eval-min-pass-rate", "1.1"],
    ["--reasoning-effort", "high", "--thinking-mode", "enabled"],
    ["--reasoning-effort", "low", "--no-thinking"],
])
def test_repaired_eval_cli_rejects_invalid_or_conflicting_controls(extra):
    with pytest.raises(SystemExit) as exc:
        bm.main([
            "--base-url", "http://127.0.0.1:39033/v1",
            "--model", "candidate",
            *extra,
        ])
    assert exc.value.code == 2


@pytest.mark.parametrize("item,extra", [
    ({"visible_answer_tokens": 65536}, ["--reasoning-headroom-tokens", "1"]),
    ({"reasoning_headroom_tokens": 65536}, ["--visible-answer-tokens", "1"]),
])
def test_cli_rejects_resolved_item_budget_that_exceeds_cap(tmp_path, item, extra):
    spec_path = _write_spec(tmp_path, _one_eval(**item))
    with pytest.raises(SystemExit) as exc:
        bm.main([
            "--bakeoff",
            "--base-url", "http://127.0.0.1:39033/v1",
            "--model", "candidate",
            "--candidate-id", "candidate",
            "--config-id", "config",
            "--suite-file", spec_path,
            *extra,
        ])
    assert exc.value.code == 2


def test_suite_file_requires_bakeoff(tmp_path):
    spec_path = _write_spec(tmp_path, _SUITE_SPEC)
    with pytest.raises(SystemExit) as exc:
        bm.main(["--base-url", "http://127.0.0.1:39015/v1", "--model", "m",
                 "--suite-file", spec_path])
    assert exc.value.code == 2


_OK_CHECKS = [{"name": "c", "contains": "x"}]  # valid filler so each case isolates ONE defect


def _one_eval(**over):
    base = {"id": "x", "prompt": "p", "checks": _OK_CHECKS}
    base.update(over)
    return {"suite": "s", "evals": [base]}


@pytest.mark.parametrize("spec", [
    [],                                                       # not an object
    {"evals": [{"id": "x", "prompt": "p", "checks": _OK_CHECKS}]},  # missing suite name
    {"suite": "s", "evals": []},                              # empty evals
    {"suite": "s", "evals": [{"prompt": "p", "checks": _OK_CHECKS}]},  # missing id
    {"suite": "s", "evals": _one_eval()["evals"] * 2},        # duplicate eval ids
    _one_eval(prompt=None),                                   # no prompt/messages
    _one_eval(prompt=""),                                     # empty prompt
    _one_eval(prompt=None, messages=[]),                      # empty messages, no prompt
    _one_eval(messages=["hi"]),                               # messages entries not objects
    _one_eval(max_tokens="64"),                               # max_tokens wrong type
    _one_eval(max_tokens=0),                                  # max_tokens not positive
    _one_eval(max_tokens=True),                               # bool is not an int here
    _one_eval(max_tokens=64, visible_answer_tokens=32),       # ambiguous total vs allocation
    _one_eval(visible_answer_tokens=0),                       # visible allocation not positive
    _one_eval(visible_answer_tokens=True),                    # bool is not an int here
    _one_eval(reasoning_headroom_tokens=-1),                  # headroom cannot be negative
    _one_eval(reasoning_headroom_tokens=True),                # bool is not an int here
    _one_eval(tools={"type": "function"}),                    # tools not a list
    _one_eval(checks=None),                                   # asserts nothing
    _one_eval(checks="contains"),                             # checks not a list
    _one_eval(expect_tool={}, checks=[]),                     # tool w/o name
    _one_eval(expect_tool={"name": 7}, checks=[]),            # tool name not a string
    _one_eval(expect_tool={"name": "f", "required_args": ["zip"]}),   # args not an object
    _one_eval(expect_tool={"name": "f", "required_args": {"zip": 98101}}),  # non-string value
])
def test_suite_file_rejects_malformed_specs(tmp_path, spec):
    with pytest.raises(ValueError):
        bm.load_suite_spec(_write_spec(tmp_path, spec))


@pytest.mark.parametrize("check", [
    "diff_shape",                                    # not an object
    {},                                              # no name, no assertion
    {"name": "diff_shape"},                          # name only -> would ALWAYS pass
    {"name": "diff_shape", "contain_all": ["---"]},  # typo'd key -> would ALWAYS pass
    {"name": "diff_shape", "contains": ""},          # empty needle -> would ALWAYS pass
    {"name": "diff_shape", "contains": ["---"]},     # wrong type for contains
    {"name": "diff_shape", "contains_all": []},      # empty list -> would ALWAYS pass
    {"name": "diff_shape", "contains_all": "---"},   # wrong type (str iterates by char)
    {"name": "diff_shape", "contains_all": ["---", 7]},          # non-string element
    {"name": "diff_shape", "contains": "x", "contains_any": ["y"]},  # two assertion keys
    {"name": "answer", "matches_regex": ""},              # empty regex is vacuous
    {"name": "answer", "matches_regex": "["},             # invalid regex
    {"name": "answer", "matches_regex": "(a+)+$"},        # catastrophic backtracking
    {"name": "answer", "matches_regex": "a|aa"},          # unsafe alternation
    {"name": "answer", "matches_regex": ".*FINAL"},       # wildcard repetition
    {"name": "answer", "matches_regex": "x" * 513},       # unbounded pattern size
    {"name": "", "contains": "x"},                   # empty name
])
def test_suite_file_rejects_vacuous_or_broken_checks(tmp_path, check):
    """evaluate_text_checks defaults ok=True for unknown keys — safe for the trusted
    in-repo prompts, a false-pass hole for external specs (a typo'd 'contain_all'
    check would report passed on EMPTY model output and sail through the notebook's
    no_failures hard gate). The loader must reject every such shape up front."""
    with pytest.raises(ValueError):
        bm.load_suite_spec(_write_spec(tmp_path, _one_eval(checks=[check])))


@pytest.mark.parametrize("overrides", [
    {"max_tokens": 65537},
    {"visible_answer_tokens": 65537},
    {"reasoning_headroom_tokens": 65537},
    {"visible_answer_tokens": 32769, "reasoning_headroom_tokens": 32768},
])
def test_suite_file_rejects_resource_exhausting_budgets(tmp_path, overrides):
    with pytest.raises(ValueError):
        bm.load_suite_spec(_write_spec(tmp_path, _one_eval(**overrides)))


def test_suite_file_rejects_too_many_evals(tmp_path):
    spec = dict(_SUITE_SPEC, evals=[
        dict(_SUITE_SPEC["evals"][0], id="eval-%d" % index)
        for index in range(101)
    ])
    with pytest.raises(ValueError, match="more than 100"):
        bm.load_suite_spec(_write_spec(tmp_path, spec))


def test_cli_rejects_aggregate_quality_token_plan(tmp_path):
    spec = dict(_SUITE_SPEC, evals=[
        _one_eval(id="eval-%d" % index, max_tokens=65536)["evals"][0]
        for index in range(31)
    ])
    with pytest.raises(SystemExit) as exc:
        bm.main([
            "--bakeoff",
            "--base-url", "http://127.0.0.1:39033/v1",
            "--model", "candidate",
            "--candidate-id", "candidate",
            "--config-id", "config",
            "--suite-file", _write_spec(tmp_path, spec),
        ])
    assert exc.value.code == 2


def test_suite_file_without_suite_flag_runs_only_the_external_suite(monkeypatch, tmp_path):
    """--suite-file alone must not fire the default chat/context probe: an unrelated
    probe failure would pollute the external-suite evidence and trip the notebook
    no_failures hard gate."""
    def boom_stream_chat(*args, **kwargs):
        raise AssertionError("built-in chat/context probe must not run")

    def fake_post_chat(*args, **kwargs):
        return {"latency_s": 0.1,
                "response": {"choices": [{"message": {"content": "+timeout = 45 --- +++"}}]}}

    monkeypatch.setattr(bm, "stream_chat", boom_stream_chat)
    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    spec = dict(_SUITE_SPEC, evals=[_SUITE_SPEC["evals"][0]])
    out = tmp_path / "only-external.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39015/v1",
        "--model", "glm-4.7-flash",
        "--candidate-id", "glm47-flash",
        "--config-id", "sglang-32k",
        "--suite-file", _write_spec(tmp_path, spec),
        "--evidence-out", str(out),
    ])

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["suites"]["planning-regression"]["status"] == "passed"
    assert evidence["context"]["targets"] == []
    assert evidence["tool"]["status"] == "not_run"
    assert evidence["session"]["status"] == "not_run"
    assert evidence["intelligence"]["status"] == "not_run"


def test_suite_file_fixture_matches_session_evals_plugin_shape(monkeypatch, tmp_path):
    """Pin compatibility with the session-evals plugin's documented suite.json shape
    (fakoli-plugins plugins/session-evals scripts/eval_emit.py docstring): kebab-case
    suite, all three check kinds, expect_tool with a null required_arg (null =
    'present, non-empty string'), messages-based and prompt-based evals."""
    fixture = str(
        __import__("pathlib").Path(__file__).parent / "fixtures" / "session_evals" / "suite.json"
    )
    spec = bm.load_suite_spec(fixture)
    assert spec["suite"] == "merge-safety"

    def fake_post_chat(base, model, key, messages, max_tokens=128, timeout=120,
                       tools=None, chat_template_kwargs=None):
        if tools:
            message = {"tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "record_weather_zip", "arguments": '{"zip": "10001"}'},
            }]}
        else:
            message = {"content": "Run git fetch then rebase. --- +++ @@"}
        return {"latency_s": 0.1, "response": {"choices": [{"message": message}]}}

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out = tmp_path / "fixture-evidence.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39015/v1",
        "--model", "glm-4.7-flash",
        "--candidate-id", "glm47-flash",
        "--config-id", "sglang-32k",
        "--suite-file", fixture,
        "--evidence-out", str(out),
    ])

    assert rc == 0
    section = json.loads(out.read_text(encoding="utf-8"))["suites"]["merge-safety"]
    assert section["status"] == "passed"
    assert [c["id"] for c in section["checks"]] == ["fetch_before_merge", "diff_review", "zip_tool"]
    assert section["checks"][2]["tool_call"]["valid"] is True


def test_suite_file_malformed_spec_exits_before_any_request(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("no request may be sent for a malformed spec")

    monkeypatch.setattr(bm, "post_chat", boom)
    monkeypatch.setattr(bm, "stream_chat", boom)
    _, argv = _suite_main_args(tmp_path, _write_spec(tmp_path, {"suite": "s", "evals": []}))
    with pytest.raises(SystemExit) as exc:
        bm.main(argv)
    assert exc.value.code == 2


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
    # schema-presence guard: the external-suites key exists even without --suite-file
    assert evidence["suites"] == {}
