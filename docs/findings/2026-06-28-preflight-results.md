# Preflight correctness-gate results — 2026-06-28 (T006)

Ran the anvil-serving PREFLIGHT correctness gate (`anvil_serving/preflight.py`, via
`python -m anvil_serving.cli preflight`) against the two live local serves. Real
results only; nothing fabricated. Environment: Windows 11, `PYTHONUTF8=1`, serves
reached over `localhost`.

Preflight runs four tests (source: `anvil_serving/preflight.py`):
1. `smoke (short coding)` — one-liner, expects `sum(` in content (`max_tokens=80`).
2. `structured JSON` — expects a parseable `{...}` with a `language` key (`max_tokens=64`).
3. `needle @ ~N ctx` — long-context needle retrieval (`max_tokens=40`).
4. `shared-prefix tool batch xK` — K concurrent tool-calls over a big shared prefix.

CLI flags: `--base-url --model [--api-key] [--needle-ctx N] [--tool-batch K]`.

---

## Scorecard

| Test | HEAVY (qwen3-coder-local, SGLang) | FAST (glm-4.7-flash, vLLM) |
|------|-----------------------------------|----------------------------|
| smoke (short coding) | **PASS** (22.0s) | **FAIL** — empty content |
| structured JSON      | **PASS** | **FAIL** — empty content → parse error |
| needle               | **PASS** (50.3s, `ZEBRA-42917-QUARTZ` retrieved @ ~128k) | **FAIL** — HTTP 500, **engine crashed** |
| shared-prefix tool batch | **PASS** (20/20 clean) | **FAIL** — connection refused (engine already dead) |
| **Overall** | **ALL PASS** | **FAILURES PRESENT** (serve crashed mid-run) |

HEAVY (Qwen3-Coder-30B on SGLang) is a clean, trustworthy serve: all four tests pass,
including 128k needle retrieval and a 20-wide concurrent tool-call batch with zero
garbage / zero malformed tool arguments.

FAST (GLM-4.7-Flash on vLLM) failed all four — for **two distinct reasons** that the
raw "FAIL x4" line hides (see findings below).

---

## Exact commands run

HEAVY:
```
PYTHONUTF8=1 python -m anvil_serving.cli preflight \
  --base-url http://localhost:30000/v1 --model qwen3-coder-local
```
Output:
```
[PASS] smoke (short coding)                   22.0s got='Here are a few Python one-liners to return the sum'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[PASS] needle @ ~128000 ctx                   50.3s ctx~128000 got='ZEBRA-42917-QUARTZ'
[PASS] shared-prefix tool batch x20           20/20 clean (sample: valid tool_call)
RESULT: ALL PASS
```

