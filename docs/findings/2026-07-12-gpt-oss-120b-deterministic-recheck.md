# GPT-OSS-120B deterministic-eval control

> **Historical protocol warning:** this finding correctly identified the broken comparison
> protocol, and its reported cross-model scores remain invalid for ranking or promotion. The
> replacement contract is now [ADR-0022](../adr/0022-evaluation-evidence-protocol.md) and
> `anvil-serving eval benchmark quality`; this point-in-time record is intentionally unchanged.

**Point-in-time record, 2026-07-12.** This run restored the production
`openai/gpt-oss-120b` Heavy serve on Fakoli Dark, repeated the same preflight
and conventional benchmark shape used for the Qwen3.5-122B MXFP4 and Nemotron
Puzzle rechecks, and ran the exact same externally-authored deterministic
planning suite. A second, explicitly non-comparable diagnostic raised only the
suite response cap to show whether GPT-OSS hidden reasoning was exhausting the
original budget.

## Configuration

| Field | Tested value |
|---|---|
| Served model | `gpt-oss-120b` |
| Checkpoint | `openai/gpt-oss-120b` revision `b5c939de8f754692c1647ca79fbf85e8c1e70f8a` |
| Host | Fakoli Dark, Windows 11, Docker Desktop/WSL2 |
| GPU | RTX PRO 6000 Blackwell Max-Q, 96 GB, sm_120 |
| Engine | vLLM `0.23.1rc1.dev531+ga65f93fb2` |
| Image | `sha256:907377dddef3...5319ff3e` |
| Quantization | GPT-OSS MXFP4, Marlin MoE backend |
| KV cache | FP8 |
| Context | 131,072 |
| Endpoint | loopback `127.0.0.1:30002` |
| Managed serve | `heavy` / `vllm-gptoss120` |

The managed start loaded 15 shards in 149.70 seconds and reported 66.17 GiB
of model memory after 232.74 seconds. The healthy steady-state GPU reading was
87,034 MiB. The `--no-thinking` / `thinking=disabled` option sends
`enable_thinking=false`; GPT-OSS ignores that Qwen-style template option and
continues to use its native reasoning channel.

## Correctness gate

```powershell
anvil-serving eval preflight --base-url http://127.0.0.1:30002/v1 `
  --model gpt-oss-120b --needle-ctx 128000 --tool-batch 20 `
  --no-thinking --confirm
```

All checks passed: short coding in 1.5 seconds, structured JSON, the 128K
needle in 28.9 seconds, and 20/20 shared-prefix tool calls.

## Conventional benchmark

The normal benchmark used 10 sequential requests, 8,192 context tokens, and a
256-token cap. Raw artifact:
[standard-throughput.json](2026-07-12-gpt-oss-120b-recheck-evidence/standard-throughput.json).

| Metric | Result |
|---|---:|
| Completion | 10/10 |
| Aggregate output throughput | **29.87 tok/s** |
| TTFT p50 / p95 | 655.67 / 1257.35 ms |
| E2E p50 / p95 | 806.72 / 1377.04 ms |
| Output tokens | 258 |

As with the other short mixed-prompt runs, this aggregate value is not the
controlled long-generation decode rate. The established production-baseline
decode result remains 183.2 tok/s.

## Exact deterministic-suite result

The exact suite used for Qwen and Nemotron retained its original per-case
256–384 token caps. Raw artifact:
[deterministic-planning-eval.json](2026-07-12-gpt-oss-120b-recheck-evidence/deterministic-planning-eval.json).

Overall: **0/5 passed**. Four of five cases had an empty visible-content
excerpt and therefore failed every literal check. A direct diagnostic of the
first request showed why: the response contained `content: null`, placed all
384 completion tokens in `message.reasoning`, and stopped with
`finish_reason: length`. See the bounded
[probe summary](2026-07-12-gpt-oss-120b-recheck-evidence/reasoning-budget-probe-summary.json).
The harness correctly evaluated visible answer content; there simply was no
visible answer inside the configured cap.

This is an eval-configuration failure for GPT-OSS, not valid evidence that its
planning quality is zero.

## Raised-cap diagnostic

To isolate the confounder, a diagnostic copy preserved every prompt and text
check while raising only `max_tokens` to 2,048:
[suite](2026-07-12-gpt-oss-120b-recheck-evidence/planning-milestone-execution-2048.suite.json)
and [result](2026-07-12-gpt-oss-120b-recheck-evidence/deterministic-planning-eval-2048.json).

All five cases then produced visible answer content and **1/5 passed**. The
local/remote-main reconciliation case passed; the other four still missed one
or more exact operational-contract phrases. This diagnostic is not an
apples-to-apples score against the original Qwen/Nemotron runs because its
response budget is larger. It establishes that hidden-reasoning headroom
materially changes the score, while also confirming that the suite's literal
contracts remain demanding after content appears.

## Original built-in eval rerun

The pre-`--suite-file` bakeoff was also rerun unchanged with its original
`chat,context,tool,session,intelligence` selection and 131,072-token context
target. Clean raw artifact:
[original-bakeoff-clean.json](2026-07-12-gpt-oss-original-eval-rerun-evidence/original-bakeoff-clean.json).

| Original built-in section | Result |
|---|---|
| 131K context probe | pass |
| OpenAI tool-call smoke | pass |
| Multi-turn session recall | pass |
| Unified-diff intelligence check | pass |
| Parallel timeout-triage intelligence check | **fail: empty visible content** |

The same original recipe passed both intelligence checks on 2026-07-11. In
this clean rerun, a direct probe of the failed case showed `content: null`, all
256 completion tokens in the reasoning channel, and `finish_reason: length`.
See the bounded
[probe summary](2026-07-12-gpt-oss-original-eval-rerun-evidence/timeout-triage-budget-probe-summary.json).
The context timing was prefix-cache-warm and is not a comparable performance
measurement.

This means the newer external suite did not introduce the entire problem. The
older built-in intelligence eval also hard-codes a 256-token response budget
without a GPT-OSS reasoning-effort control, so its quality result can change
from pass to empty-answer failure as native reasoning consumes that budget.

## Operator conclusion

**Operator assessment: the current cross-model `--suite-file` eval protocol is
broken and its Qwen, Nemotron, and GPT-OSS scores must not be used to rank or
promote models.** The deterministic text-check engine still evaluates the
visible text it receives, but the surrounding protocol does not provide
comparable reasoning controls or answer budgets across model families.

There is extra eval behavior to account for. The new suite path needs a
model-aware reasoning budget or explicit `reasoning_effort` control before its
scores can compare GPT-OSS fairly with models whose thinking can be disabled.
Evidence should also retain finish reason and reasoning-token/channel metadata
so an empty visible answer can be classified as budget exhaustion rather than
ordinary deterministic-check failure. The current artifacts also record
`thinking.unsupported: false` even though GPT-OSS ignores
`enable_thinking=false`; that metadata is misleading and must not be treated as
proof that thinking was disabled. No routing or quality-profile decision
changed from this run.
