# GLM EXL3 runtime replay and DCP1 mitigation investigation

Date: 2026-09-23 UTC. Measured hardware: two RTX PRO 6000 Blackwell
Max-Q 96 GB GPUs over PCIe, native Linux, driver 615.71.09. Evidence is
`functional` and bounded runtime diagnostics; decision `no-promotion`.

<!-- benchmark-result-card/v1 -->

## Result card

The current DCP2 configuration reproduced a CUDA illegal-memory-access crash
when a new 32K prompt arrived during 175K-context decoding. A serial control
passed. The single-setting DCP1 candidate passed three matched overlaps.
This isolates a useful mitigation direction, not the first faulty kernel.

- Same immutable 4-bpw EXL3 weights and v84 runtime; FP8 DS-MLA KV, APC,
  TP2/EP2, no speculation, batch 2,048, configured context 327,680 and C4.
- Parent serial: 174,798 / 31,820 actual input tokens, both retrieval checks
  passed. Parent overlap: 174,798 / 31,816, engine death and Xid 31 on both GPUs.
- DCP1 overlap: 3/3 passed at 174,797–174,800 anchor and 31,815–31,822
  contender input tokens. Runtime, token, retrieval and client-overlap gates
  passed; each anchor reached its 4,096-token diagnostic cap.
- Reported startup KV capacity fell from 1,416,244 to 825,268 tokens.
  Four simultaneous full configured windows would exceed the DCP1 pool.
- Separate request-policy control: strict JSON passed 0/3 with thinking
  disabled and 3/3 enabled. This fixture-specific result does not explain CUDA faults.

Measurement path: direct local online endpoints; authenticated routed checks
are restoration evidence. Model weights and compiled cache were warm; each
unique stability run used fresh synthetic prefixes. These are diagnostic
samples, never throughput-ranking or natural long-output-quality evidence.

