#!/usr/bin/env python3
"""benchmark.py - replay the measured request-size distribution against a local endpoint.

Reproduces the Claude Code SUBAGENT (specialist) profile and a fan-out burst, then
reports TTFT, end-to-end latency, throughput, and prefix-cache hit signal.
Default distribution = measured subagent percentiles (ctx p50 55K / p95 159K; gen tiny).

Stdlib only (urllib + threading; streaming for TTFT).

Usage:
  # steady mixed load:
  python3 benchmark.py --base-url http://127.0.0.1:30000/v1 --model coder-specialist \
      --requests 60 --concurrency 20
  # fan-out burst sharing ONE prefix (exercises prefix cache like a workflow wave):
  python3 benchmark.py --base-url ... --model ... --burst 20 --shared-prefix-tokens 8000 --ctx-tokens 64000
"""
import argparse
import json
import os
import time
import random
import statistics
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

FILLER = "def helper_%d():\n    return compute(%d)  # routine specialist context\n"

# Conservative chars-per-token: real code tokenizes to ~3-4 chars/token, so dividing
# char length by this OVER-estimates the token count -> a clamp built on it never lets
# the prompt sneak past the serve's max_model_len.
CHARS_PER_TOKEN = 3.0
# Token headroom left below max_model_len when clamping (absorbs heuristic error +
# template/role overhead so prompt + generation stay inside the context window).
DEFAULT_CTX_MARGIN = 1024

def est_tokens(text):
    """Conservative (slightly high) token estimate for a string, stdlib-only."""
    return int(len(text) / CHARS_PER_TOKEN)

def ctx_cap(max_model_len, max_tokens, margin=DEFAULT_CTX_MARGIN):
    """Largest prompt-token budget that keeps prompt + generation inside the serve's
    context window. Returns None when no limit is known -> no clamp (legacy behavior)."""
    if not max_model_len:
        return None
    return max(256, int(max_model_len) - int(max_tokens) - int(margin))

def clamp_ctx(ctx, cap):
    """Clamp a sampled/fixed ctx-token target down to the serve's usable budget."""
    return ctx if cap is None else min(ctx, cap)

def make_prompt(shared_prefix, ctx_tokens, uniq, max_prompt_tokens=None,
                chars_per_token=CHARS_PER_TOKEN):
    """Build a prompt sized to ~ctx_tokens tokens, never past max_prompt_tokens.

    Sized by ONE char budget from the per-target token count — the old word-count
    heuristic under-counted (the filler tokenizes at ~2.7 tok/word, the heuristic
    assumed 1.33) and the old truncation cut at the WINDOW cap, so every sub-window
    target ballooned to the window (both bakeoff context probes sent identical
    ~262k prompts; see docs/findings/2026-07-10-...-evidence/failures.md §7).
    The default chars_per_token is conservative (real tokens land UNDER budget);
    pass a value calibrated from usage.prompt_tokens to land ON the target.
    """
    # shared_prefix is identical across requests (prefix-cache hit); filler varies
    budget = ctx_tokens if max_prompt_tokens is None else min(ctx_tokens, max_prompt_tokens)
    tail = f"\n# request {uniq}: summarize the above in one line."
    char_budget = int(budget * chars_per_token) - len(shared_prefix) - len(tail) - 1
    if char_budget <= 0:
        # shared prefix ALONE exceeds the budget (tiny window / big --shared-prefix-tokens):
        # front-truncate it — keeps its head for prefix-cache hits, never overflows.
        return shared_prefix[: int(budget * chars_per_token) - len(tail)] + tail
    lines = []
    filled = 0
    i = 0
    while filled < char_budget:
        s = FILLER % (uniq + i, i)
        lines.append(s); filled += len(s); i += 1
    filler = "".join(lines)[:max(0, char_budget)]
    return shared_prefix + "\n" + filler + tail

def build_body(model, prompt, max_tokens, chat_template_kwargs=None):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
            "stream_options": {"include_usage": True}}
    # chat_template_kwargs (e.g. {"enable_thinking": False}) is honored by SGLang/vLLM
    # for Qwen3.x / GLM so thinking-by-default models don't burn the whole token budget
    # on hidden reasoning and emit ZERO content deltas (which the report reads as a FALSE
    # 0 tok/s with TTFT==E2E).
    if chat_template_kwargs:
        body["chat_template_kwargs"] = chat_template_kwargs
    return body

def parse_csv(values, default=None):
    """Parse repeatable/comma-separated CLI values into a flat list of strings."""
    if not values:
        return list(default or [])
    out = []
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if item:
                out.append(item)
    return out

