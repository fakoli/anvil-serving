# Qwen3.8 Flash Next engine scout

**Date:** 2026-10-26
**Scope:** Local functional, coding, and bounded context checks on three distinct single-GPU recipe variants. This is not a controlled serving comparison.
**Decision:** `no-promotion`; campaign closed 2026-10-26.

> **Final campaign record:** no promotion. Exact prior evaluation routes/output caps and the GLM incumbent were restored and passed the recorded local checks. The router runs a reviewed PR #704 overlay, not the original router image. Do not read these rows as an engine or model ranking.

<!-- benchmark-result-card/v1 -->
## Result card

Three recipe variants passed their six-case agentic suite and their nine-sample bounded context scout through a maximum actual prompt of 114,588 tokens. Coding results were mixed. A newly confirmed shared-router whitespace defect creates an attribution risk for Aider output; the original request and response bodies for historical Aider runs were not retained, so its effect on those runs is unknown. vLLM and Flare serving C1 each ended at 0/8 performance-eligible. Exact route/output-cap restoration and GLM incumbent readmission passed local direct/routed checks; the router uses a reviewed PR #704 overlay, not the original image.

| Setup | Retained identity or limit |
|---|---|
| Models | Ignis: `gpillon/Qwen3.8-Flash-Next-trellis-a25-ignis@8d94db83cd8e58d6be31e796723e5e35b960638c`; vLLM: `local-inference-lab/Qwen3.8-Flash-Next-NVFP4-MXFP8-CSF-QAD@dc5b8ab2fa14bc4fecd6886273b7ebc6095662e2`; Flare: `local-inference-lab/Qwen3.8-Flash-Next-FLARE@431b302155d53e296aa51633a997b4b5fd92e751` |
| Hardware | One NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition per serialized run. No dual-GPU performance result. |
| Runtime | Ignis 0.8.0; vLLM build observed as `v0.1.dev21836+g6d66eed35` (full source revision unknown); Flare source revision `2193455651794642debdaad0dda91e77ab675031`, image `ghcr.io/local-inference-lab/flare:release-2193455-k7-pf4096-20261022@sha256:67124437a73011d63c2b25c939b9c48581eba90e51c6e565d4c808d161d10fc6` |
| Image identities | Ignis `sha256:0226b62047113c219124ca4a02b1e52492402b44f5a3020db91e77756914b8cc`; vLLM `sha256:bc48d33ee167fb7675a59889fe0a7fea539bc6700c53cada89284d594a754b54`; Flare as above. |
| Context contract | Ignis configured 131,072 tokens; vLLM and Flare configured/advertised 262,144. Each tested requested buckets 8,192, 32,768, and 114,688 with three samples per bucket. Maximum observed prompt: 114,588 tokens. 8,192-token output headroom was configured. |
| Measurement path | Managed local candidate endpoints with single-request concurrency. No controlled decode, latency, throughput, or concurrent-capacity population completed. |
| Evidence | Functional and bounded context results; mixed and incomplete coding rows; both vLLM and Flare C1 terminal at 0/8 performance-eligible; exact prior route/cap and GLM restoration passed the retained local checks. |
| Decision | `no-promotion`; no primary promotion. Temporary evaluation routes and output caps were changed during the scouts; exact prior settings and GLM incumbent were restored and passed local checks. |

| Headline | Retained result | Conditions and limits |
|---|---:|---|
| Agentic | 6/6 passed on each recipe | One request per cell; task correctness only. |
| Context | 9/9 passed on each recipe | Three requested buckets; largest actual prompt 114,588; advertised 262,144 is not a full-window measurement. |
| SWE resolved | Ignis 2/5; vLLM 3/5; Flare 0/5 | Official grader outcomes. Flare had one graded unresolved task and four empty/ungraded patches after reaching the declared, frozen 60-step ceiling. Historical Aider attribution remains unresolved because original HTTP bodies are unavailable. |
| Aider | Ignis 3 passed, 2 graded failures, 1 ungraded; vLLM and Flare each 4 passed, 1 graded failure, 1 ungraded | Each lane had one ungraded budget stop. These are recipe outcomes only; they do not isolate model or engine effects. |
| Serving C1 | vLLM 0/8; Flare 0/8 performance-eligible | vLLM: seven reasoning-only rows and one visible-output miss. Flare: five no-visible-content length stops and three strict 128-word misses. C4/C8 were skipped for vLLM. No timing population. |