Evidence: [index](2026-09-23-glm-runtime-stability-evidence/README.md),
[manifest](2026-09-23-glm-runtime-stability-evidence/artifact-manifest.json),
[authoritative summary](2026-09-23-glm-runtime-stability-evidence/summary.json),
[recipe reconstruction](#recipe-reconstruction).

## Question and immutable identity

Ordinary short correctness checks and serial long-context requests did not
exercise the observed Pi failure pattern. The new
[runtime stability command](../benchmarks/runtime-stability.md) introduces
fresh prefill after observing real output from a long-context anchor. Three
independently generated values provide literal retrieval checks without
copying private conversations.

Target: `brandonmusic/GLM-5.3-Flash-tr3-4bpw` at
`a5fee929cf4888b1824323e33e8a19b60129e025`. Runtime:
`verdictai/glm53-flash-exl3-k4:r19-sm120-tp2-ep2-dcp2-v84-language-only@sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692`.
The package reports `0.1.dev20111+g7f1e92bec.d20260827`; imported Git revision
`6dc2f516688fe6f84c6994dcd20fddf296853a6c` is only partial source provenance.
The downstream `sparse_attn_indexer_kpool.py` addition is absent from the
public tree at that revision. Do not describe this as stock upstream vLLM.

The parent is the retained r10 APC recipe. The candidate changes only
`--decode-context-parallel-size 2` to `1`, apart from isolated endpoint/name
metadata. Engine startup reports effective DCP1 and normalizes the unused
DCP communication backend to `ag_rs`; the requested `a2a` flag remains in
the recipe. The publisher's immutable image tag still contains `dcp2`.

## Reproduction and attribution

At 16:57:18 UTC, the DCP2 owner dump scheduled one anchor decode token with
174,807 computed tokens and 10 output tokens alongside 2,047 new prefill
tokens. Two requests were running, none waiting, KV usage was 14.96%, and
both speculation and multimodal features were absent. This is engine-side
evidence of the mixed batch, beyond the harness's client-overlap observation.

Both GPUs reported Xid 31, `FAULT_PDE`, `ACCESS_TYPE_VIRT_WRITE`. The engine
reported illegal memory access, then `EngineDeadError` and process exit.
The implicated stack passed through `sparse_attn_indexer_kpool`,
`_merge_kpool_dcp_topk`, `_merge_b12x_dcp_topk`, and
`triton_gather_topk_ids_by_position`. The upgraded driver did not prevent
this reproduction. A later CUDA synchronization point can report an earlier
invalid operation; this stack is not first-fault proof.

The imported DCP merge helper returns immediately for world size one,
motivating the DCP1 trial. Core dumps, Compute Sanitizer, reduced kernel
reproduction, high-page-ID tests and allocation/graph poisoning were not run.
Kernel root cause remains unresolved. The known-crashing parent was not
deliberately rerun for reverse-order symmetry.

The first runner version omitted SSE error frames and chunk IDs. Its native
parent artifact remains unchanged with its original protocol classification.
[Separate correlation](2026-09-23-glm-runtime-stability-evidence/crash-175k-correlation.json)
binds the owner IDs, timestamps, shapes and kernel faults. Later runner
versions preserve exact bounded stream IDs and upstream error frames.

## Correctness, trial limits and negative results

Thinking-disabled requests leaked reasoning-like prose into visible output
and failed strict JSON with extra data. Alternating enabled/disabled repeats
retained all six outcomes. Enabled smoke, JSON, needle and tool preflight
passed for both configurations. Temperature was zero; repeated fixtures and
cache reuse limit generalization. No client reasoning default was changed.

The first DCP1 small overlap accidentally used a 512-token anchor allowance
instead of the parent's 2,048. It produced reasoning deltas without a visible
answer; the contender passed retrieval and the engine remained healthy.
Budget exhaustion is plausible, but the original runner discarded returned
usage/finish metadata on validation failure, so it is not proven for that
attempt. The failed artifact remains retained. The corrected matched small
trial passed. A real CLI regression now preserves that metadata and labels
a protocol-complete response without visible content `semantic_output_absent`.

Successful stability evidence requires managed identity before and after
every round, terminal streams, actual usage consistent with tokenizer counts,
both literal retrieval checks and the intended client overlap. It does not
prove a particular successful scheduler batch. Synthetic canaries differ
between runs; near-matched token counts are not identical prompt bytes.
The visible capture is capped at 8,192 characters. A `length` finish is
allowed for exposure, not evidence of a naturally complete or coherent guide.

## Tested matrix and untested envelope

| Configuration / case | Rounds | Actual anchor / contender input | Outcome |
| --- | ---: | --- | --- |
| DCP2 small overlap | 1 | 4,089 / 1,017 | All diagnostic gates passed |
| DCP2 large serial | 1 | 174,798 / 31,820 | All diagnostic gates passed |
| DCP2 large overlap | 1 | 174,798 / 31,816 | CUDA crash and two GPU Xid31 faults |
| DCP1 small, mismatched 512 cap | 1 | 4,092 / 1,021 | No visible anchor answer; engine healthy |
| DCP1 small, matched 2,048 cap | 1 | 4,088 / 1,016 | All diagnostic gates passed |
| DCP1 large overlap | 3 | 174,797–174,800 / 31,815–31,822 | All diagnostic gates passed; no new Xid/OOM |

All large anchors used a 4,096-token output cap and all large contenders a
1,024-token cap. The three passing DCP1 contenders emitted 214, 145 and 153
tokens; anchors each emitted 4,096 and finished with `length`. These are three
bounded exposures, not a measured service failure rate.

The time guard refused the proposed 201K/47K case before requests: 139.46
seconds remained, below the declared 145-second admission margin. No 201K,
310K, repeated-prefix, cache-churn/cancellation or full-window C4 result exists.
The conservative simultaneous demand of the measured large replay was
213,100 tokens including token-calibration tolerance and output ceilings,
below the observed 825,268-token pool. Fit arithmetic admits an experiment;
it does not establish correctness beyond the tested shape. Candidate vision,
OCR, real clients, broad agentic/SWE quality and natural long-output coherence
were not requalified. They remain required before replacement deployment.

## Recipe reconstruction

The sanitized [parent recipe](2026-09-23-glm-runtime-stability-evidence/baseline-recipe.toml)
and [DCP1 trial recipe](2026-09-23-glm-runtime-stability-evidence/candidate-dcp1-recipe.toml)
retain immutable weights/image, parser and kernel settings, flags and
containment. They substitute a generic model-cache path and loopback ports.
The model materialization manifest must match the retained SHA-256 before
startup. Supply the two local GPU identities through managed recipe loading;
keep operator topology and credentials private. Review the managed preview
before confirmation and retain the rendered local recipe digest.

Important retained settings: uniform K4 EXL3, FP8 DS-MLA KV, B12X sparse MLA,
TP2/EP2, APC, chunked prefill, batch 2,048, max sequences four, 0.970 GPU
utilization, image limit eight and no video or speculation. Host containment
is 52 GiB RAM plus 1 MiB swap, with a 12 GiB host reserve and startup checks.
Neither memory limits nor the reserved desktop GPU were changed.

Use the [documented stability workflow](../benchmarks/runtime-stability.md)
with scenario identities from the actual managed load. Match both output
caps and reasoning policy to the parent. Stop on GPU faults, engine death,
identity drift, malformed output, missed overlap or unrelated workload.
Preserve logs and kernel evidence before managed recovery.

This is an experimental mitigation recipe. It is not a promoted replacement,
a reliability guarantee, a full C4/327K qualification, or a demonstrated speed
improvement. A deployment decision still needs its protected quality,
modality, real-client, sustained runtime and workload-specific capacity gates.

## Repeatable workflow changes

The existing qualification skill now covers incident reproduction,
single-setting trials, bounded envelope searches and a complete recipe
notebook. Independent skill review used paired decisions and a fresh transfer
case; this was instruction/behavioral evaluation, not a measured model-quality
gain. Independent runner review preceded live use and caught identity,
overlap, deadline, malformed-input, resource and evidence-retention defects.
The additive `anvil-serving.stability/v1` schema keeps failed diagnostics out
of performance comparisons. Full source and review identities are retained
with the campaign evidence.

The [source registry](2026-09-23-glm-runtime-stability-evidence/source-registry.json)
pins LIL mixed-traffic, cache and kernel-boundary methods and official vLLM
CUDA debugging guidance. External recipes selected experiments; they supplied
no local pass. Failed cells, missing probes and restoration remain part of
the shareable record.

## Restoration and closure

The DCP1 candidate was unloaded and the original managed DCP2 recipe restored.
Recipe/registry digests, immutable image, checkpoint, served name, port and GPU
assignment matched the starting state. The router file hash and exclusive
operating-mode ownership were unchanged. Startup again reported DCP2, the
original KV pool and the containment gate; no Xid/OOM appeared during recovery.
Enabled-thinking smoke and JSON passed both directly and through authenticated
`llm.primary`. See the [restoration receipt](2026-09-23-glm-runtime-stability-evidence/restoration.json).
This establishes recovery, not remediation of the reproduced crash.

The diagnostic window is closed. The [coverage ledger](2026-09-23-glm-runtime-stability-evidence/coverage-and-gaps.md)
keeps replacement qualification and the larger hardware envelope open; no
production settings, client defaults or routes changed. The next useful
campaign would extend the passing DCP1 point to the second incident shape,
then explicit cache-state exposure and protected quality/modality/client gates.
A CUDA core dump or reduced-kernel oracle is still needed for first-fault claims.

The [source receipt](2026-09-23-glm-runtime-stability-evidence/development-source.json)
and [independent skill review](2026-09-23-glm-runtime-stability-evidence/skill-review-report.txt)
separate executable/skill validation from model evidence. The final
[scenario-comparison review](2026-09-23-glm-runtime-stability-evidence/skill-scenario-comparability-review.json)
requires retained mechanical diffs, preventing accidental output-budget changes
from being treated as a matched configuration trial. No external benchmark
score or reviewer opinion was used to validate the model's own answers.