def parse_context_targets(value):
    """Parse `--context-targets 32768,65536` into positive integer targets."""
    if not value:
        return [32768]
    targets = []
    for item in str(value).split(","):
        item = item.strip()
        if not item:
            continue
        target = int(item)
        if target <= 0:
            raise ValueError("context targets must be positive integers")
        targets.append(target)
    return targets or [32768]

BAKEOFF_TOOL = {
    "type": "function",
    "function": {
        "name": "record_weather_zip",
        "description": "Record the ZIP code the user supplied for weather lookup.",
        "parameters": {
            "type": "object",
            "properties": {"zip": {"type": "string"}},
            "required": ["zip"],
        },
    },
}

INTELLIGENCE_PROMPTS = [
    {
        "id": "unified_diff_timeout_edit",
        "prompt": (
            "You are editing app.py. Original file:\n"
            "timeout = 30\n"
            "retries = 2\n\n"
            "Return only a unified diff that changes timeout to 45 and leaves "
            "retries unchanged."
        ),
        "checks": [
            {"name": "diff_shape", "contains_all": ["---", "+++", "@@"]},
            {"name": "removes_old_timeout", "contains": "-timeout = 30"},
            {"name": "adds_new_timeout", "contains": "+timeout = 45"},
        ],
    },
    {
        "id": "parallel_timeout_triage",
        "prompt": (
            "A voice agent calls STT, an LLM, and TTS. The total turn timeout is "
            "2500 ms. Logs show STT=550 ms, LLM=1800 ms, TTS=650 ms. In one "
            "concise sentence, identify the problem and one practical fix."
        ),
        "checks": [
            {
                "name": "identifies_budget_overrun",
                "contains_any": ["timeout", "budget", "overrun", "too slow", "exceeds"],
            },
            {
                "name": "offers_latency_fix",
                "contains_any": ["faster", "reduce", "parallel", "cache", "shorter", "limit"],
            },
        ],
    },
]

SESSION_RECALL_PROMPT = [
    {"role": "user", "content": "Remember this session code: RIVER-918. Reply with ok."},
    {"role": "assistant", "content": "ok"},
    {"role": "user", "content": "What session code should be used? Reply with only the code."},
]

def _choice_messages(response):
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list):
        return []
    messages = []
    for choice in choices:
        if isinstance(choice, dict) and isinstance(choice.get("message"), dict):
            messages.append(choice["message"])
    return messages

def _message_text(message):
    content = message.get("content") if isinstance(message, dict) else ""
    return content if isinstance(content, str) else ""

def validate_function_tool_call(message, expected_name, required_args):
    """Return a schema/usefulness result for one OpenAI-compatible tool call."""
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not tool_calls:
        return {
            "valid": False,
            "error": "response did not include tool_calls",
            "arguments": None,
        }

    first = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
    function = first.get("function") if isinstance(first, dict) else {}
    if not isinstance(function, dict):
        return {"valid": False, "error": "tool_call missing function object", "arguments": None}
    if function.get("name") != expected_name:
        return {
            "valid": False,
            "error": "wrong function name: %r" % function.get("name"),
            "arguments": None,
        }

    raw_args = function.get("arguments")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except Exception as exc:
        return {"valid": False, "error": "arguments are not valid JSON: %s" % exc, "arguments": None}
    if not isinstance(args, dict):
        return {"valid": False, "error": "arguments are not a JSON object", "arguments": None}

    for key, expected_value in required_args.items():
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            return {
                "valid": False,
                "error": "missing required string argument: %s" % key,
                "arguments": args,
            }
        if expected_value is not None and value.strip() != expected_value:
            return {
                "valid": False,
                "error": "wrong argument %s: %r" % (key, value),
                "arguments": args,
            }

    return {"valid": True, "error": None, "arguments": args}

def evaluate_text_checks(content, checks):
    """Deterministic text checks; this never asks the candidate to grade itself."""
    normalized = content.lower()
    results = []
    for check in checks:
        ok = True
        if "contains" in check:
            ok = check["contains"].lower() in normalized
        elif "contains_all" in check:
            ok = all(item.lower() in normalized for item in check["contains_all"])
        elif "contains_any" in check:
            ok = any(item.lower() in normalized for item in check["contains_any"])
        results.append({"name": check["name"], "passed": ok})
    return results

def resolve_thinking_settings(args):
    """Resolve CLI thinking flags into request kwargs plus evidence metadata."""
    mode = getattr(args, "thinking_mode", None) or "default"
    if getattr(args, "no_thinking", False):
        mode = "disabled"

    if mode == "enabled":
        kwargs = {"enable_thinking": True}
    elif mode == "disabled":
        kwargs = {"enable_thinking": False}
    else:
        kwargs = None

    return kwargs, {
        "mode": mode,
        "chat_template_kwargs": kwargs,
        "unsupported": mode == "unsupported",
    }

