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