**Why it matters:** the checks show bounded functional and context behavior for three distinct recipes while preserving coding failures and budget stops. They do not isolate engine effects or establish an upgrade.

**Important caveat:** the shared router's buffered-text splitter was confirmed to collapse repeated spaces, including indentation. Original historical Aider HTTP bodies are unavailable, so this is a plausible Aider integrity risk, not a proven cause of any scored result. The separate router fix in [PR #704](https://github.com/fakoli/anvil-serving/pull/704) passed exact-merge CI; one repaired-router controlled capture is included as a separate diagnostic, not a campaign rerun.

Restoration: [final restoration summary](2026-10-26-flash-next-engine-qualification-evidence/final-restoration-evidence.json) and [sanitized restoration record](2026-10-26-flash-next-engine-qualification-evidence/restoration.json). Related token-boundary evidence: [GLM marker GPU trace](2026-10-26-marker-gpu-trace.md), where a worker-only token followed the API stop in the retained trace; the finding did not show client-visible leakage and does not claim a runtime fix.

C1 details: [Flare offline reconciliation](2026-10-26-flash-next-engine-qualification-evidence/flare-c1-offline-reconciliation.md), [Flare native capacity result](2026-10-26-flash-next-engine-qualification-evidence/flare-serving-c1-native-capacity-result.json), and [vLLM native capacity result](2026-10-26-flash-next-engine-qualification-evidence/vllm-serving-c1-native-capacity-result.json).

Evidence bundle: [final evidence index](2026-10-26-flash-next-engine-qualification-evidence/README.md). The finalized artifact manifest, source registry, restoration record, and decision summary close this no-promotion campaign.

## Outcome and decision

This scout retains independent outcomes for three model, image, and engine combinations. The artifact identities and recipes differ, so the rows are not a matched comparison of serving engines. All three agentic reports show six completed, passing cells. All three native context results show nine passing samples at the same three requested buckets. Neither gate tests the full advertised 262,144-token window.

Aider produced three graded passes, two graded failures, and one ungraded budget stop on Ignis; four graded passes, one graded failure, and one ungraded budget stop on vLLM; and four graded passes, one graded failure, and one ungraded budget stop on Flare. The ungraded cells are not scored as failures or passes. Historical Aider attribution remains uncertain because original HTTP bodies were not retained.

The official SWE grader resolved 2/5 Ignis tasks, 3/5 vLLM tasks, and 0/5 Flare tasks. Ignis had one graded unresolved task and two empty patches; vLLM had three resolved tasks and two empty patches. Flare attempted five tasks: Django was the only nonempty graded submission and remained unresolved; Xarray, Pylint, Sympy, and Sphinx exited with `LimitsExceeded` at the declared, frozen 60-step ceiling and had empty patches. This is step-budget exhaustion, not evidence of RAM exhaustion. The SWE path used native API arguments rather than the router's affected buffered-text delta path; this path distinction does not itself invalidate the official SWE grades.

The measured decision remains `no-promotion`; no primary promotion occurred. Temporary evaluation routes and output caps were changed during the scouts; the exact prior settings and GLM incumbent were restored. Direct and routed five-check preflights and one Pi real read-tool turn passed. Flare C1 had three pre-request setup/admission refusals before corrected r3. The terminal r3 attempt received eight HTTP responses, but all eight failed native acceptance and none was performance-eligible; its outer wrapper retained `native_control_mismatch`. Exact route/output restoration and GLM readmission passed the retained local checks.

## Exact configuration

All three runs used one RTX PRO 6000 Blackwell Max-Q GPU at a time, single-request concurrency, and a deterministic context workload. The model repositories, weight revisions, runtime identities, image IDs, configured contexts, and per-run recipe hashes are recorded in the native context result artifacts linked below. These are different recipe variants, not an engine-only comparison.

