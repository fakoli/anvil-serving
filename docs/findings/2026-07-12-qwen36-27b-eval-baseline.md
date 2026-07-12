# Qwen3.6-27B NVFP4+MTP evaluation baseline

**Point-in-time record, 2026-07-12.** This run restored the previously tested
`sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` candidate on Fakoli Dark's RTX PRO
6000, ran the existing built-in bakeoff as a baseline, and then ran the newer
session-derived `--suite-file` evaluation unchanged. The built-in bakeoff
passed. The session-derived suite reported 0/5, but that score remains invalid
for ranking or promotion under the known-broken cross-model protocol.

## Configuration

| Field | Tested value |
|---|---|
| Served model | `qwen36-27b-nvfp4-mtp` |
| Checkpoint | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` |
| Host | Fakoli Dark, Windows 11, Docker Desktop/WSL2 |
| GPU | RTX PRO 6000 Blackwell Max-Q, 96 GB, sm_120 |
| Engine | vLLM `0.23.1rc1.dev531+ga65f93fb2` |
| Quantization | ModelOpt NVFP4, text-only, BF16 MTP head |
| KV cache | FP8 |
| Context / sequences | 262,144 / 2 |
| Speculative decoding | Qwen MTP, 3 speculative tokens |
| Endpoint | `http://127.0.0.1:39027/v1` |
| Managed serve | `cand-qwen36-heavy-mtp` |

The managed swap stopped the unused `heavy` / `vllm-gptoss120` serve before
starting this candidate. The checkpoint was 18.29 GiB; target plus draft model
loading took 158.13 seconds and the healthy post-start GPU reading was 88,112
MiB. vLLM selected the FlashInfer CUTLASS NVFP4 linear kernel, the Triton/FLA
GDN prefill kernel, and a 1,600-token hybrid cache block. The engine warned that
the checkpoint does not provide calibrated FP8 KV q/prob scaling factors and
used 1.0; retain that as an accuracy caveat for later context work.

## Correctness gate

Preflight ran with thinking disabled, a 128,000-token needle, and 20 shared-prefix
tool calls. All checks passed: short coding in 5.3 seconds, structured JSON,
the 128K needle in 25.6 seconds, and 20/20 valid tool calls.

## Existing built-in baseline

Raw artifact:
[current-built-in-eval.json](2026-07-12-qwen36-27b-eval-baseline-evidence/current-built-in-eval.json).

| Check | Result |
|---|---|
| Context | pass; 128,540 prompt tokens, 35.01 s TTFT, 35.70 s end-to-end |
| Tool call | pass |
| Multi-turn session recall | pass |
| Unified-diff intelligence | pass |
| Parallel timeout triage | pass |

The built-in intelligence result was 2/2 and the complete built-in bakeoff had
no failures. This is the requested current-eval baseline. It is a single run,
not repeated quality evidence, and the context measurement had prefix caching
disabled by the serve recipe.

## New session-derived deterministic suite

Suite source:
[planning-milestone-execution.suite.json](2026-07-12-qwen35-122b-mxfp4-evidence/planning-milestone-execution.suite.json).
Raw result:
[new-session-derived-eval.json](2026-07-12-qwen36-27b-eval-baseline-evidence/new-session-derived-eval.json).

| Eval | Result | Missed literal checks |
|---|---|---|
| Low-overhead dashboard architecture | fail | stdlib, bounded retention, external evidence, degraded capability |
| Milestone dependency order | fail | exact ordered chain |
| Proof-buffer recovery | fail | reject, capture-evidence, resubmit/strict |
| Resumable CI monitor | fail | persist/resume and existing pull-request reuse |
| Local/remote main reconciliation | fail | explicit preservation of the untracked file |

Overall: **0/5 passed**. Unlike the GPT-OSS budget-starvation control, every
case produced substantive visible answer content and Qwen thinking was disabled
through its supported chat-template control. This isolates a different protocol
problem: one-shot exact-substring contracts reject directionally reasonable
answers that do not reproduce the requested operational vocabulary.

The result still cannot be used as a cross-model quality score. The suite's
`context_bucket` remains metadata rather than supplied context, the artifact
retains only excerpts rather than full output and finish-reason/reasoning-channel
evidence, and the run was not repeated. No routing profile, serve recommendation,
or promotion changed.

## Baseline conclusion

The checkpoint remains operationally healthy at its native 262K serve window
and passes the current built-in correctness/intelligence path at 131K. The new
suite's 0/5 is useful as a baseline for repairing the evaluation protocol, not
as evidence that the model has zero planning quality.
