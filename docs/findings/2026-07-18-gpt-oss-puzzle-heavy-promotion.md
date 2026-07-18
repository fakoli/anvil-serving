# GPT-OSS Puzzle 88B Heavy enablement

**Captured:** 2026-07-18<br>
**Host:** Fakoli Dark; NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition,
96 GB<br>
**Checkpoint:** `nvidia/gpt-oss-puzzle-88B` at
`9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2`<br>
**Engine source:** Anvil vLLM commits
`3fbe020fb04afb27885196dabd1a72392074d49c` and
`485463b3498ed3ffcf0c8fcb52c1670a21be5d82`<br>
**Result:** promote Puzzle 88B to the local default Heavy tier; retain official
Gemma 4 12B IT QAT W4A16 as the immediate managed rollback

This record is the promotion follow-up to the
[July 17 local qualification](2026-07-17-gpt-oss-puzzle-qualification.md).
That run established the model architecture, weighted KV grouping, real
checkpoint load, ordinary GPT-OSS control path, deterministic Chat, Responses,
prefix-cache boundary crossings, a 130,696-prompt-token near-limit retrieval,
five-request capacity, and the full official GPT-OSS GPQA sampler. Its remaining
promotion blocker was the prompt-specific `record_weather_zip` tool-parser crash
addressed here.

## Why the serving branch is required

