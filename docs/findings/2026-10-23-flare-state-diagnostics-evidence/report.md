# Flare state diagnostics — protected regression follow-up

## Executive outcome

**In progress. No qualification, default change, promotion, upstream submission,
publication bundle, or hosting has occurred.** The completed protected A/B/B/A
bracket, native state roundtrip, and bounded top-20 observations are
diagnostic evidence. The separately paired scheduler-trace action is closed, normalized, and
independently accepted as bounded diagnostic evidence. It remains diagnostic
only and does not alter the decision.

The prior cache-fix campaign remains the decision authority: its numerical gate
failed and its protected r3 observations remain diagnostic. This campaign
retains rather than erases that failure. It can reproduce or narrow retained
observations; it cannot prove an allocator cause, a general Flare benefit,
incumbent parity, or numerical fidelity.

## Configuration and completed protected bracket

| Arm | Row share | Rows / pages | Runtime identity |
| --- | ---: | ---: | --- |
| A | 25% default | 52 / 7,314 | source `47581ccac82660953a5b59cb04b2750d64edc97d`; v2 patch `b132065b27532028bf2a02e754e86553ea846281f616f22ce74e89442ff0ec76`; image `sha256:d8fb208298fd3dd3f6b2b631148b01467a9449a9dcd1fef7dca304714bcf9411` |
| B | 75% candidate | 158 / 2,434 | same frozen source/image; timing registry with transfer-byte audit disabled |

The closed `formal/protected-abba-2/` bracket uses four fresh incarnations per
lane in A/B/B/A order. It completed 264 requests: 24 warmups, 96 prime, and
144 measured. Every phase has expected = attempted = completed, with zero
failures and zero missing. All matched A/B pairs have identical case IDs, output
token IDs, and complete work: 24/24 for each `small12` pair and 12/12 for each
`pageheavy` pair. The detailed descriptive result is
`analysis/protected-abba-2.{json,md}`; its metric summaries remain diagnostic
and do not change the historical 3% protected threshold.

## Closed state and top-20 diagnostics

Window 1 remains retained failed evidence: native endpoint 512 failed, then
restoration succeeded. Two bounded source repairs followed. Five compile-only
passes (three initial and two repair passes) prove buildability only; none was
GPU execution. Window 2 closed
with no experiment or restoration error, one retained generation, and unchanged
input bindings before native work, after native work, and after HTTP
observation.

The native Snapshot A → Restore → Snapshot B oracle had one successful
process return and two passing rank checks at each of endpoints 512, 513, and
7708. Each rank check compared 81,464,576 snapshot bytes; canonical snapshots,
poison verification, source guards, and final drain all passed. This was the reduced four-snapshot/1024-page diagnostic geometry, not
the serving-cache configuration; it did not exercise host offload or sampling.

Three warmups and four top-20 HTTP observations completed. Case 40 retained
exact top-20 agreement over all eight same-history sampled positions. Case 51
kept the same eight sampled IDs, but its top-20 values ceased to be exact at
positions 3–7 (minimum overlap 18/20; largest common absolute logprob delta
0.500295639). The comparator stops at the first output-ID divergence; neither
pair diverged within the returned eight IDs. Case 51 also has a material
batch-shape confounder: first and repeat have the same aggregate MTP work (3
rounds, 5 offered, 4 accepted, 7 emitted), but nonzero labels 1/2/4 versus only
1. This prevents assigning the top-20 difference solely to restored state,
cache, MTP acceptance, or a specific kernel. This is bounded localization only,
not a full-logit or exact-128 oracle. The authoritative compact evidence is
`analysis/state-logprob.{json,md}` and its independent review is
`review/state-results-review.md`.

## Evidence status

| Evidence | Current state | Claim boundary |
| --- | --- | --- |
| Prior protected baseline | retained at `analysis/protected-baseline.{json,md}` | r3 small12/pageheavy observations only; not allocator causation |
| Frozen inputs and phase wrapper | retained under `harness/frozen-prior/`; plan hash-bound | provenance, configuration, and denominator control |
| Protected ABBA-1 | retained interrupted evidence; no timed phases | failure/recovery provenance only |
| Protected ABBA-2 | closed and descriptively analyzed | complete matched protected populations; not performance qualification |
| Native state oracle | Window 2 closed; endpoints 512/513/7708 passed both ranks | exact exercised state roundtrip only; not logits, sampling, DMA, or serving-cache proof |
| Top-20 logprob discriminator | Window 2 closed | same-history sampled-distribution localization only; not full logits or exact-128 repair |
| Scheduler trace | closed; normalized; independently accepted diagnostic evidence | 4 capture groups, 72/72 measured HTTP requests; no HTTP-to-slot mapping, host-transfer proof, or performance qualification |
| Recovery and restoration | closed state windows restored; protected attempts retain their original and additive recovery closeouts | historical final state, not future-health proof |

## Retained disruptions and repairs

The initial protected attempt and native state Window 1 are retained failures,
not deleted rehearsals. The protected bracket also retains its original false
closeout plus the additive manual recovery closeout and `CLOSED` marker. State
Window 1's direct-rank CUDA-context failure and both subsequent repair steps
remain source-bound artifacts. Artifact-set controls were **not created before
the first live action**. This late creation is a process gap that must remain
in `friction-log.md`; it is not retroactively represented as pre-run control
evidence.

## Decision and remaining gates

The current recommendation remains **no-promotion / preserve incumbent**. The
historical sampled-output numerical failure remains open. The existing signed
package lacks a semantic full-logit readback contract, and this campaign has no
teacher-forced independent reference, relative-L2, or KL result. State-copy
success and top-20 agreement/difference cannot replace those missing gates.

The paired trace is retained in `formal/trace-paired-1/`; its normalized
summary is `analysis/trace-paired-1/trace-paired-1-summary.{json,md}`. All four
capture groups contain their expected measured HTTP population (72/72), but the
trace graphs mix shapes between arms. Scheduler selection, commit, GPU interval,
and wall observations are samples, not a per-request causal mapping; rank
intervals are not summed and wall-minus-GPU is not a CPU measurement. The
independent final causal review accepts only bounded diagnostic interpretation.

### Completed fixes and unresolved cause

The evidence retains three completed bounded changes: configurable allocation in
the experiment source, test-only CUDA-context handling for the native state
fixture, and lifecycle reconciliation/recovery records. They establish neither a
production numerical fix nor a measured production benefit. The host-index
early-out is a source candidate only: it has no production patch and no measured
benefit in this campaign.

The next measurement must bind each HTTP request to its scheduler slot and
generation, then record paired admission and host-index spans. It must run
separately from timing. A separately bound raw-logit semantic package remains
necessary for numerical interpretation; top-20 observations cannot replace it.

Before final reporting or bundle assembly, retain and independently review the
trace alongside complete ABBA attempts and phase summaries, source/image/registry/lifecycle
identity, every interrupted/failed/repaired window, restoration and direct/routed
health evidence, state/top-20 raw results, and final offline analysis. No result
from an incomplete or survivor-filtered population may support a performance
claim.
