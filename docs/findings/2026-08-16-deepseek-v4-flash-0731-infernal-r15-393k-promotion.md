# DeepSeek V4 Flash 0731 Infernal Invocation r15 393K promotion

**Date:** 2026-08-16

**Decision:** human-approved `llm.primary`; r33 393K is the managed rollback

**Evidence:** `functional`, `capacity`, matched `performance`, bounded `quality`, routed acceptance

## Outcome

The digest-pinned Infernal Invocation r15 profile is qualified on the local
dual-RTX-PRO WSL2 host at a 393,216-token configured window and is installed
through the managed serving path. It owns both equal cards in exclusive TP=2,
the router alias `llm.primary` resolves to its exact served identity, and the
route is healthy and admitting. The prior r33 393K profile remains the
transactional rollback on the same fixed endpoint port.

This promotion does not transfer the upstream author's qualification. The
upstream receipt covered 131,072 tokens on native Linux with two RTX PRO 6000
Blackwell GPUs attached to direct PCIe root ports. The 393,216-token WSL2
result below is an independent local qualification.

## Source and author credit

This work was inspired by and translated from Martin Vit's (`voipmonitor`)
Infernal Invocation r15 work in
[`local-inference-lab/rtx6kpro` at `55323f94`](https://github.com/local-inference-lab/rtx6kpro/blob/55323f94cd9d9ea98ccecef553791a63c3585816/models/ds4dspark-infernal-invocation-r15.md)
and
[`local-inference-lab/blackwell-llm-docker` at `2c301121`](https://github.com/local-inference-lab/blackwell-llm-docker/blob/2c301121c8680f02a91443f502d13ca1fccb51c2/examples/docker-compose-ds4-infernal-invocation-cu133-r15.yml).
The image composition also incorporates the contributors to
[`local-inference-lab/b12x` PR #214](https://github.com/local-inference-lab/b12x/pull/214)
and
[`local-inference-lab/vllm` PR #320](https://github.com/local-inference-lab/vllm/pull/320).
The public recipe references these sources and does not redistribute their
runtime source or image. Neither pinned repository exposes a root license file,
so local use does not establish redistribution permission.

## Immutable configuration

- Checkpoint: `deepseek-ai/DeepSeek-V4-Flash-0731` revision
  `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`.
- Image: `voipmonitor/vllm@sha256:f1b13c8604b274212e1164def7d4ed7a4cac9e4f7fa06fa1739730195eca4e18`.
- Runtime: vLLM integration tree `068fc8e7270b92077ba753d002da179c865e444d`,
  B12X tree `96e5d3d5c2057fa5d4f542e2368951ddbdcb5b42`.
- Hardware: two RTX PRO 6000 Blackwell Max-Q Workstation Edition cards,
  Windows 11 Docker Desktop/WSL2, PCIe without NVLink, exclusive TP=2/DCP=1.
- Serving: B12X W4A8, FP8 compressed MLA KV, InstantTensor `BUFFERED`,
  393,216 tokens, maxseq8, batch4096, fixed probabilistic DSpark K5.
- Offload: native KV offload, native L2, and LMCache disabled.
- [K5 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r15-b12x-dspark5-maxseq8-batch4096-393k-recipe.toml)
  and [matched no-spec control](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r15-b12x-nospec-maxseq8-batch4096-393k-recipe.toml).

## Matched speculative-decoding A/B

The control retained the exact image, checkpoint, TP/DCP layout, context,
batching, maxseq, FP8 KV, allocator, loading, memory utilization, and WSL2
controls. Only DSpark, the loopback port, and served evidence name differed.

| Shape | No-spec median decode | K5 median decode | Change | No-spec / K5 effective prefill |
|---|---:|---:|---:|---:|
| 4K, c1, 3 runs | 76.4 tok/s | 150.0 tok/s | +96% | 8,237 / 8,741 tok/s |
| 32K, c1, 3 runs | 76.767 tok/s | 119.245 tok/s | +55% | 9,262 / 9,071 tok/s |

Both arms passed the full protocol gate. K5 speculative counters sampled
72.8% acceptance in the principal lane; later long requests varied, so the
speed result remains prompt- and trajectory-specific.

## Correctness, context, concurrency, and quality

- Direct K5 passed smoke, JSON, retrieval, tools 20/20, streaming tools,
  tool-result continuation, and Responses.
- Direct retrieval passed at 118,080 and **351,118 actual prompt tokens**; the
  largest completed in 53.133 seconds and returned the exact needle.
- The authenticated router passed a calibrated **340,119-actual-token** request
  in 58.891 seconds and recovered its needle through `llm.primary`.
- Short concurrency completed 8/8 at c8 with 267 aggregate output tok/s.
- Long concurrency completed 2/2 at c2 with 99,175 prompt tokens per request
  after applying the qualified 4,096-token reasoning headroom.
- Repeated tools, session recall, unified diff, and parallel timeout triage
  passed 12/12 attempts.
- The engine reported 797,689 KV tokens, or 2.03 configured full windows. The
  router therefore admits at most two requests; this is a capacity ceiling,
  not a broad 393K-c2 quality claim.

The sanitized machine-readable summary is
[`summary.json`](2026-08-16-deepseek-v4-flash-0731-infernal-r15-393k-evidence/summary.json).

## Serving-path acceptance and rollback

The guarded transaction quiesced and drained `primary-local`, reused the
already-qualified exact-identity target, reran the complete direct 390K/tools
gate, atomically installed the router profile, restarted the router, and
verified post-restart identity readiness. The route then passed coding, JSON,
tools 20/20, streaming tools, tool-result continuation, Responses, and the
calibrated 340,119-token request.

OpenClaw-compatible Anthropic Messages calls through `llm.primary` returned an
exact readiness marker and a valid typed `workspace_status` tool call. This is
wire-path evidence, not a fresh actual Mini OpenClaw client run: the reachable
Mini controller does not implement the current OpenClaw status tool. No Mini
configuration was changed.

The rollback profile is the most recent working r33 B12X/DSpark K5 393K
configuration. Its checkpoint, image, context, batching, maxseq, speculation,
and offload policy remain unchanged; an endpoint-only adapter moves it from
39075 to the transaction's fixed 39077 port. Recipe dry-run, manifest lint,
rollback-check, and promotion dry-run all passed before the live cutover.

## Retained failures and caveats

- The generic thinking-disabled request did not disable r15 reasoning. Default
  thinking is therefore the qualified contract; the router does not advertise
  caller override.
- The stock nominal-390K English preflight was rejected by the router with
  HTTP 413. Its conservative byte/4 estimate exceeded 393,216 even though the
  direct server measured 351,118 actual tokens. A token-dense calibrated
  routed probe passed at 340,119 actual tokens; the 413 is retained rather than
  relabelled as success.
- The first 128K/c2 attempt allowed only 512 completion tokens, so both requests
  finished reasoning without visible content. Repeating with the qualified
  4,096-token budget passed 2/2.
- Startup warned that K5 reduced `max_num_scheduled_tokens` to 4,064. No
  exception, OOM, or request failure followed; no unmeasured tuning was mixed
  into the A/B.
- Idle reported free VRAM is about 1.5 GiB per card. This exclusive AI profile
  does not retain a graphics/co-resident reserve.
- The private live manifest currently references a linked public worktree.
  Removing that worktree before the recipe is installed at a durable revision
  would break future managed restart or rollback commands.