def post_chat(base, model, key, messages, max_tokens=128, timeout=120,
              tools=None, chat_template_kwargs=None):
    """Non-streaming OpenAI-compatible chat call for smoke/tool probes."""
    url = base.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if chat_template_kwargs:
        body["chat_template_kwargs"] = chat_template_kwargs
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return {"latency_s": time.time() - t0, "response": data}

def detect_max_model_len(base, model=None, key=None, timeout=15):
    """Best-effort probe of <base>/models for the serve's context window. SGLang & vLLM
    expose it on the model card. Returns None on any problem so callers fall back to
    current (unclamped) behavior."""
    url = base.rstrip("/") + "/models"
    headers = {}
    if key: headers["Authorization"] = "Bearer " + key
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    entries = data.get("data") if isinstance(data, dict) else None
    if not entries:
        return None
    chosen = None
    for e in entries:
        if not isinstance(e, dict):
            continue
        if model and e.get("id") == model:
            chosen = e; break
        if chosen is None:
            chosen = e
    if not isinstance(chosen, dict):
        return None
    for k in ("max_model_len", "max_context_length", "context_length"):
        v = chosen.get(k)
        if isinstance(v, int) and v > 0:
            return v
    return None

def resolve_api_key(api_key_env=None):
    """Resolve auth for probes from an environment variable reference."""
    if api_key_env:
        return os.environ.get(api_key_env)
    return None

def stream_chat(base, model, prompt, key, max_tokens, timeout=900, chat_template_kwargs=None):
    url = base.rstrip("/") + "/chat/completions"
    body = build_body(model, prompt, max_tokens, chat_template_kwargs)
    headers = {"Content-Type": "application/json"}
    if key: headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time(); ttft = None; out_toks = 0; usage = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"): continue
            data = line[5:].strip()
            if data == "[DONE]": break
            try: chunk = json.loads(data)
            except Exception: continue
            if chunk.get("usage"): usage = chunk["usage"]
            for ch in chunk.get("choices", []):
                delta = ch.get("delta", {})
                if delta.get("content"):
                    if ttft is None: ttft = time.time() - t0
                    out_toks += 1
    return dict(ttft=ttft if ttft is not None else (time.time()-t0),
                e2e=time.time()-t0, out_toks=out_toks, usage=usage)

def cached_fraction(usage):
    if not usage: return None
    det = usage.get("prompt_tokens_details") or {}
    cached = det.get("cached_tokens")
    pt = usage.get("prompt_tokens")
    if cached is None or not pt: return None
    return cached / pt