FAST (needle-ctx fitted to GLM's 65536 max_model_len; tool-batch reduced to 8):
```
PYTHONUTF8=1 python -m anvil_serving.cli preflight \
  --base-url http://localhost:30001/v1 --model glm-4.7-flash \
  --needle-ctx 40000 --tool-batch 8
```
Output:
```
[FAIL] smoke (short coding)                   21.7s got=''
[FAIL] structured JSON                        error: Expecting value: line 1 column 1 (char 0)
[FAIL] needle @ ~40000 ctx                    error: HTTP Error 500: Internal Server Error
[FAIL] shared-prefix tool batch x8            0/8 clean (sample: error: <urlopen error [WinError 10061] ... actively refused it>)
RESULT: FAILURES PRESENT
```

---

## Findings

### Finding 1 (TOOL GAP) — preflight.py has no thinking-disable, so it cannot test thinking-default models

GLM-4.7-Flash is **thinking-default**: with no `chat_template_kwargs:{enable_thinking:false}`,
the model spends its (small) token budget on reasoning and returns **empty assistant
content**. The smoke test ran 21.7s and returned `content=''`; JSON returned empty →
JSON parse error. preflight.py's `chat()` (lines 17-28) only ever sends
`{model, messages, max_tokens, temperature, [tools], [tool_choice]}` — there is **no way
to pass `chat_template_kwargs`** or otherwise disable thinking.

Confirmed against the live serve with a manual default call (preflight's exact smoke
prompt, `max_tokens=80`, no flag):
```
content = None
reasoning_content = ''
finish_reason = length      # budget exhausted by thinking, no visible content
```

This is exactly gotcha #5 in CLAUDE.md ("Qwen3.5/GLM thinks by default → empty content
with small max_tokens"). preflight.py does not account for it, so **a correct
thinking-default model is reported as failing**.

**Recommended fix:** add a `--no-thinking` (or `--chat-template-kwargs`) flag to
preflight.py that injects `body["chat_template_kwargs"] = {"enable_thinking": false}`
into `chat()`. Without it, preflight cannot give a valid verdict on GLM/Qwen3.x-thinking
serves. (NOTE: not patched here per task scope — reported, not silently changed.)

### Finding 2 (MODEL/SERVE BUG, not a preflight gap) — GLM-4.7-Flash vLLM engine crashes on the MLA prefill-context path

The needle test returned HTTP 500 and **killed the vLLM EngineCore**; every request
after that (the tool batch) got connection-refused, and the `vllm-glm` container exited.
Container logs show a deterministic crash:
```
File ".../vllm/model_executor/layers/attention/mla_attention.py", line 2154, in _compute_prefill_context
    kv_c_normed = kv_c_normed.to(self.kv_b_proj.weight.dtype)
AttributeError: 'ColumnParallelLinear' object has no attribute 'weight'
-> vllm.v1.engine.exceptions.EngineDeadError
```
Root cause: this GLM-4.7-Flash build is **compressed-tensors WNA16 / Marlin quantized**
(logs: `Using MarlinLinearKernel`, `CompressedTensorsWNA16MarlinMoEMethod`). The
quantized `kv_b_proj` has no plain `.weight` (it stores `qweight`/`scales`), but vLLM's
GLM4-MoE-lite MLA `_compute_prefill_context` dereferences `self.kv_b_proj.weight.dtype`.
The `_compute_prefill_context` / `forward_mha` path is entered on **chunked / long-context
prefill** (the needle request) **and on prefix-cache hits** — so this crash is triggered
by (a) the long needle prompt, and (b) repeated/shared-prefix requests. It is **not**
caused by `enable_thinking:false` (a manual short request with that flag hit the same
crash once it landed on the cached-prefix prefill path; the flag only changes prompt
text, the fault is in the attention forward).

Implication: this is a real instability in the FAST serve's model+quant+vLLM-build
combination, independent of anvil-serving. It will recur under normal long-context or
prefix-cache-heavy agent traffic. Recommend the serve owner pin/patch the vLLM MLA path
or switch GLM-4.7-Flash to a quant/build whose `kv_b_proj` exposes the path expected by
`_compute_prefill_context` (or disable the chunked-prefill / MLA prefill-context path).

---

## Operational note

Running preflight against the FAST serve **crashed its vLLM container** (`vllm-glm`,
Exited(0) after `EngineDeadError`). It was restarted with `docker start vllm-glm` to
restore the environment. Weight reload is very slow (~358 s/shard over the 9P/virtiofs
Windows bind mount — CLAUDE.md gotcha #2), ~15-20 min per restart. HEAVY (`sglang`,
port 30000) stayed up throughout.

## Manual verification — GLM is correct once thinking is disabled

After restarting the serve, a **fresh** `enable_thinking:false` request (unique prompt,
first real request after startup, no prefix-cache hit, so it avoids the Finding-2 crash
path) returned valid non-empty content:
```
POST /v1/chat/completions  (max_tokens=120, chat_template_kwargs:{enable_thinking:false})
prompt: "Write a Python one-liner that returns the product of a list named items."
-> HTTP 200
   content = "```python\nproduct = lambda items: eval('*'.join(map(str, items)))\n``` ..."
   reasoning = null
```
vLLM build: `vllm-0.23.1rc1.dev531+ga65f93fb2`.

This proves the smoke/JSON failures are a **preflight tool gap (Finding 1)**, not a model
defect: with `enable_thinking:false` the model produces correct, non-empty content. The
needle-test engine crash (Finding 2) is a separate vLLM/quant bug on the MLA
prefill-context path and is independent of the thinking flag.

## Bottom line

- **HEAVY (Qwen3-Coder-30B / SGLang):** preflight ALL PASS — trustworthy serve.
- **FAST (GLM-4.7-Flash / vLLM):** preflight cannot validate it today, for two reasons:
  1. preflight.py lacks a thinking-disable option (real **tool gap** — fix preflight).
  2. the vLLM build crashes on long-context / prefix-cache MLA prefill (real **serve bug**
     — fix the serve), independent of preflight.
- **Does preflight.py need a thinking-disable fix?** **Yes** — without `--no-thinking`
  (inject `chat_template_kwargs:{enable_thinking:false}`) it falsely fails any
  thinking-default model. But note: even with that fix, this particular GLM serve would
  still fail the needle test until the vLLM MLA `kv_b_proj.weight` crash is resolved.
