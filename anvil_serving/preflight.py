#!/usr/bin/env python
"""Bounded correctness preflight for a local OpenAI-compatible model endpoint.

Validate an SGLang/vLLM serve on Blackwell sm_120 BEFORE trusting throughput.
Tests: (1) long-context needle retrieval, (2) shared-prefix tool-calling batch
(detects sm_120 garbage / spec-decode tool corruption), (3) structured JSON,
(4) short coding smoke. Stdlib only (urllib).

Usage:
  anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 \
    --model coder-specialist --needle-ctx 128000 --confirm
Exit code 0 = all pass, 1 = any fail.
"""
import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from .model_controls import validate_reasoning_control
except ImportError:  # direct ``python anvil_serving/preflight.py`` compatibility
    from model_controls import validate_reasoning_control


def _console_safe(value):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(value).encode(encoding, errors="backslashreplace").decode(encoding)


def _atomic_write_json(path, value):
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("output directory does not exist: %s" % parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=parent,
                prefix=".%s." % os.path.basename(out), suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(value, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, out)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _validate_output_path(path):
    """Fail before live probes when an output target cannot be replaced safely."""
    if not path or path == "-":
        return None
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("output directory does not exist: %s" % parent)
    if os.path.islink(out):
        raise OSError("output path cannot be a symbolic link: %s" % out)
    if os.path.exists(out) and not os.path.isfile(out):
        raise OSError("output path is not a regular file: %s" % out)
    if not os.access(parent, os.W_OK):
        raise OSError("output directory is not writable: %s" % parent)
    return out

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
             reasoning_effort=None, evidence=None, timeout=900):
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
                        chat_template_kwargs=ctk, reasoning_effort=reasoning_effort,
                        timeout=timeout)
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
               reasoning_effort=None, evidence=None, request_index=None, timeout=900):
    msgs = [{"role": "system", "content": shared_prefix},
            {"role": "user", "content": "What's the weather in Oakland? Use the tool."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens, tools=TOOLS,
                        tool_choice="auto", chat_template_kwargs=ctk,
                        reasoning_effort=reasoning_effort, timeout=timeout)
        obs = _capture(evidence, "tools", resp, dt, request_index)
        m = resp["choices"][0]["message"]
        ok, detail = validate_tool_call(m)
        obs.update({"passed": ok, "validation_detail": detail})
        return ok, f"{dt:.1f}s {detail} {_evidence_note(obs)}"
    except Exception as e:
        return False, f"error: {e}"

def t_tool_batch(base, model, key, n, ctk=None, max_tokens=256,
                 reasoning_effort=None, evidence=None, timeout=900):
    # big stable shared prefix to exercise prefix cache + reproduce fan-out
    shared = ("You are a coding specialist agent. Follow the harness contract.\n" * 400)
    oks = []
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(t_tool_one, base, model, key, shared, ctk, max_tokens,
                          reasoning_effort, evidence, index, timeout) for index in range(n)]
        details = []
        for f in as_completed(futs):
            ok, d = f.result(); oks.append(ok); details.append(d)
    passed = sum(oks)
    return passed == n, f"{passed}/{n} clean (sample: {details[0] if details else 'n/a'})"