The model-support commit adds the Puzzle architecture from the work proposed in
upstream [vLLM PR #38135](https://github.com/vllm-project/vllm/pull/38135).
The pinned checkpoint also needs a narrow generation-config compatibility fix.
Its `generation_config.json` identifies tokens `200002` and `199999` as EOS but
omits Harmony token `200012`, `<|call|>`. A valid tool call could therefore
continue into another assistant turn. The OpenAI tool parser then received token
`173781` (`assistant`) where Harmony requires token `200006` (`<|start|>`) and
returned HTTP 500. Adding `200012` as a request-level stop token made the same
tool case pass 10/10, isolating termination rather than model quality as the
failure.

The production command now supplies:

```text
--override-generation-config '{"eos_token_id":[200002,199999,200012]}'
```

The prior fork revision did not apply special-token fields from this full
generation-config override. Commit `485463b3` merges the override in
`ModelConfig.try_get_generation_config()` and adds a focused unit test for
`eos_token_id` and `stop_token_ids`. This behavior is also proposed upstream in
[vLLM PR #45978](https://github.com/vllm-project/vllm/pull/45978); the fork commit
is a local backport, not a competing upstream contribution. The broader GPT-OSS
generation-config limitation is tracked in
[vLLM issue #28041](https://github.com/vllm-project/vllm/issues/28041).

## Default Heavy shape

The `heavy` managed service and router tier now select
`gpt-oss-puzzle-88b`. The reproducible shape is:

| Setting | Value |
|---|---|
| Image | `anvil-vllm:gpt-oss-puzzle-485463b3498ed3ffcf0c8fcb52c1670a21be5d82` |
| Endpoint | `http://127.0.0.1:30002/v1` |
| Context | 131,072 tokens |
| Quantization | checkpoint-native GPT-OSS MXFP4; Marlin MoE backend |
| KV cache | FP8 |
| Admission | 8 sequences; 8,192 batched tokens |
| Tool path | native Harmony template; OpenAI parser; automatic tool choice |
| Router default | `reasoning_effort=high` |
| Immediate rollback | `heavy-gemma4-rollback` (`gemma4-12b-it-w4a16-ct`) |

The image and checkpoint revision are both immutable. Gemma 4 keeps its prior
256K recipe and evidence as the immediate rollback; ThinkingCap and GPT-OSS-120B
remain second- and third-line rollbacks.

## Functional isolation run

Before the expensive full-source image completed, a temporary overlay copied
only `vllm/config/model.py` from commit `485463b3` onto the previously qualified
Puzzle image. This isolated the generation-config merge behavior but is not
promotion evidence and supports no performance claim. On the RTX PRO 6000 it
passed:

- health and model identity;
- smoke and structured JSON;
- a 120,000-token requested needle check (99,100 prompt tokens observed);
- 20/20 shared-prefix tool calls with reasoning evidence;
- the original parser regression 10/10 without request-level
  `stop_token_ids`;
- Responses API completion and streaming SSE with `[DONE]`;
- a complete tool-call, tool-result, final-answer exchange.

The raw preflight is
[`overlay-functional-preflight.json`](2026-07-18-gpt-oss-puzzle-heavy-promotion-evidence/overlay-functional-preflight.json),
and the explicitly non-promotable summary is
[`overlay-compatibility-smoke.json`](2026-07-18-gpt-oss-puzzle-heavy-promotion-evidence/overlay-compatibility-smoke.json).

## Exact-image and router qualification

The final source build produced image
`sha256:470f7b7e39c4363696d5a79fd041d6a45253229a9ba1c055d089ddbdc0ed120c`
with both build and OCI revision labels set to `485463b3`. The managed Heavy
container resolved that exact image and reported vLLM
`0.23.1rc1.dev1229+g485463b34`. Startup resolved
`GptOssPuzzleForCausalLM`, the pinned checkpoint revision, corrected EOS list,
Marlin MXFP4, 38.72 GiB FP8 KV cache, and 2,819,653 KV tokens before health
returned HTTP 200.

The exact image then passed the same full promotion preflight: coding smoke,
structured JSON, 99,100-prompt-token retrieval, 20/20 shared-prefix tools, and
required reasoning evidence. The original `record_weather_zip` regression passed
10/10 with HTTP 200, `finish_reason=tool_calls`, exact ZIP `98101`, and no
request-level `stop_token_ids`. Responses API, streaming SSE with `[DONE]`, and a
tool-result continuation also passed. See
[`runtime-identity.json`](2026-07-18-gpt-oss-puzzle-heavy-promotion-evidence/runtime-identity.json),
[`promotion-functional-preflight.json`](2026-07-18-gpt-oss-puzzle-heavy-promotion-evidence/promotion-functional-preflight.json),
[`tool-call-regression.json`](2026-07-18-gpt-oss-puzzle-heavy-promotion-evidence/tool-call-regression.json),
and
[`exact-interface-smoke.json`](2026-07-18-gpt-oss-puzzle-heavy-promotion-evidence/exact-interface-smoke.json).
Artifact hashes are retained in
[`SHA256SUMS`](2026-07-18-gpt-oss-puzzle-heavy-promotion-evidence/SHA256SUMS).

The router profile and config were written atomically, then the pinned local
router image was started without model-service dependency changes. Its first
start failed closed because the automatically selected
`~/.anvil-serving/.env` exists but has no router-token entry, shadowing the
existing token in `~/.env`. Restarting through Anvil Serving with the explicit
existing env file restored auth without exposing or replacing the secret. The
tailnet front door then reported Heavy `ready` and `admitting`, with expected and
observed model both `gpt-oss-puzzle-88b`. An authenticated routed tool request
returned the exact model identity and `record_weather_zip(zip="98101")`. See
[`router-validation.json`](2026-07-18-gpt-oss-puzzle-heavy-promotion-evidence/router-validation.json).

The checked-in promotion and rollback router profiles were validated against the
deployed router image before any state change. The OpenClaw harness sync also
passed in dry-run mode with the Heavy-backed intents at a 131,072-token context.
No remote OpenClaw configuration was applied as part of this local Heavy change.

## Scope and caveats

- This change proves local serving compatibility and the listed protocol
  features. It does not claim Puzzle has better quality or throughput than the
  prior Gemma Heavy; no controlled cross-model quality or performance bakeoff was
  run in this transition.
- FP8 KV cache trades some numerical precision for context capacity. All live
  gates use the same FP8-KV production shape.
- The 120K gate requests approximately 120,000 characters; the tokenizer
  reported 99,100 prompt tokens. The served 131,072-token window is an engine
  identity claim for this rerun. The separate July 17 exact-image qualification
  retains the 130,696-prompt-token near-limit evidence.
- The local fork commit is retained only until the equivalent upstream
  generation-config behavior is available in the selected vLLM base.

## Reproduction surface

Serve lifecycle changes used Anvil Serving rather than raw Docker mutation:

```powershell
uv run --no-sync anvil-serving serves up heavy --manifest examples/fakoli-dark/serves.toml --recreate --no-router --confirm
uv run --no-sync anvil-serving eval preflight --tier heavy --manifest examples/fakoli-dark/serves.toml --checks smoke,json,needle,tools --needle-ctx 120000 --tool-batch 20 --thinking-mode default --reasoning-effort low --visible-answer-tokens 512 --reasoning-headroom-tokens 4096 --reasoning-evidence required --confirm
uv run --no-sync anvil-serving harness sync openclaw --config examples/fakoli-dark/anvil-router.live.toml --dry-run
```

The current and rollback shapes are defined in
`examples/fakoli-dark/docker-compose.yml`, `examples/fakoli-dark/serves.toml`,
and `configs/serve-recipes.toml`.
