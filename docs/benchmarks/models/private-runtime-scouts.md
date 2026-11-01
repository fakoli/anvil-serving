# Private target: cache-layout and speculation scouts

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** local text-generation runtime experiments on an existing private target.
    - **Selected configuration:** retain the original MTP3, TP2/DCP2, FP8-KV, 327,680 configured-context/C4 recipe.
    - **Measured hardware:** two RTX PRO 6000 Blackwell Max-Q GPUs on native Linux.
    - **Evidence:** bounded `functional` and `capacity` scouts; 144 timed requests passed per experiment, with no 10% E2E gain against both controls.
    - **Decision:** `no-promotion`; original configuration restored after both trials.
    - **Important limitation:** target and asset identities are private; variable outputs and absent canaries/no-speculation control limit performance interpretation.
    - **Review dates:** evidence and dossier reviewed 2026-11-01 UTC.

The [dated finding](../../findings/2026-11-01-runtime-scouts.md) retains both
experiments separately. The cache-layout change added reported token slots but
did not improve response speed. DFlash2 K3 worked but reduced those slots without
a material latency win. **Outcome:** retain the current configuration; no new
quality, model-family or hardware ranking follows.

## Immutable identity

Target repository, revision, served identity, draft identity and asset
fingerprints are withheld by the operator's privacy boundary. Each trial used
one fixed target and runtime across its three arms; exact private restoration
checks passed. Public aliases are descriptive placeholders, not model names.
This dossier cannot establish compatibility for a publicly named checkpoint.

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q GPUs, 96 GB class each, TP2/DCP2, native Linux,
300 W per-card limits. Both cards executed the measured workload. No other
device contributes a performance result. Hardware and resource observations
are dated to these trials and are not fleet status.

## Engine, quantization, KV, context, and concurrency recipe

[Sanitized configuration and reproduction boundary](../../findings/2026-11-01-runtime-scouts-evidence/reproduction.md).

The pinned vLLM fork, FP4 weights, FP8 target KV, C4 scheduler, batch 4,096,
7,650,410,496 KV bytes/card, 52 GiB host-memory ceiling and zero container swap
were fixed. Baseline speculation was MTP3. The layout trial changed internal
maximum context from 327,680 to 1,048,576. The separate DFlash trial kept 327,680
and tested DFlash2 K3, draft TP2, TRITON_ATTN, BF16 draft KV and standard rejection.
Neither candidate became a selected router destination.

## Evidence by measurement class

### Bounded functional and performance scouts

- **Status:** `functional`, bounded `capacity`; no new full-role qualification.
- **Measured:** 144/144 timed requests per experiment; twelve request timings per
  pooled input/concurrency group. No group met the 10% E2E gate against both
  controls. Cache slots changed +38.60% for layout and −40.63% for DFlash.
- **Limits:** approximately 6K/25K actual prompts, requested C1/C4, natural output,
  absent canaries and no matched no-speculation control; private immutable identity.
- **Evidence:** [finding](../../findings/2026-11-01-runtime-scouts.md),
  [raw evidence index](../../findings/2026-11-01-runtime-scouts-evidence/README.md),
  [manifest](../../findings/2026-11-01-runtime-scouts-evidence/artifact-manifest.json).

The direct protocol screen and restored routed checks passed. Timing and
resource evidence explain the retention decision without claiming broad coding
quality, vision, full-window concurrency, endurance or causal speedup.

## Decision and promotion state

Retain the exact original MTP3 configuration and cache layout. Both alternatives
are `no-promotion` for this evaluation. Successful loading and bounded protocol
checks did not establish an improvement that justified replacing the original.
No serving or route change accompanies this documentation update.

## Failures and gotchas

- **Output length:** token throughput can rise because the model generated more
  tokens; compare response latency and retain both controls.
- **Cache accounting:** reported slots are allocation evidence, not completed
  full-context requests. BF16 draft KV consumes part of the fixed budget.
- **Privacy:** exact target and runtime identities are withheld; public native
  artifacts support numerical inspection but not full independent reproduction.
- **Scope:** zero timed failures does not replace coding, vision or endurance gates.

## Dated run history

- 2026-11-01 — [Cache-layout and DFlash scouts](../../findings/2026-11-01-runtime-scouts.md):
  separate before/candidate/restored controls; retain original, no promotion.
