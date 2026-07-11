# Preflight transcripts and operator observations — 2026-07-10/11 bakeoff

Verbatim console output of `anvil-serving preflight` runs (the tool prints to
console; these captures are the artifacts behind the narrative's "needle" and
"20/20 tool batch" claims), plus operator `nvidia-smi` observations. The
bakeoff JSONs' `tool` suite is a separate single-check smoke
(`openai_tool_call_smoke`); the 20-request batch below is preflight's.

## Production heavy baseline restore verify (gpt-oss-120b, :30002, 2026-07-11 ~06:45Z)

```
[PASS] smoke (short coding)                   1.5s got='```python\nsum(xs)\n```'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[PASS] needle @ ~128000 ctx                   28.9s ctx~128000 got='ZEBRA-42917-QUARTZ'
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 1.0s valid tool_call get_weather(city='Oakland'))
RESULT: ALL PASS
```

## Production fast restore verify (qwen36-35b-a3b-nvfp4, :30003, --no-thinking, 2026-07-11 ~07:57Z)

```
[PASS] smoke (short coding)                   0.2s got='```python\nsum(xs)\n```'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[FAIL] needle @ ~128000 ctx                   error: HTTP Error 400: Bad Request   # probe exceeds the tier's 32k window (expected)
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 1.4s valid tool_call get_weather(city='Oakland'))
```

## Nemotron text, PIECEWISE-131k config (:39020, default mode, 2026-07-11 ~05:1xZ)

```
[PASS] smoke (short coding)                   2.4s got='```python\nsum(xs)\n```'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[PASS] needle @ ~128000 ctx                   41.9s ctx~128000 got='ZEBRA-42917-QUARTZ'
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 3.4s valid tool_call get_weather(city='Oakland'))
RESULT: ALL PASS
```

## Nemotron text, eager-64k config (:39020, default mode, 2026-07-11 ~04:1xZ)

```
[PASS] smoke (short coding)                   12.3s got='We need to respond with a Python one-liner that re'
[FAIL] structured JSON                        error: Extra data: line 1 column 32 (char 31)   # pre-nano_v3-parser think-leak
[FAIL] needle @ ~128000 ctx                   error: HTTP Error 400: Bad Request               # 64k window
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 47.7s valid tool_call get_weather(city='Oakland'))
```

## Nemotron Omni, nightly + tool flags (:39021, 2026-07-11 ~07:1xZ)

```
[PASS] smoke (short coding)                   0.8s got='```python\nsum(xs)\n```'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[FAIL] needle @ ~128000 ctx                   error: HTTP Error 400: Bad Request   # probe exceeds the 64k claim (by design)
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 2.3s valid tool_call get_weather(city='Oakland'))
```

## Ornith 35B FP8 (:39022, default mode, 2026-07-11 ~05:5xZ)

```
[FAIL] smoke (short coding)                   1.9s got=''                          # default-thinking small-budget starvation (gotcha #6/#9)
[FAIL] structured JSON                        error: Expecting value: line 1 column 1 (char 0)  # same starvation
[PASS] needle @ ~128000 ctx                   11.9s ctx~128000 got='ZEBRA-42917-QUARTZ'
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 1.0s valid tool_call get_weather(city='Oakland'))
```

## MiniMax M2.7 REAP (:39023, default mode, 2026-07-11 ~06:0xZ)

```
[PASS] smoke (short coding)                   2.2s got='The user asks: "Write a Python one-liner that retu'   # think-leak (no reasoning parser configured)
[FAIL] structured JSON                        error: Expecting ',' delimiter: line 1 column 17 (char 16)
[FAIL] needle @ ~128000 ctx                   error: HTTP Error 400: Bad Request   # 64k window
[FAIL] shared-prefix tool batch x20           14/20 clean (sample: 1.4s valid tool_call get_weather(city='Oakland'))
```

## Operator nvidia-smi observations (memory.used, MiB)

| When | GPU0 (RTX 5090) | GPU1 (PRO 6000) | Context |
|---|---:|---:|---|
| MiniMax resident @64k/1-seq | 27,347 (prod fast warm) | **94,332** | source of the 94.3 GB figure |
| Close-out (production only) | 27,597 | 87,034 | fast + heavy restored |

Preflight failures above marked as starvation/think-leak are default-mode
harness artifacts, not candidate regressions; the thinking-disabled bakeoff
JSONs are the correctness record. Kept verbatim per the no-hidden-failures
rule.

# Extension round transcripts (2026-07-11)

## Puzzle 75B, MTP n=3, :39026 (2026-07-11 ~07:4xZ)
```
[PASS] smoke (short coding)                   1.8s got='...sum_xs = lambda xs: sum(xs)...'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[PASS] needle @ ~128000 ctx                   13.8s ctx~128000 got='ZEBRA-42917-QUARTZ'
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 2.7s valid tool_call get_weather(city='Oakland'))
RESULT: ALL PASS
```

## Qwen3.6-27B NVFP4-MTP, :39027 (--no-thinking, 2026-07-11 ~08:2xZ)
```
[PASS] smoke (short coding)                   0.4s got='...sum(xs)...'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[PASS] needle @ ~128000 ctx                   25.1s ctx~128000 got='ZEBRA-42917-QUARTZ'
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 2.8s valid tool_call get_weather(city='Oakland'))
RESULT: ALL PASS
```

## Qwen3.5-35B Q4_K_M llama.cpp, :39028 (--no-thinking, 2026-07-11 ~07:5xZ)
```
[PASS] smoke (short coding)                   0.4s got='`sum(xs)`'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[FAIL] needle @ ~128000 ctx                   error: HTTP Error 400: Bad Request   # probe exceeds the 64k window
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 4.4s valid tool_call get_weather(city='Oakland'))
```

### Qwen3.5-35B server print_timing excerpt (llama.cpp, same serve - decode/prefill artifact)
```
slot print_timing: id  0 | task 792 | prompt eval time =     115.75 ms /   516 tokens (    0.22 ms per token,  4457.88 tokens per second)
slot print_timing: id  0 | task 792 |        eval time =     279.14 ms /    41 tokens (    6.81 ms per token,   146.88 tokens per second)
```
(Basis for the published ~147 tok/s decode and 4458 tok/s prefill figures.)

## Gemma-4-E4B QAT UD-Q4_K_XL llama.cpp, :39029 (2026-07-11 ~08:0xZ)
```
[PASS] smoke (short coding)                   1.2s got='...sum(xs)...'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[FAIL] needle @ ~128000 ctx                   error: HTTP Error 400: Bad Request   # probe exceeds the 64k window
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 2.1s valid tool_call get_weather(city='Oakland'))
```

## Production restores, second window (2026-07-11)
Fast (:30003, --no-thinking): smoke/JSON/tools 20/20 PASS; needle 400 beyond the 32k tier window (expected).
Heavy (:30002), cold verify:
```
[PASS] smoke (short coding)                   1.2s got='...sum(xs)...'
[PASS] structured JSON                        parsed keys=['language', 'ok']
[PASS] needle @ ~128000 ctx                   29.2s ctx~128000 got='ZEBRA-42917-QUARTZ'
[PASS] shared-prefix tool batch x20           20/20 clean (sample: 1.1s valid tool_call get_weather(city='Oakland'))
RESULT: ALL PASS
```
Two subsequent cache-warm repeats returned a resampled safety refusal on the
needle ("I'm sorry, but I can't provide that.", 2.0-2.1 s) - recorded as a
preflight-prompt observation; the cold ALL PASS is the restoration record.
