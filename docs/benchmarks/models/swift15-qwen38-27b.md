# Swift-1.5 Qwen3.8 27B NInfer

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Selected secondary chat/model route in the dated 2026-09-25 promotion record.
    - **Selected or best-qualified configuration:** All-NVFP4 W4A4, NInfer DFlash2 K=7, K8V4 KV, 262,144 total tokens, C4 configured admission, vision on.
    - **Measured hardware:** One RTX 5090, 32,607 MiB, Windows 11 / Docker Desktop / WSL2.
    - **Evidence:** Functional, bounded quality, descriptive capacity, and routed client acceptance. Near-limit direct retrieval passed at 251,715 actual prompt tokens, routed context passed 18/18 through 257,898, and frozen official SWE passed 5/5; 128K/C4 completed only 5/10 requests.
    - **Decision:** User-authorized `current` secondary deployment; prior primary route retained as rollback.
    - **Important limitation:** C4 at 128K generated five HTTP 503 queue timeouts. Exact 256-word output adherence failed in the visible-output capacity workload, so throughput is descriptive.
    - **Review dates:** evidence through 2026-09-25; dossier reviewed 2026-09-25.

The [dated result](../../findings/2026-09-25-swift15-ninfer-5090-promotion.md) separates deployment acceptance from benchmark qualification. The [managed recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-swift15-ninfer-dflash2-rtx5090-baked.toml) is the reproducible public configuration.

### Review narrative

#### 2026-09-25 — Deployed final profile and measured its limits

The community post's single-5090 profile was brought up as a pinned, baked managed serve and selected for the secondary route before capacity benchmarking. The exact card sample could not allocate 48 GiB Host KV on a 30.91 GiB RAM host; 2 GiB Host KV and four state slots were used. Final direct, routed, client, and limited quality gates passed. The 128K/C4 capacity cell exposed serving queue timeouts and prevents a full-concurrency long-window qualification. **Outcome:** selected secondary route with explicit long-context concurrency limit; publisher results remain external priors.

## Immutable identity

### Swift-1.5 NInfer DFlash2

- **Model:** `kaushikvira/Qwen3.8-27B-swift15-nvfp4full-dflash2-NInfer-v3@ff891a1130adfcf46bf745e06f0fa3dcde2e8c82`; served as `qwen38-swift15-ninfer-dflash2-c4-262k`.
- **Runtime:** NInfer `bace20dc70249eed6402b66d4852c6c3f9612905`; baked image `sha256:59df48dd99f0d177b73de57ba88eb6457ed7e4b0c5771c45e8976adcd9f5ef67`.
- **Artifacts:** All-NVFP4 model file SHA-256 `16f313c043c06a19f7c27d74f8259ed9d86c8cb5beed631efbb96f392b373d96`, with embedded z-lab DFlash2 drafter.
- **License:** See the pinned model repository and its upstream provenance; the retained local evidence does not adjudicate downstream reuse rights.

## Tested hardware and topology

- **Measured:** One RTX 5090, 32,607 MiB, PCIe, split GPU mode on Windows 11 / Docker Desktop / WSL2.
- **Protected or co-resident:** No other GPU measured. Primary route and unrelated services were protected; isolated benchmark clients ran off the model host for routed jobs.
- **Execution mode:** Managed serve on the measured host; direct capacity over a local tunnel and separate routed client acceptance.
- **Comparability boundary:** Publisher's posted 256-token probe, ThinkingCap strict short-output capacity, and this model's variable-length `observe` workload are different populations.

## Engine, quantization, KV, context, and concurrency recipe

### Deployed baked profile

- **Engine and image:** NInfer source revision and immutable image above.
- **Weights and KV:** All-NVFP4 W4A4 group size 16; K8V4 device KV, 2 GiB pinned Host KV, four Host State slots.
- **Topology:** Single GPU, no tensor-parallel replica.
- **Contract:** 262,144 total tokens, 32,768 maximum output, C4 admission, one-image vision, thinking enabled with 16K default budget.
- **Runtime controls:** Automatic KV pool, measured 282,112 shared tokens, DFlash2 K=7 with LM-head draft, 4,096 prefill chunk, 450 W power cap.
- **Recipe:** [baked managed recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-swift15-ninfer-dflash2-rtx5090-baked.toml); [source-build scout](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-swift15-ninfer-dflash2-rtx5090-post-profile.toml).

## Evidence by measurement class

### Functional, routed, and quality