def t_json(base, model, key, ctk=None, max_tokens=256, reasoning_effort=None,
           evidence=None, timeout=900):
    msgs = [{"role": "user", "content": 'Return ONLY a JSON object: {"language":"python","ok":true}. No prose.'}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens,
                        chat_template_kwargs=ctk, reasoning_effort=reasoning_effort,
                        timeout=timeout)
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
            evidence=None, timeout=900):
    msgs = [{"role": "user", "content": "Write a Python one-liner that returns the sum of a list `xs`."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens,
                        chat_template_kwargs=ctk, reasoning_effort=reasoning_effort,
                        timeout=timeout)
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
        value = os.environ.get(api_key_env)
        if not value:
            raise ValueError("environment variable %s is not set" % api_key_env)
        return value
    return None

def main(argv=None, *, prog="anvil-serving eval preflight"):
    ap = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Run bounded correctness gates before trusting endpoint performance.\n\n"
            "Examples:\n"
            "  anvil-serving eval preflight --tier heavy --checks smoke,json --dry-run\n"
            "  anvil-serving eval preflight --tier heavy --checks smoke,json,needle,tools "
            "--output preflight.json --confirm\n"
            "  anvil-serving eval preflight --base-url http://127.0.0.1:30002/v1 "
            "--model MODEL --thinking-mode enabled --reasoning-headroom-tokens 4096 "
            "--reasoning-evidence required --confirm"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    endpoint = ap.add_argument_group("direct endpoint input")
    endpoint.add_argument("--base-url", help="OpenAI-compatible endpoint base URL")
    endpoint.add_argument("--model", help="served model id")
    manifest = ap.add_argument_group("serves manifest input")
    manifest.add_argument("--manifest", help="serves manifest TOML (used with --tier)")
    manifest.add_argument("--tier", help="serve name in the manifest; fills endpoint and model")
    recipe = ap.add_argument_group("serve recipe input")
    recipe.add_argument("--recipe", help="recorded recipe model selector")
    recipe.add_argument("--registry", help="serve-recipe registry used with --recipe")
    ap.add_argument("--api-key-env", default=None,
                    help="read the bearer token from this environment variable")
    ap.add_argument("--needle-ctx", type=int, default=128000)
    ap.add_argument("--tool-batch", type=int, default=20)
    ap.add_argument("--checks", default="smoke,json,needle,tools",
                    help="comma-separated checks: smoke,json,needle,tools")
    ap.add_argument("--thinking-mode", choices=("default", "enabled", "disabled", "unsupported"),
                    default="default", help="model-family thinking control to request")
    ap.add_argument("--reasoning-effort",
                    choices=("none", "minimal", "low", "medium", "high"),
                    help="top-level OpenAI reasoning_effort for model families that use it")
    ap.add_argument("--visible-answer-tokens", type=int, default=256,
                    help="visible-answer allocation recorded by the gate")
    ap.add_argument("--reasoning-headroom-tokens", type=int, default=0,
                    help="reasoning headroom added to the API completion cap")
    ap.add_argument("--json-out", "--output", dest="json_out",
                    help="write machine-readable gate evidence atomically")
    ap.add_argument("--timeout", "--timeout-seconds", dest="timeout", type=float,
                    default=900.0, help="per-request timeout, 1..3600 seconds")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and print the plan; send no requests and write nothing")
    ap.add_argument("--reasoning-evidence", choices=("any", "required", "forbidden"),
                    default="any", help="assert effective reasoning-channel behavior")
    ap.add_argument("--allowed-finish-reasons", default="stop,tool_calls",
                    help="comma-separated finish reasons accepted by the gate")
    ap.add_argument("--no-thinking", action="store_true",
                    help="compatibility alias for --thinking-mode disabled; valid only for "
                         "chat-template-controlled model families")
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
    if not 1 <= a.needle_ctx <= 1000000:
        ap.error("--needle-ctx must be from 1 through 1000000")
    if not 1 <= a.tool_batch <= 128:
        ap.error("--tool-batch must be from 1 through 128")
    if not 0 < a.timeout <= 3600:
        ap.error("--timeout must be greater than 0 and at most 3600 seconds")
    selected = [item.strip() for item in a.checks.split(",") if item.strip()]
    unknown = sorted(set(selected) - {"smoke", "json", "needle", "tools"})
    if not selected or unknown:
        ap.error("--checks must select smoke,json,needle,tools; unknown=%s" % unknown)
    allowed_finish_reasons = {
        item.strip() for item in a.allowed_finish_reasons.split(",") if item.strip()
    }
    if not allowed_finish_reasons:
        ap.error("--allowed-finish-reasons cannot be empty")
    try:
        _validate_output_path(a.json_out)
    except OSError as exc:
        ap.error(str(exc))
    from .eval import resolve_endpoint_target
    try:
        a.base_url, a.model, _selected = resolve_endpoint_target(
            tier=a.tier,
            manifest=a.manifest,
            base_url=a.base_url,
            model=a.model,
            recipe=a.recipe,
            registry=a.registry,
        )
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    try:
        validate_reasoning_control(
            a.model,
            thinking_mode=a.thinking_mode,
            no_thinking=a.no_thinking,
            reasoning_effort=a.reasoning_effort,
        )
    except ValueError as exc:
        ap.error(str(exc))
    try:
        api_key = resolve_api_key(a.api_key_env)
    except ValueError as exc:
        ap.error(str(exc))
    ctk = ({"enable_thinking": True} if a.thinking_mode == "enabled" else
           {"enable_thinking": False} if a.thinking_mode == "disabled" else None)
    evidence = []
    if a.dry_run:
        print(json.dumps({
            "schema": "anvil-serving.eval-plan/v1",
            "workload": "preflight",
            "target": {"base_url": a.base_url, "model": a.model, "tier": a.tier,
                       "manifest": a.manifest},
            "checks": selected,
            "budget": {"visible_answer_tokens": a.visible_answer_tokens,
                       "reasoning_headroom_tokens": a.reasoning_headroom_tokens,
                       "max_completion_tokens": max_tokens},
            "timeout_seconds": a.timeout,
            "output": a.json_out,
            "deferred": ["endpoint identity", "model requests", "artifact write"],
        }, indent=2, sort_keys=True, ensure_ascii=True))
        return 0
    available = {
        "smoke": ("smoke (short coding)", lambda: t_smoke(a.base_url, a.model, api_key, ctk, max_tokens, a.reasoning_effort, evidence, a.timeout)),
        "json": ("structured JSON", lambda: t_json(a.base_url, a.model, api_key, ctk, max_tokens, a.reasoning_effort, evidence, a.timeout)),
        "needle": (f"needle @ ~{a.needle_ctx} ctx", lambda: t_needle(a.base_url, a.model, api_key, a.needle_ctx, ctk, max_tokens, a.reasoning_effort, evidence, a.timeout)),
        "tools": (f"shared-prefix tool batch x{a.tool_batch}", lambda: t_tool_batch(a.base_url, a.model, api_key, a.tool_batch, ctk, max_tokens, a.reasoning_effort, evidence, a.timeout)),
    }
    tests = [available[name] for name in selected]
    allok = True
    print(_console_safe(
        f"PRE-FLIGHT  {a.base_url}  model={a.model}  thinking={a.thinking_mode} "
        f"visible={a.visible_answer_tokens} reasoning_headroom={a.reasoning_headroom_tokens} "
        f"max_tokens={max_tokens}\n" + "-"*60
    ))
    results = []
    for name, fn in tests:
        ok, detail = fn()
        allok &= ok
        results.append({"name": name, "passed": ok, "detail": detail})
        print(_console_safe(f"[{'PASS' if ok else 'FAIL'}] {name:38} {detail}"))
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
        try:
            _atomic_write_json(a.json_out, artifact)
        except OSError as exc:
            ap.error(str(exc))
    return 0 if allok else 1

if __name__ == "__main__":
    raise SystemExit(main())
