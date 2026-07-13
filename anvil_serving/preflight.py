#!/usr/bin/env python3
"""preflight.py - correctness pre-flight for a local OpenAI-compatible LLM endpoint.

Validate an SGLang/vLLM serve on Blackwell sm_120 BEFORE trusting throughput.
Tests: (1) long-context needle retrieval, (2) shared-prefix tool-calling batch
(detects sm_120 garbage / spec-decode tool corruption), (3) structured JSON,
(4) short coding smoke. Stdlib only (urllib).

Usage:
  python3 preflight.py --base-url http://127.0.0.1:30000/v1 --model coder-specialist \
     --needle-ctx 128000 [--api-key-env ENV] [--tool-batch 20] \
     [--thinking-mode disabled|enabled] [--reasoning-headroom-tokens 4096]
Exit code 0 = all pass, 1 = any fail.
"""
import argparse
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

def chat(base, model, messages, key=None, max_tokens=256, temperature=0.0,
         tools=None, tool_choice=None, timeout=900, chat_template_kwargs=None,
         reasoning_effort=None):
    url = base.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if tools: body["tools"] = tools
    if tool_choice: body["tool_choice"] = tool_choice
    # chat_template_kwargs (e.g. {"enable_thinking": False}) is honored by SGLang/vLLM
    # for Qwen3.x / GLM so reasoning models don't burn the token budget on hidden
    # thinking and return empty content. NOTE: gpt-oss ignores this kwarg (it gates
    # reasoning via "reasoning effort", not the chat template) -> needs adequate tokens.
    if chat_template_kwargs: body["chat_template_kwargs"] = chat_template_kwargs
    if reasoning_effort is not None: body["reasoning_effort"] = reasoning_effort
    headers = {"Content-Type": "application/json"}
    if key: headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()), time.time() - t0

