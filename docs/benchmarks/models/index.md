# Model dossiers

Each dossier synthesizes immutable dated findings; it does not report live
routes, placement, or availability. Start with the [selection guide](../selection.md)
for a workload decision, or use this complete inventory to find the supporting
evidence by measured hardware.

## RTX PRO 6000

| Model / configuration | Measured result | Limitation / decision | Evidence date | Dossier |
| --- | --- | --- | --- | --- |
| GLM-5.3-Flash EXL3 r7 no-spec | 327,680 configured tokens; C4 strict120 120/120; separate long-context 9/9; MMLU-Pro 90/100, agentic 30/30, SWE 4/5 | `current` text-only lane; no concurrent full-window soak or fresh-boot proof | 2026-09-14 | [GLM-5.3-Flash](glm53-flash.md) |
| Qwen3.8 Flash Next EXL3 4.05-bpw | MMLU-Pro 91/100, context 9/9, agentic 21/30, SWE 4/5, image/OCR 12/12 | strict120 had zero performance-eligible responses; `no-promotion` | 2026-09-14 | [Qwen3.8 Flash Next](qwen38-flash-next.md) |
| Qwen3.8 Flash Next NVFP4 TP=2 | 253,703 actual prompt tokens plus 8,192 output; text, image, OCR, and video acceptance | historical rollback evidence | 2026-08-26 | [Qwen3.8 Flash Next](qwen38-flash-next.md) |
| DeepSeek V4 Flash 0731 | Retained TP=2 quality, capacity, and client evidence | former Primary; historical | 2026-08-21 | [DeepSeek V4 Flash](deepseek-v4-flash.md) |
| Qwen3.8 27B official FP8 | Declared text-only rollback after the September selection | unexercised after promotion; no fresh restoration proof | 2026-09-14 | [Qwen3.8 27B](qwen38-27b.md) |
| Qwen3.8 27B NVFP4 TP1 / TP2 / DP2 | DP2 1,401.8–1,423.4 aggregate tok/s; TP1 764.3; TP2 587.9 | TP2 strict JSON failures; all `no-promotion` | 2026-09-04 | [Qwen3.8 27B](qwen38-27b.md) |
| Agents-A1 | Historical FP8 Primary-era evidence | historical promotion | 2026-07-29 | [Agents-A1](agents-a1.md) |
| Qwen3.5 122B NVFP4 / MXFP4 | Single-card qualification and TP=2 topology evidence | retained rollback / `no-promotion` | 2026-08-01 | [Qwen3.5 122B](qwen35-122b.md) |
| Laguna S 2.1 and XS | Qualified S and failed XS paths | rollback / rejected | 2026-08-01 | [Laguna S 2.1](laguna-s-2.1.md) |
| GPT-OSS Puzzle 88B | 40/40 c8 capacity, 20/20 tools, and long-context retrieval | dated rollback; unified-diff only 2/3 | 2026-07-18 | [GPT-OSS Puzzle 88B](gpt-oss-puzzle-88b.md) |
| Gemma 4 official / Unsloth variants | Official 12B passed strict quality and 240K retrieval; 31B 62.3 tok/s | 26B timeout and 31B latency outcomes; `no-promotion` / rejected | 2026-07-17 | [Gemma 4](gemma-4.md) |
| Qwen3.6 27B and ThinkingCap | Four-checkpoint comparison and quality control | `no-promotion` | 2026-07-13 | [Qwen3.6 27B](qwen36-27b.md) |
| Nemotron 3 Super 120B | Historical quality/capacity challenger | `no-promotion` | 2026-08-01 | [Nemotron 3 Super](nemotron3-super-120b.md) |
| Nemotron Puzzle 75B MTP | 131K retrieval, 20/20 tools, and 137.0 tok/s MTP decode | `no-promotion`; later planning recheck 0/5 | 2026-07-12 | [Nemotron Puzzle 75B](nemotron-puzzle-75b.md) |
| GPT-OSS 120B | 128K retrieval, 20/20 tools, and 183.2 tok/s historical decode | historical control; `no-promotion` | 2026-07-12 | [GPT-OSS 120B](gpt-oss-120b.md) |
| Mistral Small 4 | 131K context; 57.82 aggregate tok/s at c1 and 67.04 at c5 | low-TTFT control; `no-promotion` | 2026-07-12 | [Mistral Small 4](mistral-small-4.md) |
| MiniMax M2.7 REAP | 64K retrieval, 2/2 intelligence, and 97.2 tok/s | no 131K headroom; `no-promotion` | 2026-07-11 | [MiniMax M2.7 REAP](minimax-m27-reap.md) |
| Ornith 1.0 35B | 131K needle, 20/20 tools, and 29.2 tok/s | incomplete identity; `no-promotion` | 2026-07-10 | [Ornith 1.0 35B](ornith-35b.md) |
| Inkling Small NVFP4 | Low-reasoning qualification lane | `no-promotion`; Responses caveat | 2026-08-01 | [Inkling Small](inkling-small.md) |

## RTX 5090

