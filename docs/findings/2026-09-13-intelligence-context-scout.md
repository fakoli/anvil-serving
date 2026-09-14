# Intelligence and context qualification — selected current text lane

**Observed:** 2026-09-13–14 UTC. **Decision state:** GLM Flash EXL3 r7
no-speculation is selected and admitted as the current text-only primary. This
record is bounded to the retained campaign evidence. Final deployment and bounded client acceptance are retained. Fresh boot-start and reboot tests were not run.

The user's order was intelligence and stability first, then usable context and
speed. Coding and tools were central. Image and video may stay on another lane.

## Decision evidence

| Candidate/configuration | Retained local evidence | Decision boundary |
|---|---|---|
| **GLM Flash EXL3 r7 no-spec** | 90/100 fixed MMLU-Pro scout; all 100 nonempty exact `FINAL=<letter>`/`stop`; 9/9 promoted 258K context; 30/30 agentic; strict120 120/120; five-case official SWE 4/5 | Strongest fully tested fit in this bounded campaign: text-only 4-bpw EXL3 at 327,680 configured context. The quality scout alone is not a statistical intelligence ranking. |
| GLM Flash EXL3 r7 MTP3 | 91/100 quality scout; 7/9 258K context; strict120 120/120 | Separate speculative configuration. It has a one-point higher one-pass quality count, but weaker retained context completion. |
| Qwen Flash-Next EXL3 4.05 | 91/100 quality scout; 9/9 >257K total context; 21/30 agentic; 4/5 SWE; 12/12 image/OCR | Different runtime and control lane. Its evidence remains useful, but it is not the selected primary. |
| Qwen3.8-27B FP8 | 26/30 partial quality run | Retained rollback candidate; not rankable against the fixed 100-item scouts. |

The MMLU-Pro sample was fixed before model outputs, used one repetition and a
65,536-token cap, and required exact `FINAL=<letter>`. GLM no-spec's 90/100
versus the separate 91/100 MTP3 and Next scouts does not establish superiority
or inferiority. The official raw count remains unchanged despite the separately
recorded invalid key for item 8310.

## Production and context acceptance

Managed promotion retained a passing preflight, cold reload, and readmission.
The active recipe status reports the pinned GLM quant/revision, running state,
no native KV offload, and zero container restarts or OOM kills at capture. Owner
acceptance saw the same enabled active container twice. These observations do
not substitute for a fresh systemd-start or reboot test, neither of which ran.

Post-promotion production context passed 9/9 cases through the real front door:
actual inputs were 255,647–255,672 tokens plus a 65,536-token output reserve.
All nine stopped normally. The route applies its fixed max-reasoning/sampling
policy: a deliberately invalid reasoning effort failed directly with HTTP 400
but was accepted through the primary route with HTTP 200. That verifies the
route control boundary; it is not a general prompt-quality test.

The managed client-host catalog convergence changed seven owned client settings with
backups and required restarts, then a repeat preview reported no changes. Pi
passed native read/write. Hermes retained an initial wrong-path fixture failure
before passing an absolute-path retry. OpenClaw retained a local `max` precheck
failure before passing with supported client `high` while the router enforced
`max`.

## Coding, agentic, and capacity limits

The no-spec native agentic suite completed 30/30. Its frozen official SWE scout
resolved 4/5, graded all five with zero errors, and submitted all five patches.
The sole miss was `sympy__sympy-11618`; the five trajectories used 12, 44, 27,
10, and 17 model calls for Django, Requests, Pytest, scikit-learn, and Sympy.
The previous wrapper's environment mismatch was a harness failure before model
requests; the frozen retry is the retained result.

One matched C4 strict120 pair reported median decode of 59.662 tok/s with MTP3
and 38.908 tok/s without speculation. It is one matched pair, not a broad speed
claim. Reported cache allocation does not prove simultaneous full-window
correctness.

The Qwen rollback path is declared but was not exercised after promotion. Its
image verification is advisory-only because the rollback command lacks a compose
file. Restore it only through the retained operator procedure; this result does
not claim that fallback has been freshly started.

## Candidate landscape

The selected model is
[brandonmusic GLM-5.3-Flash EXL3 4-bpw](https://huggingface.co/brandonmusic/GLM-5.3-Flash-tr3-4bpw/tree/a5fee929cf4888b1824323e33e8a19b60129e025).
The tested [Qwen Flash-Next EXL3 4.05](https://huggingface.co/turboderp/Qwen3.8-Flash-Next-exl3/tree/55a732e0c4c3d4614bc42b68493bb930d9b02c0a)
remains the closest measured alternative. Ornith HQ 3.5-bpw EXL3, Next 6.05,
YaRN variants, and Qwen27 Swift/Qwopus/Signal/Whittle fine-tunes are retained
external priors only: none has a local qualifying run in this record.

## Evidence boundary

The qualification source was `f5d8fbc4`; the controller-mirror closure was
`8600b8c`.

The [evidence bundle](2026-09-13-intelligence-context-scout-evidence/README.md)
contains sanitized native and deployment receipts with source hashes and a
field-redaction audit. It preserves the declared Qwen rollback boundary and
known missing fresh boot-start/reboot proof. No claim here changes a
route, client catalog, or recovery procedure.