def pctile(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return 0
    i = min(len(xs)-1, int(round((len(xs)-1)*q/100)))
    return xs[i]

def _result_metrics(results):
    ttfts = [r.get("ttft") for r in results if isinstance(r, dict)]
    e2es = [r.get("e2e") for r in results if isinstance(r, dict)]
    out_tot = sum(r.get("out_toks") or 0 for r in results if isinstance(r, dict))
    return {
        "ttft_p50_ms": pctile(ttfts, 50) * 1000.0,
        "ttft_p95_ms": pctile(ttfts, 95) * 1000.0,
        "e2e_p50_ms": pctile(e2es, 50) * 1000.0,
        "e2e_p95_ms": pctile(e2es, 95) * 1000.0,
        "output_tokens": out_tot,
    }

# measured subagent ctx percentiles (from role_split): rough inverse-CDF sampler
SUBAGENT_CTX = [(0.0,16000),(0.218,32768),(0.602,65536),(0.906,131072),(0.995,262144)]
def sample_ctx():
    r = random.random()
    for p, v in SUBAGENT_CTX:
        if r <= p: return v
    return 262144

def run_bakeoff(a, api_key):
    """Run selected bakeoff suites against one already-loaded endpoint.

    This mode intentionally never starts, stops, unloads, or reloads models. It
    only sends OpenAI-compatible requests to the supplied base URL and records
    both successful sub-checks and failures in one JSON artifact.
    """
    started_at = time.time()
    suites = parse_csv(a.suite, default=["chat"])
    context_targets = parse_context_targets(a.context_targets)
    max_model_len = a.max_model_len or detect_max_model_len(a.base_url, a.model, api_key)
    cap = ctx_cap(max_model_len, a.max_tokens, a.margin)
    ctk, thinking_section = resolve_thinking_settings(a)
    failures = []
    chat_results = []
    context_results = []

    should_run_context = "chat" in suites or "context" in suites
    if should_run_context:
        shared = (FILLER % (0, 0)) * max(1, int(a.shared_prefix_tokens * 0.75) // 6)
        chars_per_token = CHARS_PER_TOKEN
        for target in context_targets:
            clamped = clamp_ctx(target, cap)
            prompt = make_prompt(
                shared, clamped, target, max_prompt_tokens=cap,
                chars_per_token=chars_per_token,
            )
            row = {
                "target_tokens": target,
                "clamped_tokens": clamped,
                "attempted_context_tokens": clamped,
                "estimated_prompt_tokens": est_tokens(prompt),
                "chars_per_token": round(chars_per_token, 3),
                "status": "pending",
            }
            try:
                result = stream_chat(
                    a.base_url, a.model, prompt, api_key, a.max_tokens,
                    timeout=a.timeout, chat_template_kwargs=ctk,
                )
                row.update({
                    "status": "passed",
                    "ttft_ms": result["ttft"] * 1000.0,
                    "e2e_ms": result["e2e"] * 1000.0,
                    "output_tokens": result["out_toks"],
                    "usage": result.get("usage"),
                })
                chat_results.append(result)
                # Calibrate sizing from the serve's REAL tokenizer count so later
                # targets land ON target instead of ~15% under the conservative default.
                usage = result.get("usage") or {}
                if usage.get("prompt_tokens"):
                    measured = len(prompt) / usage["prompt_tokens"]
                    if 1.0 <= measured <= 10.0:  # ignore bogus usage
                        chars_per_token = measured
            except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                row.update({"status": "failed", "error": str(exc)})
                failures.append({
                    "suite": "context" if "context" in suites else "chat",
                    "target_tokens": target,
                    "error": str(exc),
                })
            context_results.append(row)

    tool_section = {"status": "not_run", "checks": []}
    if "tool" in suites:
        check = {
            "name": "openai_tool_call_smoke",
            "status": "pending",
            "expected_function": "record_weather_zip",
            "expected_arguments": {"zip": "98101"},
        }
        try:
            result = post_chat(
                a.base_url,
                a.model,
                api_key,
                [{"role": "user", "content": "Call record_weather_zip with zip 98101."}],
                max_tokens=128,
                timeout=a.timeout,
                tools=[BAKEOFF_TOOL],
                chat_template_kwargs=ctk,
            )
            messages = _choice_messages(result.get("response", {}))
            validations = [
                validate_function_tool_call(
                    message, "record_weather_zip", {"zip": "98101"}
                )
                for message in messages
            ]
            valid = [item for item in validations if item["valid"]]
            check.update({
                "status": "passed" if valid else "failed",
                "latency_ms": result["latency_s"] * 1000.0,
                "tool_call_count": sum(len(message.get("tool_calls") or []) for message in messages),
                "valid_tool_call_count": len(valid),
                "arguments": valid[0]["arguments"] if valid else None,
                "validation_errors": [item["error"] for item in validations if item["error"]],
            })
            if not valid:
                check["error"] = check["validation_errors"][0] if check["validation_errors"] else (
                    "response did not include valid tool_calls"
                )
                failures.append({"suite": "tool", "error": check["error"]})
        except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
            check.update({"status": "failed", "error": str(exc)})
            failures.append({"suite": "tool", "error": str(exc)})
        tool_section = {"status": check["status"], "checks": [check]}

    session_section = {"status": "not_run", "checks": []}
    if "session" in suites:
        check = {"name": "single_request_multiturn_recall", "status": "pending"}
        try:
            result = post_chat(
                a.base_url,
                a.model,
                api_key,
                SESSION_RECALL_PROMPT,
                max_tokens=64,
                timeout=a.timeout,
                chat_template_kwargs=ctk,
            )
            messages = _choice_messages(result.get("response", {}))
            content = _message_text(messages[0]) if messages else ""
            passed = "RIVER-918" in content.replace(" ", "")
            check.update({
                "status": "passed" if passed else "failed",
                "latency_ms": result["latency_s"] * 1000.0,
                "expected": "RIVER-918",
                "content_excerpt": content[:160],
            })
            if not passed:
                check["error"] = "response did not recall session code"
                failures.append({"suite": "session", "error": check["error"]})
        except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
            check.update({"status": "failed", "error": str(exc)})
            failures.append({"suite": "session", "error": str(exc)})
        session_section = {"status": check["status"], "checks": [check]}

    intelligence_section = {"status": "not_run", "checks": []}
    if "intelligence" in suites:
        checks = []
        for spec in INTELLIGENCE_PROMPTS:
            check = {
                "id": spec["id"],
                "status": "pending",
                "validator": "deterministic_text_checks",
            }
            try:
                result = post_chat(
                    a.base_url,
                    a.model,
                    api_key,
                    [{"role": "user", "content": spec["prompt"]}],
                    max_tokens=256,
                    timeout=a.timeout,
                    chat_template_kwargs=ctk,
                )
                messages = _choice_messages(result.get("response", {}))
                content = _message_text(messages[0]) if messages else ""
                text_checks = evaluate_text_checks(content, spec["checks"])
                passed = all(item["passed"] for item in text_checks)
                check.update({
                    "status": "passed" if passed else "failed",
                    "latency_ms": result["latency_s"] * 1000.0,
                    "text_checks": text_checks,
                    "content_excerpt": content[:200],
                })
                if not passed:
                    failures.append({
                        "suite": "intelligence",
                        "prompt_id": spec["id"],
                        "error": "deterministic text checks failed",
                    })
            except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                check.update({"status": "failed", "error": str(exc)})
                failures.append({"suite": "intelligence", "prompt_id": spec["id"], "error": str(exc)})
            checks.append(check)
        intelligence_section = {
            "status": "passed" if checks and all(c["status"] == "passed" for c in checks) else "failed",
            "checks": checks,
        }

    voice_section = {
        "status": "not_run",
        "stt_latency_ms": a.stt_latency_ms,
        "llm_latency_ms": None,
        "tts_latency_ms": a.tts_latency_ms,
        "total_turn_latency_ms": a.voice_latency_ms,
    }
    if "voice" in suites:
        if a.voice_latency_ms is None:
            voice_section["status"] = "skipped"
            voice_section["reason"] = "voice latency metrics were not supplied"
        else:
            voice_section["status"] = "recorded"

    metrics = _result_metrics(chat_results)
    wall_ms = (time.time() - started_at) * 1000.0
    passed_contexts = [
        r["target_tokens"] for r in context_results if r.get("status") == "passed"
    ]
    intelligence_checks = intelligence_section.get("checks") or []
    intelligence_pass_rate = None
    if intelligence_checks:
        intelligence_pass_rate = (
            sum(1 for check in intelligence_checks if check.get("status") == "passed")
            / len(intelligence_checks)
        )
    evidence = {
        "schema": "anvil-serving.fast-tier-bakeoff/v1",
        "run_id": time.strftime("fast-bakeoff-%Y%m%dT%H%M%SZ", time.gmtime(started_at)),
        "identity": {
            "candidate_id": a.candidate_id,
            "config_id": a.config_id,
            "model": a.model,
            "base_url": a.base_url,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        },
        "source_recipe": {
            "ref": a.source_recipe,
            "serve_command": a.serve_command,
        },
        "selection": {
            "suites": suites,
            "context_targets": context_targets,
            "requests_per_context": 1,
            "endpoint_already_loaded": True,
        },
        "timing": {
            "wall_ms": wall_ms,
            "chat": metrics,
        },
        "context": {
            "max_model_len": max_model_len,
            "cap_tokens": cap,
            "targets": context_results,
        },
        "tool": tool_section,
        "session": session_section,
        "intelligence": intelligence_section,
        "thinking": thinking_section,
        "voice": voice_section,
        "score_inputs": {
            "voice_latency_ms": a.voice_latency_ms,
            "tool_call_passed": tool_section.get("status") == "passed",
            "session_recall_passed": session_section.get("status") == "passed",
            "intelligence_pass_rate": intelligence_pass_rate,
            "usable_context_tokens": max(passed_contexts) if passed_contexts else None,
            "ttft_p50_ms": metrics["ttft_p50_ms"],
            "e2e_p50_ms": metrics["e2e_p50_ms"],
            "thinking_mode": thinking_section["mode"],
            "operational_fit_notes": [
                "endpoint was already loaded; benchmark did not start or stop serves"
            ],
        },
        "failures": failures,
    }
    if a.evidence_out:
        with open(a.evidence_out, "w", encoding="utf-8") as f:
            json.dump(evidence, f, indent=2, sort_keys=True)
            f.write("\n")
        print("wrote bakeoff evidence: " + a.evidence_out)
    else:
        print(json.dumps(evidence, indent=2, sort_keys=True))

    # Persist into the bakeoff notebook (in ADDITION to --evidence-out, so the
    # existing behavior never regresses). Requires --notebook-task/-hardware to
    # key the comparison; missing them is a loud error, not a silent skip.
    if getattr(a, "notebook", None):
        if not a.notebook_task or not a.notebook_hardware:
            print("error: --notebook requires --notebook-task and --notebook-hardware",
                  file=sys.stderr)
            return 2
        from .external_benchmarks import store as _nb_store

        row_id = _nb_store.record_bakeoff_run(
            a.notebook, evidence,
            task=a.notebook_task, hardware=a.notebook_hardware,
            evidence_path=a.evidence_out,
        )
        print("recorded bakeoff run %s into notebook %s (row %d)"
              % (evidence["run_id"], a.notebook, row_id))
    return 0

def _serve_recipes():
    """Import the shared serve-recipe helpers for package or direct script execution."""
    try:
        from . import serve_recipes as sr  # package import
    except ImportError:
        import serve_recipes as sr  # bare-script import (same dir on sys.path)
    return sr

def build_recipe(a, summary, *, capture=None, hardware=None):
    """Assemble a serve-recipe dict from a completed benchmark run + a live container.

    The reproducible half (image/env/flags/port + gpu_uuid) comes from
    `capture_from_container`; the hardware name/VRAM from `capture_hardware`; the
    [recipe.measured] numbers from THIS run's `summary`; and [recipe.intent] from the
    --recipe-* flags. `capture` / `hardware` are injectable for hermetic tests.
    """
    sr = _serve_recipes()
    capture = capture or sr.capture_from_container
    hardware = hardware or sr.capture_hardware

    cap = capture(a.recipe_from_container) if a.recipe_from_container else {}
    serve = dict(cap.get("serve") or {})
    hw = dict(cap.get("hardware") or {})
    gpu_uuid = hw.get("gpu_uuid")
    # Only capture hardware when the GPU is KNOWN — capture_hardware(None) would pick the
    # FIRST GPU row, silently recording the wrong card on a multi-GPU box (Copilot review).
    gpu = hardware(gpu_uuid) if gpu_uuid else {}

    recipe = {"model": a.recipe_model or a.model, "status": a.recipe_status}
    recipe["source"] = "measured via anvil-serving eval benchmark run (%s)" % summary.get("run_id", "")

    hardware_block = {}
    if gpu.get("gpu"): hardware_block["gpu"] = gpu["gpu"]
    if gpu.get("vram_total_gb") is not None: hardware_block["vram_total_gb"] = gpu["vram_total_gb"]
    if gpu_uuid: hardware_block["gpu_uuid"] = gpu_uuid
    if hardware_block: recipe["hardware"] = hardware_block

    ctx = summary.get("context_tokens") or summary.get("max_context_tokens")
    if ctx and "context_tokens" not in serve:
        serve["context_tokens"] = ctx
    if serve: recipe["serve"] = serve

    metrics = summary.get("metrics") or {}
    measured = {}
    tps = metrics.get("throughput_tok_s")
    if tps is not None:
        # throughput_tok_s is the AGGREGATE across all concurrent streams. Only label
        # it single-stream when concurrency==1; otherwise record it as aggregate + the
        # concurrency, so a generated recipe never mislabels a ~20-way number as the
        # single-stream stat the registry treats as its headline. (critic SHOULD-FIX)
        conc = summary.get("concurrency") or 1
        if conc == 1:
            measured["throughput_single_tok_s"] = round(tps, 1)
        else:
            measured["throughput_aggregate_tok_s"] = round(tps, 1)
            measured["concurrency"] = conc
    ttft = metrics.get("ttft_p50_ms")
    if ttft is not None:
        measured["ttft_p50_ms"] = round(ttft, 1)
    if ctx:
        measured["context_tokens"] = ctx
    if measured: recipe["measured"] = measured

    intent = {}
    if a.recipe_intent:
        suited = [s.strip() for s in a.recipe_intent.split(",") if s.strip()]
        if suited: intent["suited"] = suited
    if a.recipe_mode:
        intent["mode"] = a.recipe_mode
    if intent: recipe["intent"] = intent

    return recipe

def emit_recipe(a, summary, *, capture=None, hardware=None, append=None):
    """Render + persist the recipe for this benchmark run (stdout if --recipe-out '-')."""
    sr = _serve_recipes()
    recipe = build_recipe(a, summary, capture=capture, hardware=hardware)
    if a.recipe_out == "-":
        print(sr.format_recipe(recipe), end="")
    else:
        try:
            (append or sr.append_recipe)(a.recipe_out, recipe)
        except OSError as exc:
            print("could not write serve recipe to %s: %s" % (a.recipe_out, exc), file=sys.stderr)
            return recipe
        print("recorded serve recipe for %s -> %s" % (recipe["model"], a.recipe_out))
    return recipe

def main(argv=None, *, prog="anvil-serving eval benchmark run"):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "external":
        from .external_benchmarks import cli as external_bench
        return external_bench.main(
            argv[1:], prog="anvil-serving eval benchmark external"
        )

    ap = argparse.ArgumentParser(
        prog=prog,
        epilog=(
            "Related command: anvil-serving eval benchmark external "
            "(import, report, and compare external benchmark priors)."
        ),
    )
    ap.add_argument("--base-url", required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--api-key-env", default=None,
                    help="read the bearer token from this environment variable")
    ap.add_argument("--requests", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--burst", type=int, default=0, help="if >0, fire N requests sharing ONE prefix concurrently")
    ap.add_argument("--shared-prefix-tokens", type=int, default=8000)
    ap.add_argument("--ctx-tokens", type=int, default=0, help="fixed ctx; 0 = sample measured subagent distribution")
    ap.add_argument("--max-tokens", type=int, default=64, help="generation length (subagent median is tiny)")
    ap.add_argument("--max-model-len", type=int, default=0,
                    help="serve's context window (max_model_len). Sampled/fixed ctx is clamped to "
                         "stay under it (ctx <= max_model_len - max_tokens - margin) so a small-context "
                         "serve (e.g. 16384) doesn't HTTP 400. 0 = auto-detect from /v1/models, else no clamp.")
    ap.add_argument("--margin", type=int, default=DEFAULT_CTX_MARGIN,
                    help="token headroom left below max_model_len when clamping (default %(default)s)")
    ap.add_argument("--no-thinking", action="store_true",
                    help="inject chat_template_kwargs={'enable_thinking': False} so thinking-by-default "
                         "models (Qwen3.x, GLM) emit CONTENT instead of spending the whole max_tokens "
                         "budget on hidden reasoning and reporting a FALSE 0 tok/s (TTFT==E2E). NOTE: "
                         "gpt-oss-style models IGNORE this kwarg (they gate reasoning via 'reasoning effort').")
    ap.add_argument("--thinking-mode", choices=("default", "enabled", "disabled", "unsupported"),
                    default=None,
                    help="record/request thinking behavior for benchmark evidence. "
                         "disabled maps to chat_template_kwargs={'enable_thinking': False}; "
                         "enabled maps to {'enable_thinking': True}; unsupported records that "
                         "the serve has no supported thinking control.")
    ap.add_argument("--json-out", default=None,
                    help="write a machine-readable JSON summary for benchmark external compare")
    # --- GENERATE a serve recipe as a side effect of benchmarking a live serve ------
    # (READ them back with `anvil-serving models recipe list|show`.) All optional.
    ap.add_argument("--recipe-out", default=None,
                    help="after the run, record a [[recipe]] block: PATH to append to the "
                         "serve-recipe registry, or '-' for stdout. Captures the live serve's "
                         "reproducible docker config + THIS run's measured numbers.")
    ap.add_argument("--recipe-from-container", default=None, metavar="NAME",
                    help="docker container NAME of the serve to capture (image/env/flags/port + "
                         "gpu_uuid via `docker inspect`) for the recorded recipe")
    ap.add_argument("--recipe-intent", default=None, metavar="CSV",
                    help="comma-separated work-classes the serve is suited for (-> [recipe.intent].suited)")
    ap.add_argument("--recipe-mode", default=None,
                    help="the mode this recipe belongs to (-> [recipe.intent].mode)")
    ap.add_argument("--recipe-status", default="verified",
                    help="recipe provenance status (default %(default)s = measured on-box)")
    ap.add_argument("--recipe-model", default=None, metavar="NAME",
                    help="model id recorded in the recipe (default: --model)")
    # --- Fast-tier bakeoff evidence mode: target an already-loaded endpoint -----
    ap.add_argument("--bakeoff", action="store_true",
                    help="run selected Fast-tier bakeoff checks against an already-loaded "
                         "OpenAI-compatible endpoint and emit structured evidence JSON")
    ap.add_argument("--candidate-id", default=None,
                    help="candidate identifier recorded in --bakeoff evidence")
    ap.add_argument("--config-id", default=None,
                    help="serve/config identifier recorded in --bakeoff evidence")
    ap.add_argument("--context-targets", default="32768",
                    help="comma-separated context targets for --bakeoff (default %(default)s)")
    ap.add_argument("--suite", action="append",
                    help="bakeoff suite(s) to run; repeatable or comma-separated. "
                         "Known suites: chat, context, tool, session, intelligence, voice")
    ap.add_argument("--evidence-out", default=None,
                    help="write --bakeoff structured evidence JSON to this path")
    ap.add_argument("--notebook", default=None,
                    help="also record this --bakeoff run into the bakeoff notebook "
                         "SQLite DB at this path (append; keeps history)")
    ap.add_argument("--notebook-task", default=None,
                    help="task key for the notebook row (required with --notebook)")
    ap.add_argument("--notebook-hardware", default=None,
                    help="hardware key for the notebook row (required with --notebook)")
    ap.add_argument("--source-recipe", default=None,
                    help="recipe/config source reference recorded in --bakeoff evidence")
    ap.add_argument("--serve-command", default=None,
                    help="command needed to reproduce the already-loaded serve")
    ap.add_argument("--voice-latency-ms", type=float, default=None,
                    help="optional externally measured STT->LLM->TTS total latency")
    ap.add_argument("--stt-latency-ms", type=float, default=None,
                    help="optional externally measured STT stage latency")
    ap.add_argument("--tts-latency-ms", type=float, default=None,
                    help="optional externally measured TTS stage latency")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="request timeout in seconds (default %(default)s)")
    a = ap.parse_args(argv)
    api_key = resolve_api_key(a.api_key_env)

    if a.bakeoff:
        if not a.candidate_id or not a.config_id:
            ap.error("--bakeoff requires --candidate-id and --config-id")
        return run_bakeoff(a, api_key)

    # Resolve the serve's context window: explicit flag wins; else best-effort probe /v1/models.
    max_model_len = a.max_model_len or detect_max_model_len(a.base_url, a.model, api_key)
    cap = ctx_cap(max_model_len, a.max_tokens, a.margin)
    ctk, thinking = resolve_thinking_settings(a)

    shared = (FILLER % (0, 0)) * max(1, int(a.shared_prefix_tokens * 0.75) // 6)
    n = a.burst if a.burst else a.requests
    conc = a.burst if a.burst else a.concurrency
    jobs = []
    for i in range(n):
        ctx = clamp_ctx(a.ctx_tokens or sample_ctx(), cap)
        jobs.append(make_prompt(shared, ctx, i if not a.burst else 0, max_prompt_tokens=cap))  # burst: identical-ish prefix

    capnote = f" max_model_len={max_model_len}(ctx<={cap})" if cap is not None else ""
    thinknote = "" if thinking["mode"] == "default" else f" thinking={thinking['mode']}"
    print(f"BENCH {a.base_url} model={a.model}  n={n} concurrency={conc} "
          f"{'BURST(shared-prefix)' if a.burst else 'mixed'} max_tokens={a.max_tokens}{capnote}{thinknote}")
    started_at = time.time()
    t0 = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = [ex.submit(stream_chat, a.base_url, a.model, p, api_key, a.max_tokens,
                          chat_template_kwargs=ctk) for p in jobs]
        for f in as_completed(futs):
            try: results.append(f.result())
            except Exception as e: print("  req error:", e)
    wall = time.perf_counter() - t0
    ttfts = [r["ttft"] for r in results]; e2es = [r["e2e"] for r in results]
    out_tot = sum(r["out_toks"] for r in results)
    cfs = [cached_fraction(r["usage"]) for r in results]
    cfs = [c for c in cfs if c is not None]
    print("-"*60)
    print(f"completed:        {len(results)}/{n} in {wall:.1f}s")
    print(f"TTFT  p50/p95:    {pctile(ttfts,50):.2f}s / {pctile(ttfts,95):.2f}s")
    print(f"E2E   p50/p95:    {pctile(e2es,50):.2f}s / {pctile(e2es,95):.2f}s")
    print(f"throughput:       {(out_tot / wall if wall else 0.0):.0f} output tok/s (aggregate)")
    summary = {
        "schema": "anvil-serving.benchmark/v1",
        "run_id": time.strftime("benchmark-%Y%m%dT%H%M%SZ", time.gmtime(started_at)),
        "base_url": a.base_url,
        "model": a.model,
        "requests": n,
        "completed": len(results),
        "concurrency": conc,
        "context_tokens": a.ctx_tokens or None,
        "max_context_tokens": max_model_len,
        "max_tokens": a.max_tokens,
        "serve_flags": {
            "shared_prefix_burst": bool(a.burst),
            "no_thinking": bool(a.no_thinking),
            "thinking_mode": thinking["mode"],
        },
        "metrics": {
            "ttft_p50_ms": pctile(ttfts, 50) * 1000.0,
            "ttft_p95_ms": pctile(ttfts, 95) * 1000.0,
            "e2e_p50_ms": pctile(e2es, 50) * 1000.0,
            "e2e_p95_ms": pctile(e2es, 95) * 1000.0,
            "throughput_tok_s": (out_tot / wall) if wall else 0.0,
            "output_tokens": out_tot,
            "prefix_cache_hit_avg": statistics.mean(cfs) if cfs else None,
        },
    }
    if cfs:
        print(f"prefix-cache hit: {statistics.mean(cfs)*100:.1f}% avg cached prompt tokens (KEY KPI)")
    else:
        print("prefix-cache hit: endpoint did not return prompt_tokens_details.cached_tokens")
    print("-"*60)
    print("Tip: run once cold, then immediately again — TTFT should drop sharply on the 2nd run if prefix cache works.")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
            f.write("\n")
        print("wrote JSON summary: " + a.json_out)
    if len(results) != n:
        if a.recipe_out:
            print(
                "skipping serve recipe: benchmark completed %d/%d requests" % (len(results), n),
                file=sys.stderr,
            )
        return 1
    # Benchmarking a serve ALSO records its reproducible recipe when asked.
    if a.recipe_out:
        emit_recipe(a, summary)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