| Recipe | Runtime and configured concurrency | Tested concurrency | MTP | Weight/KV configuration |
|---|---|---:|---:|---|
| Ignis | Ignis 0.8.0; configured C8 | C1 | 3 draft tokens | Trellis mixed 2–4 bpw experts; KV dtype not recorded; context 131,072 |
| vLLM | `v0.1.dev21836+g6d66eed35`; configured C16 | C1 | 3 draft tokens | NVFP4/MXFP8 CSF QAD; FP8 KV; context 262,144 |
| Flare | source `2193455651794642debdaad0dda91e77ab675031`; configured C16 | C1 | 5 draft tokens | NVFP4 routed experts / MXFP8 attention and shared experts; KV dtype not recorded, `--kv-cache-limit memory`; native effective context 114,688; configured/advertised maximum 262,144 |

The vLLM and Flare benchmark source revision is `a5ccb7454ad49f47e5a953b130696fff69cdb829`. Ignis source content SHA-256 is `daf9d724d6513c488667740e0e4db2ecc5e4ec6fad58b6fb2cac1694e101f83a`; its Git revision is not recorded. Configured concurrency is recipe metadata; the serving cells here measured only C1.

Flare's request overrides were temperature 0.6 and top-p 0.95; its native runtime defaults record temperature 1.0, top-k 20, and top-p approximately 0.95. Sampling-control equivalence to the other lanes is not asserted. Flare artifact metadata also records that full stored-weight payload verification was not checked.

## Method

The agentic suite contained six one-request cells per lane. The context suite contained nine samples per lane: three each at requested buckets 8,192, 32,768, and 114,688 tokens. The largest observed prompt was 114,588 tokens. Configured and advertised context limits are reported separately from observed input size.

Aider retained six attempted cells across two reports per lane. The fixed-five SWE denominator is five attempted tasks per lane; empty patches remain ungraded, and the official SWE-bench grader scored nonempty submissions. No campaign artifact here is a controlled serving-speed matrix. Timing and throughput are out of scope; vLLM and Flare C1 each had 0/8 performance-eligible responses.

## Results

| Recipe lane | Agentic | Aider | Official SWE | Context | Other gate |
|---|---|---|---|---|---|
| Ignis 0.8.0 | 6/6 passed | 3 passed, 2 failed, 1 ungraded | 2/5 resolved; 1 graded unresolved; 2 empty/ungraded | 9/9 passed; configured 131,072; max actual prompt 114,588 | A separate Go repeat diagnostic is not pooled into the six-task Aider result. At an effective 16K cap, its first attempt failed and its bounded repair passed; a true 32K follow-up used the declared 32,768-token cap, but both responses ended with `finish_reason=length` and the task remained unresolved. The earlier incorrectly clamped run remains a separate invalid-cap historical attempt. |
| vLLM r2 | 6/6 passed; preflight 18/18 | 4 passed, 1 failed, 1 ungraded | 3/5 resolved; 2 empty/ungraded | 9/9 passed; configured/advertised 262,144; max actual prompt 114,588 | C1 had 0/8 eligible responses; C4/C8 were skipped. |
| Flare r2 | 6/6 passed; preflight 18/18 | 4 passed, 1 failed, 1 ungraded | 0/5 resolved; 1 graded unresolved; 4 empty/ungraded | 9/9 passed; advertised/configured 262,144; native effective context 114,688; max actual prompt 114,588 | C1 terminal: 8 HTTP responses, 0/8 eligible; five no-visible-content length stops and three strict 128-word misses. The wrapper failure `native_control_mismatch` is preserved. Final restoration passed the retained local checks; the router image uses the reviewed PR #704 overlay rather than the original image. |

Native evidence: [Ignis agentic](2026-10-26-flash-next-engine-qualification-evidence/ignis-agentic-campaign-report.json), [Aider A](2026-10-26-flash-next-engine-qualification-evidence/ignis-aider-campaign-report-a.json), [Aider B](2026-10-26-flash-next-engine-qualification-evidence/ignis-aider-campaign-report-b.json), [context](2026-10-26-flash-next-engine-qualification-evidence/ignis-context-benchmark-result.json), [SWE result](2026-10-26-flash-next-engine-qualification-evidence/ignis-swe-benchmark-result.json), [official grader](2026-10-26-flash-next-engine-qualification-evidence/ignis-swe-official-grader-summary.json).

