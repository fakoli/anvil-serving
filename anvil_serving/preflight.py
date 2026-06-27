#!/usr/bin/env python3
"""preflight.py - correctness pre-flight for a local OpenAI-compatible LLM endpoint.

Validate an SGLang/vLLM serve on Blackwell sm_120 BEFORE trusting throughput.
Tests: (1) long-context needle retrieval, (2) shared-prefix tool-calling batch
(detects sm_120 garbage / spec-decode tool corruption), (3) structured JSON,
(4) short coding smoke. Stdlib only (urllib).

Usage:
  python3 preflight.py --base-url http://localhost:30000/v1 --model coder-specialist \
     --needle-ctx 128000 [--api-key KEY] [--tool-batch 20]
Exit code 0 = all pass, 1 = any fail.
"""
import argparse, json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

def chat(base, model, messages, key=None, max_tokens=256, temperature=0.0,
         tools=None, tool_choice=None, timeout=900):
    url = base.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if tools: body["tools"] = tools
    if tool_choice: body["tool_choice"] = tool_choice
    headers = {"Content-Type": "application/json"}
    if key: headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()), time.time() - t0

def t_needle(base, model, key, ctx_tokens):
    secret = "ZEBRA-42917-QUARTZ"
    line = "The engineers continued routine checks while the telemetry stayed nominal. "
    words = int(ctx_tokens * 0.75)
    reps = max(1, words // max(1, len(line.split())))
    body = line * reps
    cut = int(len(body) * 0.58)
    doc = body[:cut] + f"\n\nIMPORTANT: The launch code is {secret}.\n\n" + body[cut:]
    msgs = [{"role": "user", "content": doc + "\n\nQuestion: What is the launch code? Reply with ONLY the code."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=40)
        out = resp["choices"][0]["message"].get("content") or ""
        ok = secret.replace("-", "") in out.replace("-", "").replace(" ", "")
        return ok, f"{dt:.1f}s ctx~{ctx_tokens} got={out.strip()[:50]!r}"
    except Exception as e:
        return False, f"error: {e}"

TOOLS = [{"type": "function", "function": {
    "name": "get_weather",
    "description": "Get current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]

def t_tool_one(base, model, key, shared_prefix):
    msgs = [{"role": "system", "content": shared_prefix},
            {"role": "user", "content": "What's the weather in Oakland? Use the tool."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=128, tools=TOOLS, tool_choice="auto")
        m = resp["choices"][0]["message"]
        content = (m.get("content") or "")
        # garbage signatures seen with sm_120 / bad spec-decode
        if any(g in content for g in ("<<tool", "<|", "function=", "�")):
            return False, f"garbage content: {content[:60]!r}"
        tcs = m.get("tool_calls") or []
        if tcs:
            json.loads(tcs[0]["function"]["arguments"])  # must parse
            return True, "valid tool_call"
        return (len(content) > 0), f"text-only: {content[:40]!r}"
    except Exception as e:
        return False, f"error: {e}"

def t_tool_batch(base, model, key, n):
    # big stable shared prefix to exercise prefix cache + reproduce fan-out
    shared = ("You are a coding specialist agent. Follow the harness contract.\n" * 400)
    oks = []
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(t_tool_one, base, model, key, shared) for _ in range(n)]
        details = []
        for f in as_completed(futs):
            ok, d = f.result(); oks.append(ok); details.append(d)
    passed = sum(oks)
    return passed == n, f"{passed}/{n} clean (sample: {details[0] if details else 'n/a'})"

def t_json(base, model, key):
    msgs = [{"role": "user", "content": 'Return ONLY a JSON object: {"language":"python","ok":true}. No prose.'}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=64)
        out = (resp["choices"][0]["message"].get("content") or "").strip()
        s = out[out.find("{"): out.rfind("}") + 1]
        obj = json.loads(s)
        return ("language" in obj), f"parsed keys={list(obj)[:4]}"
    except Exception as e:
        return False, f"error: {e}"

def t_smoke(base, model, key):
    msgs = [{"role": "user", "content": "Write a Python one-liner that returns the sum of a list `xs`."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=80)
        out = (resp["choices"][0]["message"].get("content") or "")
        return ("sum(" in out), f"{dt:.1f}s got={out.strip()[:50]!r}"
    except Exception as e:
        return False, f"error: {e}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--needle-ctx", type=int, default=128000)
    ap.add_argument("--tool-batch", type=int, default=20)
    a = ap.parse_args()
    tests = [
        ("smoke (short coding)", lambda: t_smoke(a.base_url, a.model, a.api_key)),
        ("structured JSON",      lambda: t_json(a.base_url, a.model, a.api_key)),
        (f"needle @ ~{a.needle_ctx} ctx", lambda: t_needle(a.base_url, a.model, a.api_key, a.needle_ctx)),
        (f"shared-prefix tool batch x{a.tool_batch}", lambda: t_tool_batch(a.base_url, a.model, a.api_key, a.tool_batch)),
    ]
    allok = True
    print(f"PRE-FLIGHT  {a.base_url}  model={a.model}\n" + "-"*60)
    for name, fn in tests:
        ok, detail = fn()
        allok &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name:38} {detail}")
    print("-"*60); print("RESULT:", "ALL PASS" if allok else "FAILURES PRESENT")
    raise SystemExit(0 if allok else 1)

if __name__ == "__main__":
    main()
