# Model dossiers

Each dossier is a stable synthesis over immutable dated findings. Dossiers may
change as new evidence arrives; linked findings remain point-in-time records.
Every dossier uses the same contract: status, identity, topology, recipe,
evidence class, decision boundary, failures, and dated history.

New and reformatted dossiers lead with a decision snapshot, then preserve the
full technical narrative in dated or outcome-oriented sections. Configuration
lanes, measurement classes, decisions, and failures stay visibly separate.
Not every campaign retains the same amount of evidence; an unavailable field
is reported as an evidence gap rather than inferred to make the pages look
artificially uniform. See the [dossier and finding format](../finding-format.md).

Status and decision wording below is the latest published conclusion as of
each dossier's review date and linked dated evidence. It is not a report of
live operator routes, placement, or availability; use the benchmark portal's
[published decision synthesis](../index.md#published-decisions-and-recent-controls)
for the maintained cross-model view.

## RTX PRO 6000

| Dossier | Latest published decision | Contents |
|---|---|---|
| [GLM-5.3-Flash](glm53-flash.md) | `current` text/tools/image/OCR reference at SGLang 393K/C1 | Pinned ormandj W4A16/NVFP4 adaptive-MTP on TP=2: 112.07/96.17/102.42/99.79 tok/s decode at nominal 4K/120K/262K/380K, 304,491 measured prompt tokens, coding 15/15, media 12/12, endurance 60/60, managed and routed gates, and real Pi/OpenClaw/Hermes acceptance; exact 524K EXL3/DFlash2 profile retained as rollback |
| [Qwen3.8 Flash Next](qwen38-flash-next.md) | immediate retained text/image/OCR/video rollback | RadixArk ModelOpt NVFP4, exclusive TP=2 at 262K/c1; hash-gated SM120 QSA-fast plus MTP3; direct vision 30/30, live routed repeats 57/60 strict, four-image/one-video admission, 155.9/114.7/112.9 median decode tok/s at 4K/128K/254K targets, full-reserve request, and fresh OpenClaw/Hermes/Pi acceptance |
| [DeepSeek V4 Flash 0731](deepseek-v4-flash.md) | former text Primary / retained evidence | Infernal Invocation r18/r15 B12X/DSpark K5 promotion history, matched no-spec A/B, long-context capacity, and client acceptance |
| [Qwen3.8 27B](qwen38-27b.md) | former single service / retained recipe | Official FP8 SGLang TP=1/393K/MTP `3/1/4` text/image/OCR/video evidence |
| [Agents-A1](agents-a1.md) | historical promotion / retained recipe | Thinking-disabled FP8 Primary-era evidence; BF16 control and compact NVFP4 text profile |
| [Qwen3.5 122B](qwen35-122b.md) | `rollback` in its dated campaign; retained qualified recipe | NVFP4 rollback-era evidence and historical MXFP4 |
| [Laguna S 2.1 and Laguna XS](laguna-s-2.1.md) | `rollback` in its dated campaign / `rejected`; retained historical recipe | Qualified S and failed XS path |
| [GPT-OSS Puzzle 88B](gpt-oss-puzzle-88b.md) | `rollback` in its dated campaign; retained historical recipe | Additional pinned rollback-era evidence |
| [Gemma 4 PRO variants](gemma-4.md) | `no-promotion` / `rejected` | Official and Unsloth 12B, 26B, 31B |
| [Qwen3.6 27B and ThinkingCap](qwen36-27b.md) | `no-promotion` | Community NVFP4+MTP, official FP8, Unsloth, ThinkingCap |
| [Nemotron 3 Super 120B](nemotron3-super-120b.md) | `no-promotion` | Historical quality/capacity challenger |
| [Nemotron Puzzle 75B](nemotron-puzzle-75b.md) | `no-promotion` | MTP throughput challenger |
| [GPT-OSS 120B](gpt-oss-120b.md) | `no-promotion` | Historical throughput control |
| [Mistral Small 4](mistral-small-4.md) | `no-promotion` | Low-TTFT control |
| [MiniMax M2.7 REAP](minimax-m27-reap.md) | `no-promotion` | Historical community challenger |
| [Ornith 1.0 35B](ornith-35b.md) | `no-promotion` | Historical specialist |
| [Inkling Small](inkling-small.md) | `no-promotion` | Qualified NVFP4 TP=2 lane; low-reasoning contract passes, reasoning-off Responses caveat retained |

## RTX 5090

| Dossier | Latest published decision | Capability |
|---|---|---|
| [FLUX.2 Klein 4B](flux2-klein.md) | published `available=true`, `promoted=false` workflow decision (2026-08-28) | Text-to-image generation through real Hermes/MCP; fixed 512/768/1024-pixel profiles; 6/8 strict bounded reviews pass with two retained draft failures; exact cold lifecycle pass |
| [Wan2.2 TI2V 5B](wan22.md) | unavailable candidate, `no-promotion` | Text-to-video generation; functional/decode plus real-Hermes acceptance; one bounded sample failed prompt adherence and spatial quality |
| [Qwen3.8 27B](qwen38-27b.md) | Gittensor target-only preferred TTFT `challenger`, Unsloth Dynamic V3 preferred clean 64K MTP arm, `no-promotion`; GGUF incumbent retained | 50.9 ms warm TTFT/full-context direct text plus 137.7 tok/s clean 64K speculation; separate image/OCR/video and routed evidence |
| [Nemotron Nano/Omni 30B](nemotron-omni-30b.md) | `current` in 2026-07-28 evidence; retained topology record | Auxiliary text, vision, OCR |
| [Qwen2.5-Omni 3B](qwen25-omni-3b.md) | `challenger`, `no-promotion` | Co-resident Omni |
| [Parakeet TDT 0.6B v3](parakeet.md) | `current` in 2026-07-28 evidence | STT |
| [Qwen3-ASR 0.6B](qwen3-asr.md) | `challenger`, `no-promotion` | STT |
| [Nemotron 3.5 ASR](nemotron35-asr.md) | `rejected` | STT |
| [Kokoro](kokoro.md) | `current` in 2026-07-28 evidence | TTS |
| [Gemma 4 E4B Fast](gemma4-e4b.md) | `no-promotion` | Historical Fast control |

Statuses use the portal's [evidence and decision labels](../index.md#how-to-read-the-evidence).
Failed loads and compatibility-only runs remain visible.
