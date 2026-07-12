# Qwen3.6 protocol-v2 comparison on RTX PRO 6000

**Point-in-time record, 2026-07-12.** Four Qwen3.6-27B checkpoints were tested
one at a time on Fakoli Dark's single RTX PRO 6000 Blackwell 96 GB. This round
used the repaired deterministic protocol-v2 suite, audited a suspicious
completion-budget failure, added and tested Unsloth's new NVFP4 checkpoint,
and restored ThinkingCap as the resident Heavy quality challenger at
`http://127.0.0.1:39031/v1`. No router profile or production tier was promoted.

Raw artifacts are under
[2026-07-12-qwen36-protocol-v2-evidence/](2026-07-12-qwen36-protocol-v2-evidence/).
Their committed byte identities are listed in
[SHA256SUMS](2026-07-12-qwen36-protocol-v2-evidence/SHA256SUMS).

## Tested checkpoints and serving recipes

| Candidate | Pinned revision | Engine / quantization | Context / admission |
|---|---|---|---|
| `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | `6f194695406a3bc88a00573187d5b2eecf984a99` | vLLM `0.23.1rc1.dev531`, ModelOpt NVFP4, FP8 KV, MTP 3 | 262,144 / five; exact-card calibration used two |
| `Qwen/Qwen3.6-27B-FP8` | `e89b16ebf1988b3d6befa7de50abc2d76f26eb09` | vLLM `0.23.1rc1.dev531`, FP8, FP8 KV, MTP 3 | 262,144 / five |
| `bottlecapai/ThinkingCap-Qwen3.6-27B-FP8` | `e48255afd77b403446332be0f595868337b36591` | vLLM `0.23.1rc1.dev531`, FP8, FP8 KV, MTP 3 | 262,144 / five |
| `unsloth/Qwen3.6-27B-NVFP4` | `ccdaab7e68af2409599b8949a8f2685703c9bae5` | vLLM `0.25.0`, compressed-tensors NVFP4, FP8 KV, MTP 2 | 262,144 / five |

The three original candidates pin image digest
`sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`.
Unsloth pins amd64 vLLM 0.25.0 manifest digest
`sha256:e1c1ff1af9a15921bfa11d1d95047258c1797392cdbfa296e7639da446b23f97`.
Operator inspection of the latter image reported
`flashinfer-python==0.6.13` and `nvidia-cutlass-dsl==4.5.2`, matching Unsloth's
published minimum recipe. Operator-observed runtime logs selected
`FlashInferCutlassNvFp4LinearKernel`; those inspection/log transcripts were not
retained, so the pinned recipe is reproducible but these runtime facts are not
raw-artifact-backed in this change.

Operator-observed preflight passed smoke, structured JSON, the approximately
128K needle, and 20/20 shared-prefix tool calls with thinking disabled for every
candidate. Those preflight transcripts were not retained in this change, so
these deployment facts are not independently recoverable from the staged raw
directory. The prior variation round already recorded 5/5 concurrency
completion for the first three. Unsloth's retained raw artifact also records
5/5 at concurrency five in this round.

## Matched 1,024-headroom repeated baseline

Each item received 256 visible-answer tokens plus 1,024 reasoning-headroom
tokens as one 1,280-token API completion cap. Each item ran three times and
needed at least two passes. These are identical fixtures and validators across
all four checkpoints.

| Candidate | ARC stable / attempts | ARC wall | MMLU-Pro stable / attempts | MMLU wall |
|---|---:|---:|---:|---:|
| Community NVFP4+MTP | 3/5; 9/15 | 154.17 s | 0/10; 0/30 | 350.33 s |
| Official FP8 | 2/5; 6/15 | 195.05 s | 1/10; 3/30 | 419.18 s |
| ThinkingCap FP8 | **5/5; 15/15** | 104.36 s | **7/10; 21/30** | 300.24 s |
| Unsloth NVFP4 | 4/5; 12/15 | 152.63 s | 1/10; 3/30 | 418.81 s |
| Nemotron 3 Super reference | **5/5; 15/15** | **69.91 s** | **8/10; 23/30** | **260.91 s** |

Matched raw runs: community NVFP4 [ARC](2026-07-12-qwen36-protocol-v2-evidence/nvfp4-arc-thinking-headroom1024.json) / [MMLU-Pro](2026-07-12-qwen36-protocol-v2-evidence/nvfp4-mmlu-thinking-headroom1024.json),
official FP8 [ARC](2026-07-12-qwen36-protocol-v2-evidence/fp8-arc-thinking-headroom1024.json) / [MMLU-Pro](2026-07-12-qwen36-protocol-v2-evidence/fp8-mmlu-thinking-headroom1024.json),
ThinkingCap [ARC](2026-07-12-qwen36-protocol-v2-evidence/thinkingcap-arc-thinking-headroom1024.json) / [MMLU-Pro](2026-07-12-qwen36-protocol-v2-evidence/thinkingcap-mmlu-thinking-headroom1024.json),
and Unsloth [ARC](2026-07-12-qwen36-protocol-v2-evidence/unsloth-arc-thinking-headroom1024.json) / [MMLU-Pro](2026-07-12-qwen36-protocol-v2-evidence/unsloth-mmlu-thinking-headroom1024.json).

## Why the low Qwen scores are not intelligence scores

An independent recipe audit found that every community-NVFP4 MMLU attempt
consumed exactly the 1,280-token cap and ended with `finish_reason=length`.
Twenty-four of 30 attempts never left the reasoning channel; the other six
started a visible answer but were cut off before the final marker. ARC lost six
attempts to the same failure class. The reasoning parser, chat-template
control, checkpoint revision, and MTP path were all working. Runtime MTP
acceptance was approximately 67–83 percent, so speculative decoding was not
silently disabled.

The serving recipe did have two audit-worthy differences from the checkpoint's
RTX PRO 6000 card: five admitted sequences and 0.92 GPU utilization instead of
two and 0.90. The exact-card A/B started cleanly and passed preflight, but those
admission settings cannot explain a serial request exhausting its response
budget. FP8-KV startup also warned that missing attention scales fall back to
1.0; that remains a quality-confound follow-up, not proof of damage.

The valid conclusion is therefore **completion-budget starvation**, not model
incompetence. The 1,024-headroom rows remain useful as a constrained-efficiency
comparison only.

## Calibrated operating points

Calibration used one attempt per item to select a budget. Only ThinkingCap's
selected 4K point was then confirmed with all three repetitions.

| Candidate | Selected calibration | ARC | MMLU-Pro | Decision |
|---|---:|---:|---:|---|
| Community NVFP4+MTP | 8,192 | 5/5 one pass | 8/10 one pass; two truncations | Did not challenge ThinkingCap; no repeated confirmation |
| Official FP8 | 4,096 | 5/5 one pass | 8/10 one pass; two truncations | Did not challenge ThinkingCap; no repeated confirmation |
| ThinkingCap FP8 | **4,096** | 5/5 and 15/15 already at 1,024 | **9/10 stable; 27/30 attempts**, 458.78 s | Best confirmed quality-slice score |
| Unsloth NVFP4 | 8,192 | not rerun; 4/5 stable at 1,024 | 9/10 one pass; one truncation, 371.61 s | Same calibration score at twice ThinkingCap's budget; no repeated confirmation |

Selected calibrated artifacts: community NVFP4 [ARC 8K](2026-07-12-qwen36-protocol-v2-evidence/nvfp4-arc-thinking-headroom8192-calibration.json) / [MMLU-Pro 8K](2026-07-12-qwen36-protocol-v2-evidence/nvfp4-mmlu-thinking-headroom8192-calibration.json),
official FP8 [ARC 4K](2026-07-12-qwen36-protocol-v2-evidence/fp8-arc-thinking-headroom4096-calibration.json) / [MMLU-Pro 4K](2026-07-12-qwen36-protocol-v2-evidence/fp8-mmlu-thinking-headroom4096-calibration.json),
[ThinkingCap repeated MMLU-Pro 4K](2026-07-12-qwen36-protocol-v2-evidence/thinkingcap-mmlu-thinking-headroom4096.json),
and [Unsloth MMLU-Pro 8K](2026-07-12-qwen36-protocol-v2-evidence/unsloth-mmlu-thinking-headroom8192-calibration.json).

ThinkingCap's remaining computer-science item still exhausted 4K reasoning in
all three confirmation attempts. At 8K it completed but answered incorrectly
in the calibration. More budget is therefore not automatically more quality.

## Five-session capacity

All candidates completed five of five independent 8K-context requests with
thinking disabled. These short-output runs validate admission and scheduling,
not long-context five-session residency at five full 262K windows.

| Candidate | Completed | TTFT p50 | E2E p50 | Aggregate output throughput |
|---|---:|---:|---:|---:|
| Community NVFP4+MTP | 5/5 | 3.22 s | 3.75 s | 15.74 tok/s |
| Official FP8 | 5/5 | 5.68 s | 6.31 s | 8.31 tok/s |
| ThinkingCap FP8 | 5/5 | 4.66 s | 5.22 s | 7.92 tok/s |
| Unsloth NVFP4 | 5/5 | 3.68 s | 4.21 s | 15.21 tok/s |

Unsloth raw capacity runs: [concurrency one](2026-07-12-qwen36-protocol-v2-evidence/unsloth-concurrency1.json)
and [concurrency five](2026-07-12-qwen36-protocol-v2-evidence/unsloth-concurrency5.json).

Unsloth's first in-process cold pull was launched without `HF_TOKEN` loaded
and later stalled; the subsequent authenticated dedicated pull completed.
That observation does not prove the missing token caused the stall. The
canonical `models pull` command now
forwards `HF_TOKEN` by default, prefers an exported value, falls back to
`~/.env`, and supports explicit `--no-token`. The authenticated resumable pull
completed all 20 files; cached startup reached health in 239 seconds. ThinkingCap
also incurred a roughly five-minute cold weight-load and torch.compile startup
when restored. These are operational caveats, not steady-state request latency.

## Source age and decision use

| Source | Observed / source date | Age class | Evidence type | Decision impact |
|---|---|---|---|---|
| [Qwen3.6 official FP8 card](https://huggingface.co/Qwen/Qwen3.6-27B-FP8) | observed 2026-07-12; updated 2026-04-24 | current | official primary | native context, model controls, official baseline |
| [Community NVFP4+MTP card](https://huggingface.co/sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP) | observed 2026-07-12; updated 2026-04-29 | current | community checkpoint recipe | exact-card A/B and MTP configuration |
| [ThinkingCap card](https://huggingface.co/bottlecapai/ThinkingCap-Qwen3.6-27B-FP8) | observed 2026-07-12; updated 2026-07-10 | current | fine-tune publisher | selected fine-tune candidate |
| [Unsloth Reddit announcement](https://www.reddit.com/r/unsloth/comments/1usn545/new_25x_faster_qwen36_nvfp4_unsloth_quants/) | published 2026-07-10; observed 2026-07-12 | current | publisher announcement / community discussion | discovered checkpoint and performance claim; local tests required |
| [Unsloth NVFP4 card and recipe](https://huggingface.co/unsloth/Qwen3.6-27B-NVFP4) | observed 2026-07-12; revision updated 2026-07-12 | current | publisher primary | selected engine/dependency/MTP recipe |

The Reddit throughput claim is an external prior, not a local result. Its cited
B200/128-concurrency setup is not comparable to this serial RTX PRO 6000
quality suite. Local preflight, quality, and five-session artifacts are the
decision evidence. Machine-readable source lineage is in
[source-registry.json](2026-07-12-qwen36-protocol-v2-evidence/source-registry.json).

## Decision boundary

ThinkingCap is the selected resident **Heavy quality challenger** because it has
the highest confirmed stable score in the current protocol-v2 sample. Nemotron
3 Super remains the better matched-1K-budget and latency result. The evidence
does not establish a universal model ranking, a production promotion, five
simultaneous 262K sessions, or the advertised one-million-token extension.