def response_observation(response):
    """Retain the evidence needed to distinguish bad output from budget starvation."""
    choices = response.get("choices") if isinstance(response, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    reasoning_field = None
    reasoning = ""
    for field in ("reasoning", "reasoning_content"):
        value = message.get(field)
        if isinstance(value, str):
            reasoning_field = field
            if value:
                reasoning = value
                break
    usage = response.get("usage") if isinstance(response, dict) else None
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    return {
        "content": content,
        "finish_reason": choice.get("finish_reason"),
        "content_chars": len(content),
        "content_excerpt": content[:200],
        "reasoning_field": reasoning_field,
        "reasoning_chars": len(reasoning),
        "reasoning_excerpt": reasoning[:200],
        "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None,
        "usage": usage,
    }

def _capture(evidence, test, response, seconds, request_index=None):
    observation = response_observation(response)
    observation.update({"test": test, "seconds": round(seconds, 3)})
    if request_index is not None:
        observation["request_index"] = request_index
    if evidence is not None:
        evidence.append(observation)
    return observation

def _evidence_note(observation):
    return "finish=%r visible=%s reasoning_chars=%s reasoning_tokens=%r" % (
        observation["finish_reason"], observation["content_chars"],
        observation["reasoning_chars"], observation["reasoning_tokens"],
    )

def t_needle(base, model, key, ctx_tokens, ctk=None, max_tokens=256,
             reasoning_effort=None, evidence=None):
    secret = "ZEBRA-42917-QUARTZ"
    line = "The engineers continued routine checks while the telemetry stayed nominal. "
    words = int(ctx_tokens * 0.75)
    reps = max(1, words // max(1, len(line.split())))
    body = line * reps
    cut = int(len(body) * 0.58)
    doc = body[:cut] + f"\n\nIMPORTANT: The launch code is {secret}.\n\n" + body[cut:]
    msgs = [{"role": "user", "content": doc + "\n\nQuestion: What is the launch code? Reply with ONLY the code."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens,
                        chat_template_kwargs=ctk, reasoning_effort=reasoning_effort)
        obs = _capture(evidence, "needle", resp, dt)
        out = resp["choices"][0]["message"].get("content") or ""
        ok = secret.replace("-", "") in out.replace("-", "").replace(" ", "")
        obs.update({"passed": ok, "validation_detail": "needle present" if ok else "needle missing"})
        return ok, f"{dt:.1f}s ctx~{ctx_tokens} got={out.strip()[:50]!r} {_evidence_note(obs)}"
    except Exception as e:
        return False, f"error: {e}"

TOOLS = [{"type": "function", "function": {
    "name": "get_weather",
    "description": "Get current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]

def validate_tool_call(message, expected_name="get_weather", required_args=None):
    """Validate that a chat response produced a usable OpenAI-style tool call."""
    required_args = required_args or ["city"]
    content = message.get("content") or ""
    if any(g in content for g in ("<<tool", "<|", "function=", "�")):
        return False, f"garbage content: {content[:60]!r}"

    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return False, "response did not include tool_calls"

    first = tool_calls[0] or {}
    function = first.get("function") or {}
    if function.get("name") != expected_name:
        return False, f"wrong function name: {function.get('name')!r}"

    raw_args = function.get("arguments")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except Exception as exc:
        return False, f"arguments are not valid JSON: {exc}"
    if not isinstance(args, dict):
        return False, "arguments are not a JSON object"

    missing = []
    for arg in required_args:
        value = args.get(arg)
        if not isinstance(value, str) or not value.strip():
            missing.append(arg)
    if missing:
        return False, "missing required string argument(s): " + ", ".join(missing)

    shown = ", ".join(f"{arg}={args[arg]!r}" for arg in required_args)
    return True, f"valid tool_call {expected_name}({shown})"

def t_tool_one(base, model, key, shared_prefix, ctk=None, max_tokens=256,
               reasoning_effort=None, evidence=None, request_index=None):
    msgs = [{"role": "system", "content": shared_prefix},
            {"role": "user", "content": "What's the weather in Oakland? Use the tool."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens, tools=TOOLS,
                        tool_choice="auto", chat_template_kwargs=ctk,
                        reasoning_effort=reasoning_effort)
        obs = _capture(evidence, "tools", resp, dt, request_index)
        m = resp["choices"][0]["message"]
        ok, detail = validate_tool_call(m)
        obs.update({"passed": ok, "validation_detail": detail})
        return ok, f"{dt:.1f}s {detail} {_evidence_note(obs)}"
    except Exception as e:
        return False, f"error: {e}"

def t_tool_batch(base, model, key, n, ctk=None, max_tokens=256,
                 reasoning_effort=None, evidence=None):
    # big stable shared prefix to exercise prefix cache + reproduce fan-out
    shared = ("You are a coding specialist agent. Follow the harness contract.\n" * 400)
    oks = []
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(t_tool_one, base, model, key, shared, ctk, max_tokens,
                          reasoning_effort, evidence, index) for index in range(n)]
        details = []
        for f in as_completed(futs):
            ok, d = f.result(); oks.append(ok); details.append(d)
    passed = sum(oks)
    return passed == n, f"{passed}/{n} clean (sample: {details[0] if details else 'n/a'})"

def t_json(base, model, key, ctk=None, max_tokens=256, reasoning_effort=None,
           evidence=None):
    msgs = [{"role": "user", "content": 'Return ONLY a JSON object: {"language":"python","ok":true}. No prose.'}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens,
                        chat_template_kwargs=ctk, reasoning_effort=reasoning_effort)
        obs = _capture(evidence, "json", resp, dt)
        out = (resp["choices"][0]["message"].get("content") or "").strip()
        s = out[out.find("{"): out.rfind("}") + 1]
        obj = json.loads(s)
        ok = "language" in obj
        obs.update({"passed": ok, "validation_detail": "parsed keys=%r" % list(obj)[:4]})
        return ok, f"parsed keys={list(obj)[:4]} {_evidence_note(obs)}"
    except Exception as e:
        return False, f"error: {e}"

def t_smoke(base, model, key, ctk=None, max_tokens=256, reasoning_effort=None,
            evidence=None):
    msgs = [{"role": "user", "content": "Write a Python one-liner that returns the sum of a list `xs`."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens,
                        chat_template_kwargs=ctk, reasoning_effort=reasoning_effort)
        obs = _capture(evidence, "smoke", resp, dt)
        out = (resp["choices"][0]["message"].get("content") or "")
        ok = "sum(" in out
        obs.update({"passed": ok, "validation_detail": "contains sum(" if ok else "missing sum("})
        return ok, f"{dt:.1f}s got={out.strip()[:50]!r} {_evidence_note(obs)}"
    except Exception as e:
        return False, f"error: {e}"

def resolve_api_key(api_key_env=None):
    """Resolve auth for probes from an environment variable reference."""
    if api_key_env:
        return os.environ.get(api_key_env)
    return None

def main(argv=None, *, prog="anvil-serving eval preflight"):
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Run correctness gates against a direct endpoint or a serves-manifest tier.",
    )
    endpoint = ap.add_argument_group("direct endpoint input")
    endpoint.add_argument("--base-url", help="OpenAI-compatible endpoint base URL")
    endpoint.add_argument("--model", help="served model id")
    manifest = ap.add_argument_group("serves manifest input")
    manifest.add_argument("--manifest", help="serves manifest TOML (used with --tier)")
    manifest.add_argument("--tier", help="serve name in the manifest; fills endpoint and model")
    ap.add_argument("--api-key-env", default=None,
                    help="read the bearer token from this environment variable")
    ap.add_argument("--needle-ctx", type=int, default=128000)
    ap.add_argument("--tool-batch", type=int, default=20)
    ap.add_argument("--checks", default="smoke,json,needle,tools",
                    help="comma-separated checks: smoke,json,needle,tools")
    ap.add_argument("--thinking-mode", choices=("default", "enabled", "disabled", "unsupported"),
                    default="default", help="model-family thinking control to request")
    ap.add_argument("--reasoning-effort",
                    help="top-level OpenAI reasoning_effort for model families that use it")
    ap.add_argument("--visible-answer-tokens", type=int, default=256,
                    help="visible-answer allocation recorded by the gate")
    ap.add_argument("--reasoning-headroom-tokens", type=int, default=0,
                    help="reasoning headroom added to the API completion cap")
    ap.add_argument("--json-out", help="write machine-readable gate evidence")
    ap.add_argument("--reasoning-evidence", choices=("any", "required", "forbidden"),
                    default="any", help="assert effective reasoning-channel behavior")
    ap.add_argument("--allowed-finish-reasons", default="stop,tool_calls",
                    help="comma-separated finish reasons accepted by the gate")
    ap.add_argument("--no-thinking", action="store_true",
                    help="inject chat_template_kwargs={'enable_thinking': False} into every "
                         "request so reasoning/thinking-default models (Qwen3.x, GLM) don't "
                         "burn the token budget on hidden reasoning and FALSE-FAIL with empty "
                         "content. NOTE: gpt-oss-style models IGNORE this kwarg (they gate "
                         "reasoning via 'reasoning effort', not the chat template) -> they just "
                         "need adequate max_tokens; the correctness tests already use >=256.")
    a = ap.parse_args(argv)
    if a.no_thinking:
        if a.thinking_mode not in ("default", "disabled") or a.reasoning_effort is not None:
            ap.error("--no-thinking conflicts with explicit thinking controls")
        a.thinking_mode = "disabled"
    if a.reasoning_effort is not None and a.thinking_mode != "default":
        ap.error("--reasoning-effort cannot be combined with --thinking-mode")
    if a.visible_answer_tokens < 1 or a.reasoning_headroom_tokens < 0:
        ap.error("token allocations must be visible>=1 and reasoning>=0")
    max_tokens = a.visible_answer_tokens + a.reasoning_headroom_tokens
    if max_tokens > 65536:
        ap.error("combined completion allocation cannot exceed 65536")
    selected = [item.strip() for item in a.checks.split(",") if item.strip()]
    unknown = sorted(set(selected) - {"smoke", "json", "needle", "tools"})
    if not selected or unknown:
        ap.error("--checks must select smoke,json,needle,tools; unknown=%s" % unknown)
    allowed_finish_reasons = {
        item.strip() for item in a.allowed_finish_reasons.split(",") if item.strip()
    }
    if not allowed_finish_reasons:
        ap.error("--allowed-finish-reasons cannot be empty")
    from .eval import resolve_endpoint_target
    try:
        a.base_url, a.model, _selected = resolve_endpoint_target(
            tier=a.tier,
            manifest=a.manifest,
            base_url=a.base_url,
            model=a.model,
        )
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    api_key = resolve_api_key(a.api_key_env)
    ctk = ({"enable_thinking": True} if a.thinking_mode == "enabled" else
           {"enable_thinking": False} if a.thinking_mode == "disabled" else None)
    evidence = []
    available = {
        "smoke": ("smoke (short coding)", lambda: t_smoke(a.base_url, a.model, api_key, ctk, max_tokens, a.reasoning_effort, evidence)),
        "json": ("structured JSON", lambda: t_json(a.base_url, a.model, api_key, ctk, max_tokens, a.reasoning_effort, evidence)),
        "needle": (f"needle @ ~{a.needle_ctx} ctx", lambda: t_needle(a.base_url, a.model, api_key, a.needle_ctx, ctk, max_tokens, a.reasoning_effort, evidence)),
        "tools": (f"shared-prefix tool batch x{a.tool_batch}", lambda: t_tool_batch(a.base_url, a.model, api_key, a.tool_batch, ctk, max_tokens, a.reasoning_effort, evidence)),
    }
    tests = [available[name] for name in selected]
    allok = True
    print(f"PRE-FLIGHT  {a.base_url}  model={a.model}  thinking={a.thinking_mode} "
          f"visible={a.visible_answer_tokens} reasoning_headroom={a.reasoning_headroom_tokens} "
          f"max_tokens={max_tokens}\n" + "-"*60)
    results = []
    for name, fn in tests:
        ok, detail = fn()
        allok &= ok
        results.append({"name": name, "passed": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name:38} {detail}")
    bad_finishes = [item for item in evidence if item.get("finish_reason") not in allowed_finish_reasons]
    reasoning_seen = any(
        (item.get("reasoning_chars") or 0) > 0 or (item.get("reasoning_tokens") or 0) > 0
        for item in evidence
    )
    policy_errors = []
    if bad_finishes:
        policy_errors.append("disallowed finish_reason: %s" % sorted({
            repr(item.get("finish_reason")) for item in bad_finishes
        }))
    if a.reasoning_evidence == "required" and not reasoning_seen:
        policy_errors.append("reasoning evidence was required but not observed")
    if a.reasoning_evidence == "forbidden" and reasoning_seen:
        policy_errors.append("reasoning evidence was forbidden but observed")
    for error in policy_errors:
        print(f"[FAIL] {'evidence policy':38} {error}")
    allok &= not policy_errors
    print("-"*60); print("RESULT:", "ALL PASS" if allok else "FAILURES PRESENT")
    artifact = {
        "schema_version": "preflight/v2", "base_url": a.base_url, "model": a.model,
        "thinking": {"mode": a.thinking_mode, "chat_template_kwargs": ctk,
                     "reasoning_effort": a.reasoning_effort},
        "budget": {"visible_answer_tokens": a.visible_answer_tokens,
                   "reasoning_headroom_tokens": a.reasoning_headroom_tokens,
                   "max_completion_tokens": max_tokens},
        "checks": selected, "results": results, "observations": evidence,
        "evidence_policy": {"reasoning": a.reasoning_evidence,
                            "allowed_finish_reasons": sorted(allowed_finish_reasons),
                            "errors": policy_errors},
        "passed": allok,
    }
    if a.json_out:
        out = os.path.abspath(os.path.expanduser(a.json_out))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(artifact, f, indent=2)
            f.write("\n")
    return 0 if allok else 1

if __name__ == "__main__":
    raise SystemExit(main())
