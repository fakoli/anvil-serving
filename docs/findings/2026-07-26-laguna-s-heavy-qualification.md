# Laguna S 2.1 Heavy qualification and promotion

**Date:** 2026-07-26

**Host:** Fakoli Dark, NVIDIA RTX PRO 6000 Blackwell 96 GB

**Decision:** Promote Laguna S 2.1 NVFP4 as the managed Heavy serve with thinking
disabled; retain GPT-OSS Puzzle 88B as the declared rollback.

## Exact tested configuration

| Field | Qualified value |
|---|---|
| Checkpoint | `poolside/Laguna-S-2.1-NVFP4` |
| Revision | `07614121b31898586430f189d27a25a0be310843` |
| Served name | `laguna-s-2.1-nvfp4` |
| Engine | vLLM `0.23.1rc1.dev1327+gf25953cc5` |
| Image | `vllm/vllm-openai:nightly-f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1` |
| Context | 262,144 tokens |
| Managed endpoint | `http://127.0.0.1:30002/v1` |
| Thinking control | `chat_template_kwargs.enable_thinking=false` |

The checkpoint identity, engine, context, and request-level thinking control are
recorded in the [repeated quality artifact](2026-07-26-laguna-s-qualification-evidence/quality-thinking-disabled.json).
The source recipe and managed manifest pin the same model revision and engine
image.

## Why thinking is disabled

The [upstream model card](https://huggingface.co/poolside/Laguna-S-2.1-NVFP4)
documents thinking as enabled by default and documents request-level control
through `chat_template_kwargs`. A [July 23 upstream
discussion](https://huggingface.co/poolside/Laguna-S-2.1-NVFP4/discussions/3)
likewise confirms that the template toggle is the supported way to disable
reasoning. The locally cached template defaults `enable_thinking` to true and
`preserve_thinking` to false.

The first thinking-enabled smoke check consumed the complete 4,352-token
completion allowance, returned `finish_reason=length`, emitted 18,840 reasoning
characters, and produced no visible answer after 45.9 seconds. The immediate
full rerun passed, including a 2,090-character reasoning trace in its smoke
check. These two outcomes establish an intermittent exhaustion risk rather than
a deterministic parser failure. The failing
[operator observation](2026-07-26-laguna-s-qualification-evidence/thinking-enabled-exhaustion.json)
and passing [thinking-enabled rerun](2026-07-26-laguna-s-qualification-evidence/preflight-thinking-enabled.json)
are both retained.

Thinking-disabled preflight passed smoke, structured JSON, a 4K needle, and
2/2 parallel tool calls with no reasoning field. The production router and
promotion plan therefore force `enable_thinking=false`; callers do not inherit
the checkpoint's default.

## Local qualification

The repeated protocol-v3 quality run used three attempts per deterministic
check, a required pass rate of 1.0, 256 visible-answer tokens, and zero reasoning
headroom. It recorded no failures:

| Gate | Result |
|---|---|
| Context retrieval | Passed at 32K, 128K, and 240K targets |
| Actual prompt tokens | 27,799; 148,899; 243,641 |
| Context TTFT | 2.26 s; 21.15 s; 50.64 s |
| OpenAI tool call | 3/3 |
| Multi-turn recall | 3/3 |
| Unified diff | 3/3 |
| Timeout triage | 3/3 |

The [quality artifact](2026-07-26-laguna-s-qualification-evidence/quality-thinking-disabled.json)
contains the complete visible answers, deterministic checks, budgets, usage, and
latencies. A second
[thinking-disabled preflight](2026-07-26-laguna-s-qualification-evidence/preflight-thinking-disabled.json)
and the final
[promotion functional gate](2026-07-26-laguna-s-qualification-evidence/promotion-functional-preflight.json)
also passed; the latter exercised a 120K requested needle and 10/10 tools.

Short-output capacity completed 10/10 requests at concurrency one and 40/40 at
concurrency eight with independent 8,192-token prompts:

| Concurrency | TTFT p50 / p95 | E2E p50 / p95 | Aggregate output |
|---:|---:|---:|---:|
| 1 | 0.07 / 0.55 s | 0.57 / 0.90 s | 75.46 tok/s |
| 8 | 3.44 / 4.37 s | 3.87 / 4.87 s | 83.24 tok/s |

These are short-output batch-capacity measurements, not controlled long-decode
throughput. See the [c1](2026-07-26-laguna-s-qualification-evidence/capacity-8k-c1.json)
and [c8](2026-07-26-laguna-s-qualification-evidence/capacity-8k-c8.json)
artifacts.

## Promotion and rollback

The human-approved guarded promotion:

1. loaded the exact managed Laguna Heavy service on port 30002;
2. passed the thinking-disabled promotion functional gate;
3. promoted the matching router config and profile;
4. verified router gateway HTTP 200 and exact served identity; and
5. left the Heavy reservation in the `admitting` state.

GPT-OSS Puzzle 88B remains the only declared Heavy rollback. Its container is
not kept resident because it competes for the same GPU, but its pinned image,
checkpoint cache, manifest entry, router configuration, and prior qualification
remain available through the managed rollback path.

## Scope and caveats

- This proves the exact Laguna S configuration on one RTX PRO 6000; it is not a
  general cross-model intelligence ranking.
- The thinking-enabled failure is intermittent. The disabled-thinking profile
  is the qualified production contract, not a claim that reasoning can never
  work.
- The capacity artifacts use short completions, so their aggregate output rates
  must not be compared with controlled long-generation numbers.
- Promotion was human-authorized after the local gates; benchmark completion
  alone did not change production.
