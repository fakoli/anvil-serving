# Qwen3.8 efficient fine-tunes and Minitron

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** direct RTX 5090 replacement candidates, not promoted services.
    - **Selected or best-qualified configuration:** no qualified replacement;
      tested Signal and Swift 27B Q6_K/MTP3, Qwopus Flash 27B
      Q6_K/no-spec, and Minitron 20B Q6_K/no-spec; all 64K/C1.
    - **Measured hardware:** one NVIDIA RTX 5090, 32,607 MiB, Windows/Docker/WSL2.
    - **Evidence:** functional passes for all four; bounded quality and failed
      controlled-output scouts. These are not broad benchmark accuracy scores.
    - **Decision:** retain the exact incumbent; tested challengers `no-promotion`.
    - **Important limitation:** no challenger has qualified the incumbent's
      262K deployment contract or the declared complete-output performance gate.
    - **Review dates:** evidence cutoff and review 2026-09-12.

[Managed recipes](../../findings/2026-09-12-qwen38-efficient-variants-rtx5090-evidence/recipes.toml),
[no-spec controls](../../findings/2026-09-12-qwen38-efficient-variants-rtx5090-evidence/nospec-controls.toml),
[decision](#decision-and-promotion-state), and
[complete evidence index](../../findings/2026-09-12-qwen38-efficient-variants-rtx5090-evidence/README.md).

### Review narrative

The four candidates fit and served the functional workload. Their efficiency
claims did not remove strict output or bounded quality failures. The exact
incumbent has been restored and passed its post-run checks. It scored 8/10
thinking-on and 21/30 thinking-off, versus Signal's 9/10 and 24/30. Signal is
the best new efficiency research lead, but not a qualified full-contract
replacement. The incumbent was faster on the revised cold short-output scout.

## Immutable identity

| Recipe family | Weight repository | Exact revision |
|---|---|---|
| Signal 27B | `agentionai/Signal-3.8-27B-GGUF` | `9848da210febc2141edb7e3f89cfc307a0d15163` |
| Swift 27B | `ukisai/Swift-Qwen3.8-27B-GGUF` | `dfc5e7382fa86bd3b971108e23423be5ef18941e` |
| Qwopus Flash 27B | `mradermacher/Qwopus3.8-27B-Flash-i1-GGUF` | `a2f33e11aa22e467206633d7af9da1990f7111b9` |
| Minitron 20B | `mradermacher/Qwen3.8-20B-Minitron-i1-GGUF` | `ef2d8cbbb83bbe27142546665163d2ab9d31727c` |

The [source registry](../../findings/2026-09-12-qwen38-efficient-variants-rtx5090-evidence/source-registry.json)
separates upstream fine-tunes from quantizers and records model-card priors.
Swift's pinned source license has a commercial revenue threshold; eligibility
was not assumed. Signal and Minitron cards identify Apache 2.0.

## Tested hardware and topology

One RTX 5090 (sm120), driver 616.64; direct loopback endpoint under Docker/WSL2.
The incumbent was retained stopped while one candidate ran at a time. The
pre-existing Windows camera utility was protected. No other GPU was measured.
The router was absent at baseline; no client catalog or route was changed.

## Engine, quantization, KV, context, and concurrency recipe

All four use llama.cpp source
`a298422da78eb75e440a7de0ca408af64d323d93`, CUDA server image digest
`sha256:cf2e30bc855cf58cdbdc65d05b5b5e02afa95fb788343a5334d704367ac5c9ac`,
Q6_K weights, Q4_0 K/V, full GPU layer placement, Flash Attention, 64K context,
C1, 512/256 batch/microbatch, eight threads, Jinja, and per-request thinking
control. Signal/Swift have matched no-spec variants. Exact filenames,
sampling, graph settings, and flags are in the linked recipes.

## Evidence by measurement class

### Four-model functional and quality scout

- **Status:** `functional`, diagnostic `quality`, failed strict `capacity`.
- **Measured:** every original candidate passed all six preflight checks,
  including tools 20/20 and exact retrieval at 47,349 actual prompt tokens.
  Thinking-enabled ten-question strict scores: Signal 9/10, Swift 9/10,
  Qwopus 1/10, Minitron 3/10.
- **Limits:** scores count answer-format and budget failures; one repetition
  cannot support general knowledge accuracy or a 2% quality-loss bound.
- **Evidence:** [raw runs and interpretation](../../findings/2026-09-12-qwen38-efficient-variants-rtx5090-evidence/README.md).

Signal and Swift both exhausted the same computer-science question at 5120
total completion tokens. Qwopus had eight format failures and one exhaustion;
disabling thinking improved strict repeated results to 24/30 but did not remove
budget failures. Minitron passed 15/30 with thinking disabled and used 24,580
completion tokens in the enabled scout, versus 10,966/11,020 for Signal/Swift.
Smaller did not mean less reasoning in this bounded sample.

### Controlled-output failures and diagnostic repair

- **Status:** negative `capacity`, not a speed ranking.
- **Measured:** Signal and Swift completed 0/5 exact 128-word requests with MTP and
  again without it. Swift no-spec and Qwopus each passed 5/5 revised 32-word
  requests; Minitron completed 0/5.
- **Limits:** original failures remain. Swift's revised population was warm
  while Qwopus was cold; their timings are not a matched comparison. No
  speculation speedup is established. N5 p99 is descriptive only.
- **Evidence:** [failure ledger](../../findings/2026-09-12-qwen38-efficient-variants-rtx5090-evidence/friction-log.md).

## Decision and promotion state

### Retained or selected

The operator authorized unattended best-supported selection and promotion.
That authority does not turn a failed gate into qualification. Keep the
existing 262K Unsloth GGUF/MTP3 deployment: exact restoration and all six
post-run functional gates passed. No route or client catalog changed.

### Rejected or incomplete

The four tested 64K profiles remain `no-promotion`; none is a qualified 262K
replacement. Qwopus and Minitron have additional quality failures. Signal is
an efficiency research lead, not a proven full-contract upgrade.

## Failures and gotchas

- **Capacity calibration:**57344 nominal filler produced 47,349 actual tokens;
  do not call that a57K-plus8K qualification.
- **Invalid speed:** repetitive cap-hitting responses are failures, not useful
  decode throughput. Removing speculation did not repair 128-word adherence.
- **Quality interpretation:** strict syntax, visible budget, hidden reasoning,
  and factual correctness are separate failure causes. The ten-question
  fixture is diagnostic and not a representative MMLU-Pro evaluation.
- **Timing contamination:** repository tests overlapped some diagnostic
  quality collection; those wall times are not clean finalist latency.
- **Lifecycle tooling:** retained-container restoration required guarded
  product fixes. Independent review accepts serialized campaign use but holds
  general release pending shared lifecycle locking and immutable process
  attestation. The separate Windows Pi transport fixes passed a real smoke.

## Dated run history

- 2026-09-12 — [Efficient-variant finding](../../findings/2026-09-12-qwen38-efficient-variants-rtx5090.md)
  and [complete evidence](../../findings/2026-09-12-qwen38-efficient-variants-rtx5090-evidence/README.md).
