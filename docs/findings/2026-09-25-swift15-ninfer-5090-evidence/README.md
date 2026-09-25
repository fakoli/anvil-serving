# Swift-1.5 NInfer RTX 5090 campaign evidence

This directory contains the sanitized evidence bundle for the dated finding.
The native benchmark artifacts remain authoritative; the files below provide a
consistent campaign-level index.

## Campaign boundary

- **Campaign ID:** `2026-09-25-swift15-ninfer-5090`
- **Capability:** text, tools, image/OCR, long context
- **Repository revision:** `e760e7b3c2438bc90a091d27c73fa1a39a9a78e8` plus this worktree's new recipe and finding
- **Evidence labels:** functional, bounded quality, descriptive capacity, failed long-context concurrency
- **Decision label:** user-authorized current secondary deployment with explicit 128K/C4 failure
- **Promotion boundary:** trusted router identity, exact install, routed clients, and repeat fleet convergence verified; topology receipts retained privately

## Common campaign artifacts

- [`artifact-manifest.json`](artifact-manifest.json) - role ledger, native
  schemas, file hashes, and explicit gaps
- [`source-registry.json`](source-registry.json) - dated source provenance and
  decision impact
- [`summary.json`](summary.json) - bounded machine-readable outcome and
  decision
- [`friction-log.md`](friction-log.md) - failures, workarounds, ambiguity, and
  recurring manual steps
- [`restoration.json`](restoration.json) - starting/ending state and post-run
  verification, or the reason restoration was not applicable

## Working campaign controls

- [`campaign-state.json`](campaign-state.json) - final stage and completed-cell ledger
- [`coverage-and-gaps.md`](coverage-and-gaps.md) - request-to-evidence matrix
  that keeps partial, rejected, and missing outcomes visible

These controls organize the review; they are not independent benchmark proof.

## Workload and plan

The [source-build scout recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-swift15-ninfer-dflash2-rtx5090-post-profile.toml)
and [baked deployment recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-swift15-ninfer-dflash2-rtx5090-baked.toml)
pin the model revision and runtime source. The public
[runtime Dockerfile](https://github.com/fakoli/anvil-serving/blob/main/configs/runtime/ninfer-bace20dc-swift15/Dockerfile)
produced the [verified immutable image](baked-build.txt). The preflight fixture image is
[`small-text-1.png`](https://github.com/fakoli/anvil-serving/blob/main/tests/computer_use/vision_diagnostic/assets/small-text-1.png),
SHA-256 `7a065d780311911f33cb2ccacaa21a1491cf81b6fcaf869d4ee0c5a5cb3e0a46`.
Native artifacts retain the thinking-mode and output budgets used by each gate.
The [frozen run plan](run-plan.md) fixes the promotion-first sequence and the
post-promotion benchmark cells.
The [workload manifest](workload-manifest.md) records the selected suites,
repetition counts, corpus hashes, and frozen SWE issue IDs.

## Raw run evidence

- [Failed first startup](startup-attempt-1.txt): unsupported card-sample flag,
  after artifact SHA verification.
- [Successful second startup](startup-attempt-2.txt): managed load, exact SHA,
  effective memory and KV pool.
- [Baked build and load](baked-build.txt): exact image ID, source label,
  bounded build, binary/artifact SHA checks, and effective startup capacity.
- [Smoke and JSON](direct-preflight-smoke.json),
  [tools and Responses](direct-preflight-tools.json),
  [vision and OCR](direct-preflight-vision.json), and
  [four-request tools](direct-preflight-c4-tools.json): `preflight/v2`,
  direct online functional checks, all passed.
- [First long-context probe](direct-preflight-250k.json): 206,339 actual prompt
  tokens, retrieval passed.
- [Near-limit long-context probe](direct-preflight-near-limit.json): 251,715
  actual prompt tokens, retrieval passed.
- [Baked-image direct preflight](baked-direct-preflight.json): smoke, JSON,
  four-request tools, streaming, continuation, Responses, image, and OCR all
  passed on the final image.

## Post-promotion benchmark evidence

- [Native routed agentic scout](agentic-routed-18.json): 17/18 with default thinking, including one `debug-loop` tool-protocol failure despite a correct final answer. [Sanitized durable-job evidence](agentic-routed-evidence.json) replaces the real model-host and worker identifiers; the case sidecar is byte-identical to its private SHA-256 `ef5a9b2ca159f2affa33e77ee2afb695093e78e31aff939af5e5ab9457b7fdd4`.
- [Native routed SWE-bench Verified scout](swe-routed-5.json): 5/5 officially graded and resolved on the frozen five-case selection, with default thinking and no stage or instance failure. The [sanitized durable-job evidence](swe-routed-evidence.json) and native run replace the real router URL, host IDs, and worker home paths. The original private worker stage SHA-256 is `bd794c1250ce23be840da6c3731ab97d85c880b49c0e33534b3bdf2df5c71a74`; the public copy is deliberately different. Setup attempts 001 and 002 failed before model execution because of missing explicit selection and a mismatched worker interpreter; their private artifacts are retained, and [friction](friction-log.md) records the correction. Five cases are not a broad coding ranking.
- [Native chat, tools, session, and bounded intelligence](quality-chat-tool-session-intelligence.json): tools 3/3, session 3/3, two deterministic intelligence checks 6/6.
- [Sanitized native routed context](context-routed-18.json): 18/18 exact markers at actual prompt depths 32,718, 130,918, and 257,898 with 4,096 output-token reserve. The private transport copy has SHA-256 `b94858933d724c4b9a2bb444477c012d79647174ab34baddda2e67d26d003f4e`; this public native evidence replaces the real model-host and isolated-worker identifiers. Its stage sidecar hashes refer to private worker artifacts and are not public file links.
- [Pinned 12-case image/OCR corpus](multimodal-computer-use-12.json): 12/12. Its source corpus path was sanitized to a repository-relative path without changing the scored observations.
- [4K/C1](capacity-4k-c1-visible-observe.json) and [4K/C4](capacity-4k-c4-visible-observe.json): 100/100 visible completions each; descriptive throughput with 0 exact output-target adherence.
- [32K/C1](capacity-32k-c1-visible-observe.json) and [32K/C4](capacity-32k-c4-visible-observe.json): 30/30 each; descriptive samples below the 100-request tail threshold.
- [128K/C1](capacity-128k-c1-visible-observe.json): 10/10. [128K/C4](capacity-128k-c4-visible-observe.json): **5/10**, with five HTTP 503 request-queue timeouts confirmed in managed engine logs; no throughput claim.
- [Near-limit C1](capacity-near-limit-c1-visible-observe.json): 3/3 at 252,223 actual prompt tokens, with limited output.
- [Default-thinking strict control](capacity-4k-c1.json) and [no-thinking strict control](capacity-4k-c1-no-think-probe.json): retained failures; output budget exhaustion and word-target nonadherence, respectively.

The serve profile stayed fixed for all benchmark cells. Per-request thinking was disabled only for visible-output throughput cells. No local IFBench, GSM8K, or method-matched reproduction of the publisher's performance table is claimed.

All preflight measurements are direct, warm-server scouts with one attempt per
cell; they do not establish sustained throughput, broad quality, or long-run
reliability. The first source build was cold; the second startup reused the
compiled runtime volume.

## Decision and publication

The [dated finding](../2026-09-25-swift15-ninfer-5090-promotion.md)
explains the host-memory adaptation and measured results. The baked image, routed promotion, and harness convergence are complete.
The role ledger and decision summary disclose remaining benchmark/publication gaps.
