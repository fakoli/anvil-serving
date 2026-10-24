# Proposed PR: recognize the deployed legacy GLM chat template

Private draft for user review; nothing has been pushed or submitted.

## Problem and change

An older signed GLM package contains the unannotated checkpoint chat template. Current main rejects it with `chat template requires message-boundary mark annotations`, even though its bytes exactly match the current GLM recipe template after removing the six boundary marks. The existing compatibility path handles the corresponding deployed Qwen, Step and Kimi templates but omits GLM.

Add GLM to that same exact-byte upgrade list in `helios/src/frontend/templates.rs`. Loading the recognized legacy asset then compiles the current annotated GLM template in memory. Unknown or modified unannotated templates continue to fail closed. Update the existing documentation and add focused regression coverage; package files, signatures, captured graphs, cache allocation and sampling policy are unchanged.

The retained 10,950-byte legacy asset has SHA-256 `0c4099f3382d6c92700dfb99725025360966fd73032f0ecf32377c0d9e6309c5`. The current 11,022-byte asset becomes byte-identical after its six literal boundary marks are removed. Matching a model-family label alone is insufficient; this patch requires the existing exact comparison.

## Regression and validation

The regression exercises the real frontend loading/rendering path, rendered text and token IDs, Unicode, reordered tool results, strictly ordered boundaries and stable prefix cuts after a suffix change. It also rejects internal edits and CRLF conversion. The existing MiniJinja comparison supplies an independent rendering reference.

- Four focused CPU tests passed with `runtime-package,server`, matching upstream CI's feature selection: two exact-template checks, the existing MiniJinja rendering comparison, and the legacy GLM prompt/token/boundary regression.
- The release build and `git diff --check` passed; staged design review passed before the local commit.
- With the retained signed package on two RTX PRO 6000 Blackwell Max-Q GPUs, TP2/C4, six fresh startups passed package/weight verification. One coding smoke and all 391 bounded diagnostic requests completed without request failures.
- All 192 measured one-token responses matched their priming response token IDs, prompt IDs and request-body hashes. This is bounded compatibility evidence, not a general numerical or long-output quality guarantee.

The first CPU test invocation omitted `runtime-package` and failed in unrelated offload-test compilation; using the upstream feature set required no source change. The complete Rust/GPU test suites were not claimed or run for this focused patch. Separate initial trial failures involved a host driver/library mismatch after a system update, an operator monitoring mapping, and our collector's assumption that response model IDs equal requested aliases. Those were corrected outside this Flare patch and are retained in the experiment report.

## Scope and related work

Based on upstream `d5fd9824593c2a70f6bdc001b39f16b4c5b3b0b4`; local commit `0b21d7f4883ac6f2b099df55813e8be06777b9df`, three files. [Reviewable diff](legacy-glm-template-compat.diff).

This complements the current one-pass annotated renderer rather than reviving closed PR #27. It does not apply PR #16's draft-page correction or enable PR #35's package-bound score buffers; both require re-export independently. It makes no cache speedup or engine-parity claim. The reviewed all-state inventory and focused GLM/template search found no equivalent compatibility proposal as of 2026-10-24 UTC.

Repository AGENTS.md allows a focused PR without opening an issue first, with design review and accurate validation disclosure. The recommendation is to submit this small compatibility change for maintainer review after the user's final review. The allocation proposal remains a separate experiment.
