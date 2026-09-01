# Laguna S 2.1 and Laguna XS

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Laguna S is a retained rollback-era qualified recipe;
      Laguna XS is a rejected compatibility path. Neither label reports a
      current live route.
    - **Selected or best-qualified configuration:** Laguna S 2.1 NVFP4 with
      thinking disabled at 262,144 tokens; retained TP=1 and exclusive TP=2
      evidence describe different measured lanes.
    - **Measured hardware:** one RTX PRO 6000 for the historical TP=1 lane and
      two equal RTX PRO 6000 cards over PCIe for the exclusive TP=2 refresh.
    - **Evidence:** Laguna S has `functional`, `capacity`, and `quality`
      evidence; Laguna XS has `compatibility-only` and `historical-invalid`
      evidence.
    - **Decision:** Laguna S was a dated `rollback`; the TP=2 refresh is
      `no-promotion`; Laguna XS is `rejected` under the tested recipes.
    - **Important limitation:** thinking-enabled Laguna S can exhaust its
      visible-answer budget, and some Laguna XS response bodies/full logs were
      not retained.
    - **Review dates:** retained evidence through 2026-08-01; dossier-format
      review 2026-08-31.

[Open the retained recipe registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

#### 2026-07-12 — Laguna XS engine A/B rejection

Laguna XS was attempted through vLLM and SGLang on one RTX PRO 6000. The vLLM
FP8-KV lane exposed corrupted text and 0/20 tools; the non-FP8 fallback stalled
during cache profiling. SGLang loaded but produced repetitive or off-topic
output, missed the long-context and tool gates, and its documented
`trtllm_mha` backend rejected sm_120. Some operator-observed healthy-run
responses and full logs were lost when containers were recreated, so they are
not reproducible quality claims.

**Outcome:** `rejected` for the tested configurations, not a judgment about the
model on every Blackwell product.

#### 2026-07-26 — Laguna S single-card qualification and promotion

Laguna S used vLLM `0.23.1rc1.dev1327+gf25953cc5`, NVFP4 weights, FP8 KV,
262,144 context, and thinking disabled. Repeated protocol-v3 quality passed,
including 32K, 128K, and 240K retrieval, tools, session recall, unified diff,
and timeout triage. Thinking-enabled testing also showed an intermittent
failure that consumed the complete 4,352-token completion allowance and
returned no visible answer, so the qualified contract forced thinking off.
The dated campaign then recorded a human-approved Heavy promotion with
GPT-OSS Puzzle retained as rollback.

**Outcome:** the exact TP=1 disabled-thinking recipe qualified and was promoted
in that dated campaign; this dossier does not claim it remains live.

#### 2026-08-01 — Exclusive TP=2 refresh

The same exact Laguna S checkpoint ran on two equal PRO 6000 cards with pinned
vLLM 0.25.1, NVFP4/FP8 KV, 262,144 context, one admitted request, no DFlash,
and thinking disabled. Smoke, JSON, 30K retrieval, tools, extended tools,
intelligence 6/6, session 3/3, and tools 3/3 passed. At 32K it measured
1.97-second TTFT, 15,134 effective prefill tok/s, and 70.9 tok/s decode. At a
231,457-token prompt it measured 31.85-second TTFT, 7,252 effective prefill
tok/s, and 66.0 tok/s decode.

**Outcome:** qualified as a TP=2 `no-promotion` comparison lane; no production
alias or router profile changed.

## Immutable identity

### Laguna S 2.1

- **Model:** `poolside/Laguna-S-2.1-NVFP4` revision
  `07614121b31898586430f189d27a25a0be310843`.
- **TP=1 served name:** `laguna-s-2.1-nvfp4`.
- **TP=2 served name:** `laguna-s-2.1-nvfp4-tp2`.
- **TP=1 runtime:** vLLM `0.23.1rc1.dev1327+gf25953cc5`, image reference
  `vllm/vllm-openai:nightly-f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1`.
- **TP=1 registry digest:** Not retained; the engine-revision tag is not a
  registry digest.
- **TP=2 runtime:** vLLM 0.25.1, image
  `sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089`.

### Laguna XS 2.1

- **Model:** `poolside/Laguna-XS-2.1-NVFP4` revision
  `07133fb3df1cc3111478e24ee71a823a598c8c2f` in the retained July 12
  evaluation.
- **Qualification:** no qualified served recipe resulted.
- **Retained runtimes:** vLLM image
  `sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`
  and SGLang image
  `sha256:0261d11a9bf5ef8041d68d55f0bac3f3375330e21353245e23ef461ffbe57da5`.

## Tested hardware and topology

### Laguna S TP=1

- **Measured:** one RTX PRO 6000 Blackwell 96 GB on an isolated endpoint.
- **Execution mode:** single-card, five or fewer admitted requests depending
  on the workload; the promotion contract served thinking disabled.

### Laguna S TP=2

- **Measured:** two equal RTX PRO 6000 Blackwell Max-Q cards.
- **Execution mode:** exclusive TP=2 over PCIe without NVLink, concurrency one.
- **Comparability boundary:** runtime, topology, admission, and workload differ
  from the TP=1 lane, so this is not a topology-only speed A/B.

### Laguna XS compatibility probes

- **Measured:** one RTX PRO 6000, isolated candidate endpoints.
- **Boundary:** the failures apply to the exact tested vLLM/SGLang recipes.

## Engine, quantization, KV, context, and concurrency recipe

### Laguna S TP=1 rollback-era recipe

- **Engine and image:** vLLM `0.23.1rc1.dev1327+gf25953cc5`, commit-tagged
  nightly image above.
- **Weights and KV:** compressed-tensors NVFP4 and FP8 KV.
- **Contract:** 262,144 tokens; request-level
  `chat_template_kwargs.enable_thinking=false`.
- **Recipe:** [single-card registry entry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml).

### Laguna S TP=2 refresh

- **Engine and image:** pinned vLLM 0.25.1 image digest above.
- **Weights and KV:** NVFP4 and FP8 KV.
- **Contract:** TP=2, 262,144 tokens, one admitted request, no DFlash, thinking
  disabled.
- **Recipe:** [TP=2 campaign registry](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml).

### Laguna XS rejected probes

- **vLLM:** FP8-KV and non-FP8 fallback probes only.
- **SGLang:** Laguna-specific CUDA 13 image with default and explicit template
  probes.
- **Recipe:** [retained experimental Compose](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.experiment.yml).

## Evidence by measurement class

### Laguna S TP=1

- **Status:** `functional`, `capacity`, and `quality`.
- **Measured:** retrieval passed at 32K, 128K, and 240K targets; repeated
  tools/session/intelligence checks passed. Short-output capacity completed
  10/10 at c1 and 40/40 at c8, with 75.46 and 83.24 aggregate tok/s.
- **Evidence:** [qualification finding](../../findings/2026-07-26-laguna-s-heavy-qualification.md)
  and its linked raw artifacts.

### Laguna S TP=2

- **Status:** `functional`, `capacity`, and `quality`; `no-promotion`.
- **Measured:** functional and repeated gates passed; 32K measured 1.97-second
  TTFT, 15,134 prefill tok/s, and 70.9 tok/s decode; 231,457 prompt tokens
  measured 31.85-second TTFT, 7,252 prefill tok/s, and 66.0 tok/s decode.
- **Evidence:** [campaign finding](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md),
  [32K artifact](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/laguna-s-21-capacity-32k-c1.json),
  and [240K artifact](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/laguna-s-21-capacity-240k-c1.json).

### Laguna XS

- **Status:** `compatibility-only` and `historical-invalid`; tested recipes
  rejected.
- **Measured:** the retained negative evidence includes corrupted text, 0/20
  tools, startup stalls, empty long-context output, and backend rejection.
- **Limit:** full response bodies/logs for some operator-observed attempts were
  not retained.
- **Evidence:** [engine A/B finding](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md)
  and [machine-readable failure](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/laguna-xs-21-nvfp4-sm120.failure.json).

## Decision and promotion state

### Retained or selected

- **Laguna S TP=1:** dated `rollback`-era qualified recipe. Do not interpret
  this as a current live assignment.
- **Laguna S TP=2:** `no-promotion`; retained as a qualified comparison lane.

### Rejected

- **Laguna XS:** `rejected` under the tested vLLM and SGLang configurations;
  reconsideration requires fresh immutable identity and complete evidence.

## Failures and gotchas

### Reasoning and evidence limits

- **Laguna S thinking:** the qualified contract disables thinking because the
  enabled path intermittently exhausted the completion budget without a
  visible answer.
- **Laguna XS retention gap:** some response bodies and complete logs were not
  retained, so those attempts remain historical-invalid rather than quality
  results.

### Runtime and topology limits

- **Laguna XS FP8 KV:** produced corrupted output and 0/20 tools.
- **Laguna XS fallback:** non-FP8 vLLM stalled; SGLang output remained
  repetitive/off-topic and its documented `trtllm_mha` path rejected sm_120.
- **Cross-lane comparison:** TP=1 and TP=2 results use different runtimes,
  admission, and workloads.

## Dated run history

- [2026-08-01 — dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-07-26 — Laguna S qualification and promotion](../../findings/2026-07-26-laguna-s-heavy-qualification.md)
- [2026-07-12 — Laguna XS evaluation](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md)