[vLLM agentic](2026-10-26-flash-next-engine-qualification-evidence/vllm-agentic-campaign-report.json) · [Aider A](2026-10-26-flash-next-engine-qualification-evidence/vllm-aider-campaign-report-a.json) · [Aider B](2026-10-26-flash-next-engine-qualification-evidence/vllm-aider-campaign-report-b.json) · [context](2026-10-26-flash-next-engine-qualification-evidence/vllm-context-benchmark-result.json) · [SWE result](2026-10-26-flash-next-engine-qualification-evidence/vllm-swe-benchmark-result.json) · [official grader](2026-10-26-flash-next-engine-qualification-evidence/vllm-swe-official-grader-summary.json).

[Flare agentic](2026-10-26-flash-next-engine-qualification-evidence/flare-agentic-campaign-report.json) · [Aider A](2026-10-26-flash-next-engine-qualification-evidence/flare-aider-campaign-report-a.json) · [Aider B](2026-10-26-flash-next-engine-qualification-evidence/flare-aider-campaign-report-b.json) · [context](2026-10-26-flash-next-engine-qualification-evidence/flare-context-benchmark-result.json) · [SWE result](2026-10-26-flash-next-engine-qualification-evidence/flare-swe-benchmark-result.json) · [official grader](2026-10-26-flash-next-engine-qualification-evidence/flare-swe-official-grader-summary.json).

## Failures and caveats

- **Shared router text handling:** an offline four-boundary capture confirmed that the frozen router source's buffered-text path collapses repeated spaces, including indentation. The original HTTP request and response bodies for the historical Aider runs were not retained. The defect may have affected Aider output, but historical causality is unproven; do not attribute malformed Aider code or score differences to a model or engine. The SWE path used native API arguments rather than the affected buffered-text delta path, so this router defect does not itself invalidate the official SWE grades.
- **Separate fixed-router capture:** PR #704 merged and its exact-merge CI, docs, and secret checks passed. One controlled capture with the fix preserved the returned code byte-for-byte; the repaired supplemental verifier passed, and the offline exercise suite passed 16/16. This is not a campaign rerun and does not rescore any historical Aider row.
- **SWE budgets:** keep completed official grades separate from `LimitsExceeded`/empty-patch tasks. The empty patches are ungraded, not resolved and not proof of correctness.
- **vLLM serving scout:** 0/8 responses were performance-eligible. Its counter-reconciliation supplement changes accounting only; it does not rehabilitate the failed output gate.
- **Flare C1:** after three pre-request setup/admission refusals, corrected r3 received eight HTTP responses; all eight failed native acceptance, so eligibility was 0/8. The outer wrapper retained `native_control_mismatch`. Offline reconciliation found matching request/response counters and prompt-token accounting, plus zero post-run in-flight gauges and no sampled OOM events. No performance result follows. Exact route/output-cap restoration and GLM incumbent readmission passed the retained local checks. The router uses a reviewed PR #704 overlay rather than the original image; remote-memory terminal remains unknown and drained=false.
- **Context boundary:** 114,588 tokens is the maximum observed prompt here; 262,144 configured/advertised is untested at full length. No full-window capacity or concurrent-full-context claim follows.
- **Public redaction:** native schemas are retained, but public context artifacts replace raw reasoning, visible answers, and private GPU UUIDs with redaction markers. Private source hashes and redaction provenance are held separately.

## Follow-up boundaries

The campaign is closed with no promotion. Any future coding replay requires a separately frozen plan and must not overwrite these historical outcomes. Historical Aider attribution remains uncertain because original HTTP bodies are unavailable. The reviewed PR #704 overlay is part of the restored router runtime; the original router image was not restored. The exact-predecessor rollback capability is tracked separately in PR #705; its status does not change the benchmark outcomes. The canonical router image Python 3.11/native-WAL failure is tracked in `.tickets/2026-10-26-router-python311-native-wal-image-compatibility.md`.

## Evidence boundary

This final record supports bounded functional outcomes, three nine-sample context scouts, and bounded recipe-outcome comparison. It does not support engine causality, a speed ranking, throughput, full-context or concurrent-capacity claims, or model promotion. Flare C1 is terminal with no eligible requests. Exact local route restoration, direct/routed five-check preflights and one Pi read-tool turn passed; no whole-fleet, reboot or fresh-host acceptance is claimed.