| Model / configuration | Measured result | Limitation / decision | Evidence date | Dossier |
| --- | --- | --- | --- | --- |
| FLUX.2 Klein 4B | 6/8 bounded visual reviews pass | workflow `available=true`, not promoted | 2026-08-28 | [FLUX.2 Klein](flux2-klein.md) |
| Wan2.2 TI2V 5B | Functional/decode and Hermes acceptance | unavailable, `no-promotion` | 2026-09-15 | [Wan2.2](wan22.md) |
| Qwen3.8 27B GGUF / NVFP4 | 50.9 ms warm TTFT target-only and 137.7 tok/s 64K speculative arm | challengers and incumbent retained; `no-promotion` | 2026-09-03 | [Qwen3.8 27B](qwen38-27b.md) |
| Gemma 4 E2B W4A16 | 30K/60K/120K preflight; 96 aggregate tok/s c1 and 204 c2 at 32K | timeout triage 0/3; `no-promotion` | 2026-07-16 | [Gemma 4 E2B](gemma4-e2b.md) |
| Gemma 4 31B W4A16 Fast lane | 30K/45K/60K protocol preflight; 9/8 aggregate tok/s at 30K c1/c2 | 128K start lacked KV headroom; rejected; dossier also covers a separate PRO lane | 2026-07-17 | [Gemma 4 variants](gemma-4.md) |
| Signal 27B Q6_K/MTP3 | Functional; 9/10 thinking-on quality scout | 262K contract not qualified; strict output failures; `no-promotion` | 2026-09-12 | [Qwen3.8 efficient variants](qwen38-efficient-variants.md) |
| Swift 27B Q6_K/MTP3 | Functional; 9/10 thinking-on quality scout | 262K contract not qualified; strict output failures; `no-promotion` | 2026-09-12 | [Qwen3.8 efficient variants](qwen38-efficient-variants.md) |
| Qwopus Flash 27B Q6_K | Functional; 1/10 thinking-on quality scout | format and budget failures; `no-promotion` | 2026-09-12 | [Qwen3.8 efficient variants](qwen38-efficient-variants.md) |
| Minitron 20B Q6_K | Functional; 3/10 thinking-on quality scout | output and bounded-quality failures; `no-promotion` | 2026-09-12 | [Qwen3.8 efficient variants](qwen38-efficient-variants.md) |
| Qwen3.5 35B-A3B GGUF Q4_K_M | 59,053 actual prompt tokens at the configured 64K window; 20/20 tools; 56.3 tok/s short probe | historical fast candidate, no promotion and no immutable revision retained | 2026-07-11 | [Qwen3.5 35B](qwen35-35b.md) |
| Nemotron Nano/Omni 30B | 20/20 tools, session recall, and 64K context on nightly lane | historical topology record | 2026-07-27 | [Nemotron Nano/Omni](nemotron-omni-30b.md) |
| Qwen2.5-Omni 3B | 128K retrieval and 20/20 tools | challenger, `no-promotion` | 2026-07-27 | [Qwen2.5-Omni](qwen25-omni-3b.md) |
| Parakeet TDT 0.6B v3 | Primary-human micro-WER 3.343% | `current` in dated evidence | 2026-07-28 | [Parakeet](parakeet.md) |
| Qwen3-ASR 0.6B | Primary-human micro-WER 3.621% | challenger, `no-promotion` | 2026-07-28 | [Qwen3-ASR](qwen3-asr.md) |
| Nemotron 3.5 ASR | Primary-human micro-WER 6.685% | rejected; 3.343-point WER regression | 2026-07-28 | [Nemotron 3.5 ASR](nemotron35-asr.md) |
| Kokoro | 289.27 ms TTS and RTF 0.1006 | `current` in dated evidence | 2026-07-28 | [Kokoro](kokoro.md) |
| Gemma 4 E4B Fast | 30K retrieval, 20/20 tools, 61 ms warm TTFT, ~97 tok/s | historical Fast control; `no-promotion` | 2026-07-16 | [Gemma 4 E4B](gemma4-e4b.md) |

## Apple Silicon

| Model / configuration | Measured result | Limitation / decision | Evidence date | Dossier |
| --- | --- | --- | --- | --- |
| Qwen3.5-9B MLX 4-bit | Functional 6/6; diagnostic quality 9/12 | spoken 33/36, patch 0/3, output 0/10; `no-promotion` | 2026-09-08 | [Voice LLM MLX](voice-llm-mlx.md) |
| Qwen3.8-27B MLX 4-bit | Compatibility preflight 5/6 | tools 1/3; incomplete, `no-promotion` | 2026-09-08 | [Voice LLM MLX](voice-llm-mlx.md) |
| Qwen3.6-35B-A3B MLX 4-bit | Spoken 36/36 and one SDK session | strict JSON failure; incomplete, `no-promotion` | 2026-09-08 | [Voice LLM MLX](voice-llm-mlx.md) |
| Qwen3 4B MLX 4-bit baseline | Spoken suite 36/36 strict | shared-prefix tools 0/3; unchanged baseline, not a promotion | 2026-09-08 | [Voice LLM MLX](voice-llm-mlx.md) |

The result and decision labels are historical evidence labels. Failed loads and
compatibility-only runs remain visible; none of these rows authorizes serving
or routing changes.
