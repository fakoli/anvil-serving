# Model dossiers

Each dossier is a stable synthesis over immutable dated findings. Dossiers may
change as new evidence arrives; linked findings remain point-in-time records.
Every dossier uses the same contract: status, identity, topology, recipe,
evidence class, decision boundary, failures, and dated history.

## RTX PRO 6000

| Dossier | Current decision | Contents |
|---|---|---|
| [Qwen3.5 122B](qwen35-122b.md) | `current` | Current NVFP4 and historical MXFP4 |
| [Laguna S 2.1 and Laguna XS](laguna-s-2.1.md) | `rollback` / `rejected` | Qualified S and failed XS path |
| [GPT-OSS Puzzle 88B](gpt-oss-puzzle-88b.md) | `rollback` | Secondary pinned rollback |
| [Agents-A1](agents-a1.md) | `challenger`, `no-promotion` | Thinking-disabled qualification |
| [Gemma 4 PRO variants](gemma-4.md) | `no-promotion` / `rejected` | Official and Unsloth 12B, 26B, 31B |
| [Qwen3.6 27B and ThinkingCap](qwen36-27b.md) | `no-promotion` | Community NVFP4+MTP, official FP8, Unsloth, ThinkingCap |
| [Nemotron 3 Super 120B](nemotron3-super-120b.md) | `no-promotion` | Historical quality/capacity challenger |
| [Nemotron Puzzle 75B](nemotron-puzzle-75b.md) | `no-promotion` | MTP throughput challenger |
| [GPT-OSS 120B](gpt-oss-120b.md) | `no-promotion` | Historical throughput control |
| [Mistral Small 4](mistral-small-4.md) | `no-promotion` | Low-TTFT control |
| [MiniMax M2.7 REAP](minimax-m27-reap.md) | `no-promotion` | Historical community challenger |
| [Ornith 1.0 35B](ornith-35b.md) | `no-promotion` | Historical specialist |
| [DeepSeek V4 Flash](deepseek-v4-flash.md) | `rejected` | Incomplete compatibility attempt |

## RTX 5090

| Dossier | Current decision | Capability |
|---|---|---|
| [Nemotron Nano/Omni 30B](nemotron-omni-30b.md) | `current` topology | Auxiliary text, vision, OCR |
| [Qwen2.5-Omni 3B](qwen25-omni-3b.md) | `challenger`, `no-promotion` | Co-resident Omni |
| [Parakeet TDT 0.6B v3](parakeet.md) | `current` | STT |
| [Qwen3-ASR 0.6B](qwen3-asr.md) | `challenger`, `no-promotion` | STT |
| [Nemotron 3.5 ASR](nemotron35-asr.md) | `rejected` | STT |
| [Kokoro](kokoro.md) | `current` | TTS |
| [Gemma 4 E4B Fast](gemma4-e4b.md) | `no-promotion` | Historical Fast control |

Statuses use the portal's [evidence and decision labels](../index.md#evidence-status).
Failed loads and compatibility-only runs remain visible.