- **Status:** `functional` and bounded `quality`; final image and route healthy.
- **Measured:** Exact direct retrieval at 251,715 actual prompt tokens and routed context 18/18 through 257,898; tools/session/intelligence diagnostic 12/12 subchecks; routed synthetic agentic 17/18 (one `debug-loop` protocol failure); frozen official SWE-bench Verified 5/5; pinned image/OCR corpus 12/12; routed Mini OpenClaw/Hermes 2/2 and Windows Pi tool-read acceptance.
- **Limits:** The diagnostic, agentic, and five-case SWE scouts are not IFBench, GSM8K, or broad coding quality. Real client receipts remain private because they contain operator topology.
- **Evidence:** [finding](../../findings/2026-09-25-swift15-ninfer-5090-promotion.md), [direct and quality artifacts](../../findings/2026-09-25-swift15-ninfer-5090-evidence/README.md).

### Routed agentic scout

- **Status:** Bounded `quality`/tool protocol evidence; profile pass floor met.
- **Measured:** 17/18 deterministic cases with default thinking; one `debug-loop` protocol failure despite a correct final answer.
- **Limits:** Synthetic scenarios only, not a repository-level coding benchmark.
- **Evidence:** [native case record](../../findings/2026-09-25-swift15-ninfer-5090-evidence/agentic-routed-18.json) and [sanitized durable-job evidence](../../findings/2026-09-25-swift15-ninfer-5090-evidence/agentic-routed-evidence.json).

### Routed repository scout

- **Status:** Bounded `quality`; official SWE-bench Verified grading complete.
- **Measured:** The frozen five-case selection was graded and resolved 5/5 with one pinned mini-SWE-agent worker and the official grader. The agent and grader stages exited successfully.
- **Limits:** Five issues do not establish a full-suite score. The worker recorded no per-instance token counts or durations; prior results used different worker environments, so no causal improvement is claimed.
- **Evidence:** [sanitized native SWE run](../../findings/2026-09-25-swift15-ninfer-5090-evidence/swe-routed-5.json) and [durable-job evidence](../../findings/2026-09-25-swift15-ninfer-5090-evidence/swe-routed-evidence.json).

### Routed long context

- **Status:** `functional` C1 routed context, 18/18 exact-marker passes.
- **Measured:** Six cases at each actual prompt depth 32,718, 130,918, and 257,898, across three needle positions and two repetitions; 4,096 output-token reserve.
- **Limits:** No concurrent long-window proof or engine telemetry in this job. The served configured-context field was unavailable to this worker.
- **Evidence:** [sanitized native context artifact](../../findings/2026-09-25-swift15-ninfer-5090-evidence/context-routed-18.json).

### Capacity and failure

- **Status:** Descriptive `capacity`; 128K/C4 incomplete and performance-ineligible.
- **Measured:** ~3.7K/C1 and C4 both 100/100 visible completions, at 328.21 and 615 aggregate visible tok/s. ~31K/C1 and C4 both 30/30; ~126K/C1 10/10; 252,223 actual-token C1 scout 3/3. ~126K/C4 5/10 with five engine queue timeouts.
- **Limits:** No exact 256-word completions; thinking disabled only for visible decode measurement. Request populations below 100 have descriptive tails only. These numbers do not reproduce the publisher's ~160.8 tok/s claim.
- **Evidence:** [native capacity bundle](../../findings/2026-09-25-swift15-ninfer-5090-evidence/README.md) and [failure interpretation](../../findings/2026-09-25-swift15-ninfer-5090-promotion.md#post-promotion-measurements).

## Decision and promotion state

The secondary alias was updated after user authorization and exact route/client gates. The primary route was retained. The former ThinkingCap profile remains a historical 128K/C1 qualified measurement and operator rollback, not an active secondary assignment after this dated promotion. The selected NInfer serve remains at its final settings; the observed 128K/C4 failure is a capacity limit requiring separate qualification before it could be advertised as reliable.

## Failures and gotchas

### Evidence and interpretation limits

- **Output control:** The model did not produce the exact requested 256-word code sequence in the capacity workload. All successful `observe` throughput cells are descriptive only.
- **Publisher comparison:** IFBench, GSM8K, 200K prefill, and posted decode numbers were not rerun with matching methods.
- **Long-context concurrency:** Five of ten 128K/C4 requests received HTTP 503 queue timeouts; the container stayed up.

### Runtime, topology, or integration limits

- **Host RAM:** The card's 48 GiB Host KV example cannot fit on the 30.91 GiB host. The 2 GiB adaptation changes the memory envelope.
- **Pinned CLI:** `--image-token-budget` was unsupported at the selected NInfer revision and was removed after a retained failed startup.
- **SSE error visibility:** The benchmark request parser previously omitted error chunks without an observer; the parser now raises the engine error and has a regression test.

## Dated run history

- 2026-09-25 — [exact recipe feasibility and first functional gates](../../findings/2026-09-25-swift15-ninfer-5090-exact-recipe-feasibility.md).
- 2026-09-25 — [promotion, post-promotion benchmark, and native evidence](../../findings/2026-09-25-swift15-ninfer-5090-promotion.md).
