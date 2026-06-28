# Model mixture for fakoli-dark — deep-research findings (2026-06-28)

**Question (R003):** best open-weight LLMs to serve on the fakoli-dark box (one RTX PRO 6000
96GB + one RTX 5090 32GB, Blackwell **sm_120**, independent SGLang replicas) for a multi-agent
**coding/research/writing** workload, in two size classes — HEAVY (fits 96GB w/ ~128K KV) and
FAST (~8-14B, ~8-10GB, 200+ tok/s on 32GB). Exclude GGUF; prefer AWQ/compressed-tensors/
GPTQ-Marlin; flag FP8-MoE/NVFP4 sm_120 hazards.

**Method:** deep-research harness — 6 angles, 28 sources fetched, 131 claims extracted, 25
adversarially verified (3-vote), 22 confirmed / 3 killed, 9 findings after synthesis.

---

## Bottom line

- **HEAVY (96GB) general driver:** upgrade the baseline to **QuantTrio/Qwen3.6-35B-A3B-AWQ**
  (4-bit AWQ safetensors, 35B total / 3B active, 256 experts, 262K native ctx, ~24GB weights,
  Apache-2.0, recommended for SGLang ≥0.5.10). Same footprint as the current
  `qwen35-awq-local` baseline → drop-in upgrade. **Caveat:** documentation-level "recommended"
  only — *no third-party confirmation it runs end-to-end on sm_120.* Preflight is mandatory.
- **HEAVY coding specialist (2nd replica):** **community-AWQ Qwen3-Coder-30B-A3B** (official
  Qwen3-Coder ships only FP8 + GGUF — no official AWQ; the FP8 80B Qwen3-Coder-Next *hung* on
  sm_120, repo gotcha #3). Measured ~8,400 tok/s aggregate (400 concurrency) on the 96GB card.
- **FAST (32GB) general driver:** **Qwen3-14B-AWQ** (official `Qwen/Qwen3-14B-AWQ`) — dense
  4-bit AWQ/Marlin, ~8GB, Apache-2.0, 32K native / 131K YaRN. Being **dense, it dodges every
  documented sm_120 MoE/FP8/NVFP4 hang** — the safest FAST pick. Leaves large KV headroom.
  *Caveat:* single-stream tok/s on sm_120 not directly measured (200+ inferred from size/arch).
- **FAST (32GB) coding runner-up:** **Qwen3-Coder-30B-A3B in a W4A16 AWQ build**
  (`QuantTrio/...-AWQ` or `cpatonn/...-GPTQ-4bit`) — MoE ~16GB, coding-specialized,
  **measured ~898 tok/s in SGLang on a 5090**, ~114K ctx. *Caveats:* card warns "significant
  loss under 4-bit"; the SGLang CompressedTensorsWNA16 MoE path needs vLLM in the image (#9838) —
  vLLM is the cleaner load path. (Note: the local `Qwen3-Coder-30B-A3B-Instruct-GGUF` is GGUF,
  not this — the AWQ build is a separate ~16GB download.)
- **FAST max-speed mini:** **Qwen3-8B-AWQ** (official) — ~4.5GB, nearly the whole 32GB free for
  KV, if the 14B is too slow for the role.

> FAST research method: 2nd deep-research pass (task w5lw0l8rd) — same harness, RTX-5090-specific.
> Requested candidates GLM-4-9B, Ministral-8B, Codestral/Devstral, Gemma-3-12B, Phi-4, Seed-Coder,
> OlympicCoder surfaced **no** verified non-GGUF SGLang-loadable build on sm_120 → cannot be ranked.

---

## CONSOLIDATED VERIFIED FIELD (2026-06-28, dynamic-workflow pass — supersedes the provisional tables below)

Single paced dynamic workflow (23 agents, no rate-limiting): 50 candidate rows → 42 unique → 16
live-verified → 13 kept. Each candidate's HF repo + sm_120 serveability was re-checked live.

### HEAVY (96GB) — ranked
| # | Model | Origin | Repo | Status | Note |
|---|---|---|---|---|---|
| 1 | **gpt-oss-120b** (MXFP4 native) | 🇺🇸 OpenAI | `openai/gpt-oss-120b` | **verified** | **Heavy winner + best American model.** 117B-A5.1B MoE, ~63GB, 131K, Apache-2.0, SWE ~62%. Beats the Qwen3.5 baseline; fast (5.1B active) w/ huge KV. **Serve on TRITON MXFP4** (FlashInfer MXFP4 fails, sglang#13061). |
| 2 | Qwen3-Coder-30B-A3B (W4A16 AWQ) | 🇨🇳 Qwen | `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` | provisional | Coding replica; ~16GB, ~8,400 tok/s aggregate. CompressedTensors WNA16 MoE needs vLLM in image (#9838). |
| 3 | Seed-OSS-36B-Instruct (AWQ) | 🇨🇳 ByteDance | `QuantTrio/Seed-OSS-36B-Instruct-AWQ` | verified | Dense ~20GB, SWE 56 / LCB 67.4 / MMLU-Pro 82.7 / RULER@128K 94.6. → flashinfer. |
| 4 | Llama-3.3-70B-Instruct (AWQ) | 🇺🇸 Meta | `casperhansen/llama-3.3-70b-instruct-awq` | verified | Dense ~40GB, generalist, weak on agentic coding (late-2024). → flashinfer. |
| 5 | Nemotron-Super-49B-v1.5 (W4A16) | 🇺🇸 NVIDIA | `cyankiwi/Llama-3_3-Nemotron-Super-49B-v1_5-AWQ-4bit` | provisional | Dense reasoner ~29GB; needs `--trust-remote-code`; sm_120 untested. → flashinfer. |
| — | Qwen3.5-35B-A3B-AWQ (baseline) | 🇨🇳 Qwen | `cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit` | verified | The live `qwen35-awq-local` bar to beat. |

### FAST (32GB) — ranked
| # | Model | Origin | Repo | Status | Note |
|---|---|---|---|---|---|
| 1 | **GLM-4.7-Flash** (AWQ) | 🇨🇳 Zhipu | `cyankiwi/GLM-4.7-Flash-AWQ-4bit` | **verified** | Best fast coder. 30B-A3B MoE, MIT, ~20GB, SWE 59.2/LCB 64.0; 3B active → best shot at 150+ tok/s. **TRITON** backend; AWQ not BF16 (BF16→garbage, sglang#18874). |
| 2 | Qwen3-Coder-30B-A3B (W4A16 AWQ) | 🇨🇳 Qwen | `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` | provisional | ~898-1,157 tok/s on a 5090; highest coding ceiling; vLLM cleaner load path (#9838). |
| 3 | Qwen3-14B-AWQ (dense, safest) | 🇨🇳 Qwen | `Qwen/Qwen3-14B-AWQ` | verified | Dense ~8GB → dodges every sm_120 MoE/FP8/NVFP4 hang. → flashinfer. |
| 4 | Devstral-Small-2-24B (AWQ) | 🇫🇷 Mistral | `cyankiwi/Devstral-Small-2-24B-Instruct-2512-AWQ-4bit` | verified | **Highest SWE in class (68%)**, dense → slower (~62-93 tok/s). → flashinfer, `--language-only`. |
| 5 | Gemma-3-27B-it (GPTQ-4bit) | 🇺🇸 Google | `ISTA-DASLab/gemma-3-27b-it-GPTQ-4b-128g` | provisional | Best US fast option; generalist (coding-weak); confirm gemma3 sliding-window. → flashinfer. |
| 6 | gpt-oss-20b (MXFP4 native) | 🇺🇸 OpenAI | `openai/gpt-oss-20b` | provisional | ~16GB, LCB ~70; **TRITON MXFP4**; only batch tok/s measured, single-stream unproven. |

### Verdict — US vs Chinese open-weight for LOCAL coding on this box
**America owns the HEAVY slot, China owns the FAST slot.** gpt-oss-120b is the one genuinely
class-leading American model and is the heavy winner outright (clears the Qwen3.5 baseline). Below
it, US dense options (Llama-3.3-70B, Nemotron-49B, Gemma-3) are competent generalists/reasoners but
trail the best Chinese coders (Qwen3-Coder-30B, Seed-OSS-36B, GLM-4.7-Flash) and France's Devstral
(68% SWE) on agentic coding. There is **no American FAST-tier coding leader.** sm_120 fragility
rewards the simplest quant paths: dense AWQ/GPTQ/compressed-tensors → flashinfer; gpt-oss MXFP4 and
GLM MoE → triton; **avoid FP8-MoE and NVFP4 entirely** (silent zeros / hangs).

### Recommended mixture (final, pending R006 preflight)
- **HEAVY:** `openai/gpt-oss-120b` (US, general/reasoning driver) **+** `cpatonn/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit` (coding replica).
- **FAST:** `cyankiwi/GLM-4.7-Flash-AWQ-4bit` (coding) **or** `Qwen/Qwen3-14B-AWQ` (safe dense fallback).

### Quant + fine-tune audit (2026-06-28 dynamic-workflow pass — exact download repos)
| Model | Download repo | Quant verdict | Fine-tune |
|---|---|---|---|
| gpt-oss-120b | `openai/gpt-oss-120b` | **keep** — native MXFP4 is the QAT quality ceiling; any requant only loses | skip (only abliterated/GGUF variants exist) |
| gpt-oss-20b | `openai/gpt-oss-20b` | **keep** — native MXFP4 ceiling | skip |
| Qwen3-Coder-30B-A3B | `cpatonn/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit` | **SWITCH** from QuantTrio → calibrated AWQ W4A16, group_size=32, MoE gates + lm_head kept full-precision; field-proven ~898 tok/s on sm_120 (QuantTrio self-warns "significant loss under 4-bit") | skip |
| GLM-4.7-Flash | `cyankiwi/GLM-4.7-Flash-AWQ-4bit` | **keep** — calibrated W4A16 g32, card documents sm_120 triton serving | skip |
| Qwen3-14B | `Qwen/Qwen3-14B-AWQ` | **keep** — official calibrated AWQ (not re-audited) | skip |
| Seed-OSS-36B | `QuantTrio/Seed-OSS-36B-Instruct-AWQ` | keep (conservative — **not re-audited**; re-check for a calibrated cpatonn-style build) | skip |
| Devstral-Small-2-24B | `cyankiwi/Devstral-Small-2-24B-Instruct-2512-AWQ-4bit` | keep (conservative — **not re-audited**) | skip |

**New serving caveats from the audit:**
- **gpt-oss MXFP4 needs `triton>=3.4` + the `triton_kernels` package** installed, or SGLang **silently dequantizes to bf16** (blows up VRAM). Never FlashInfer MXFP4 (fails sm_120, sglang#13061). Some users needed `--enforce-eager` for stability.
- **Qwen3-14B is hybrid-thinking** → disable thinking for coding (`enable_thinking:false`) or it returns empty content under small `max_tokens`.
- **No fine-tune adopted anywhere** — every candidate found was abliteration/uncensoring (no coding gain, sometimes worse) or GGUF/MLX-only (not SGLang-servable). Consistent with the PRD's no-fine-tuning non-goal.
- **Coverage gap:** Seed-OSS-36B and Devstral-24B were not re-audited (run truncated to 4 models) — worth a follow-up to check for a finer-grained calibrated build, as was found for Qwen3-Coder.

## Recommended SGLang launch configs (the deploy starting points)

**Common sm_120 rule (CORRECTED 2026-06-28):** the right attention backend is **model/quant-specific**
— do NOT blindly pin flashinfer. Always avoid the auto-selected `trtllm_mha` (SM100-only → `ValueError`).
Then: **AWQ/GPTQ dense → flashinfer**; **GLM MoE (GLM-4.7-Flash) → `--attention-backend triton`** (per
its card); **gpt-oss MXFP4 → triton MXFP4 backend** (FlashInfer MXFP4 MoE kernel fails on sm_120,
sglang#13061). Avoid FP8-MoE and NVFP4 entirely (NVFP4 GEMM is broken on sm_120 — CUTLASS silently
returns zeros, flashinfer#2577; vllm#24921). R006 preflight confirms the backend per model.

```bash
# HEAVY winner (96GB) — gpt-oss-120b, native MXFP4 -> TRITON backend (US, general/reasoning driver)
FLASHINFER_CUDA_ARCH_LIST=12.0f python3 -m sglang.launch_server \
  --model-path openai/gpt-oss-120b \
  --attention-backend triton \
  --context-length 131072 --mem-fraction-static 0.85 \
  --reasoning-parser gpt-oss --tool-call-parser gpt-oss \
  --weight-loader-disable-mmap --served-model-name gpt-oss-120b-heavy --host 0.0.0.0 --port 30000
  # MXFP4 auto-detected. TRITON (FlashInfer MXFP4 MoE fails on sm_120, sglang#13061).
  # If CUDA-graph-replay crash on sm_120: add --disable-cuda-graph. Set reasoning effort via harmony prompt.

# FAST winner (32GB / RTX 5090) — GLM-4.7-Flash AWQ, GLM MoE -> TRITON backend (coding)
python3 -m sglang.launch_server \
  --model-path cyankiwi/GLM-4.7-Flash-AWQ-4bit \
  --attention-backend triton \
  --context-length 65536 --mem-fraction-static 0.85 \
  --reasoning-parser glm45 --tool-call-parser glm45 \
  --served-model-name glm47-flash-fast --host 0.0.0.0 --port 30001
  # AWQ build, NOT BF16 (BF16 -> garbage on sm_120, sglang#18874). Use BF16 KV if output corrupts.

# FAST safe alternative (32GB) — Qwen3-14B-AWQ, DENSE -> flashinfer (robustness over coding ceiling)
python3 -m sglang.launch_server --model-path Qwen/Qwen3-14B-AWQ \
  --quantization awq_marlin --attention-backend flashinfer \
  --context-length 65536 --mem-fraction-static 0.85 \
  --reasoning-parser qwen3 --tool-call-parser qwen3_coder --served-model-name qwen3-14b-fast

# HEAVY coding replica (96GB) — Qwen3-Coder-30B-A3B, CALIBRATED cpatonn AWQ (MoE -> triton)
python3 -m sglang.launch_server --model-path cpatonn/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit \
  --attention-backend triton --context-length 131072 --mem-fraction-static 0.9 \
  --tool-call-parser qwen3_coder --kv-cache-dtype fp8_e5m2 --served-model-name qwen3-coder
  # MoE W4A16 -> triton (moe_wna16). Fallback cpatonn GPTQ-4bit/Marlin sibling for sglang#9838.
```
> **gpt-oss prerequisite:** the heavy/fast gpt-oss configs require `triton>=3.4` + the `triton_kernels`
> package in the image, or SGLang silently dequantizes MXFP4 → bf16 (VRAM blowup). Add `--enforce-eager`
> if you hit CUDA-graph instability on sm_120.
Thinking-default models: disable per-request with `extra_body={"chat_template_kwargs":
{"enable_thinking": false}}` OR rely on `--reasoning-parser` — **not both** (they conflict →
`content=null`). These are *starting points*; R006 preflight/benchmark sets the real knobs.

## Verified findings

### sm_120 serving hazards (HIGH confidence)
1. **FP8-MoE / hybrid-GDN fails out-of-the-box on consumer Blackwell under SGLang.** Only
   `triton` or `trtllm_mha` backends are allowed for Blackwell-GDN; the triton kernel needs
   ~112KiB shared mem vs the SM's ~99KiB limit, and `trtllm_mha` validation accepts only SM100
   (the 5090/PRO 6000 report **sm120**) → `ValueError`. **Workaround: pin
   `--attention-backend flashinfer`.** Directly confirms repo gotcha #3.
   Sources: sglang#16816, #14814, #9140, #15122.
2. **AWQ / GPTQ-Marlin / compressed-tensors are first-class SGLang formats** (auto-parsed from
   HF config) — the right quant class for sm_120. ("listed" ≠ "flawless"; FP8-MoE & NVFP4
   large-prefill remain rough.) Source: SGLang quantization.md.
3. **NVFP4 of the exact heavy candidate is unstable on consumer Blackwell:** NVIDIA's own
   `nvidia/Qwen3.6-35B-A3B-NVFP4` build shows 3 failure modes on a 5090 (illegal instruction,
   CUDA-graph-replay crash, silent engine hang) — another reason to prefer AWQ over NVFP4.

### Thinking / reasoning handling (HIGH)
4. Qwen3/3.5/3.6 (+ gpt-oss, DeepSeek-R1/V3) are **thinking-by-default** → empty `content`
   under small `max_tokens`. Disable per-request via `chat_template_kwargs {"enable_thinking":
   false}` (not OpenAI-native — tunnel via `extra_body`), **or** split it with
   `--reasoning-parser qwen3` (returns `reasoning_content` vs `content`). **The two can
   conflict** (`enable_thinking=False` + qwen3 parser → `content=null`, sglang#20786). SGLang
   ships parsers for 7 families. Confirms repo gotcha #5.

### Model facts (HIGH)
5. **Qwen3.6-35B-A3B-AWQ** (heavy successor): 35B/3B MoE, 256 experts (8 routed + 1 shared),
   262K ctx, ~24GiB, Apache-2.0; hybrid Gated-DeltaNet+Gated-Attention+MoE → needs recent
   SGLang GDN kernels (exactly where the sm_120 bugs concentrate).
6. **Qwen3.5-35B-A3B-AWQ** (current baseline `qwen35-awq-local`): 35B/3B, 4-bit AWQ (data-free,
   a quality risk), 262K native (→~1M YaRN), parsers `qwen3` / `qwen3_coder`.
7. **Qwen3-Coder** ships only FP8 + GGUF officially (480B-A35B, Coder-Next 80B-A3B, 30B-A3B).
   SGLang-servable single-card coding ⇒ community AWQ 30B-A3B (FP8 80B hung on sm_120).

### Benchmark positioning (HIGH/MED)
8. Top standardized open-weight coder = **Qwen3-Coder-480B-A35B, 38.7% SEAL SWE-bench Pro**
   (Kimi-K2 27.7, Qwen3-235B 21.4, GLM-4.6 9.7) — but 480B/235B don't fit one card; useful as
   a ceiling, not a deployable pick. Frontier open coders (DeepSeek V4 Pro, Nemotron 3 Ultra,
   Kimi K2.6, GLM-4.7) are all too large for 96GB/32GB.
9. **Measured single-card throughput** (96GB): ~8,400 tok/s aggregate @400 concurrency on
   Qwen3-Coder-30B-A3B-AWQ (≈ 4×RTX 4090); ~100 tok/s single-stream on Qwen3.6-27B-INT4 with
   flashinfer + MTP n=3 + FP8 KV. (Single-vendor/community blogs; aggregate ≠ single-stream.)

## Killed claims (did not survive verification)
- "SGLang FP8 always falls back to Triton on sm120" — refuted 0-3.
- "Unsloth Qwen3-Coder-Next offers no AWQ/Marlin" — refuted 1-2.
- "Qwen3.6-35B-A3B-FP8 hits ~170 tok/s on one PRO 6000" — refuted 1-2.

## Caveats
Fast-moving mid-2026 snapshot; SGLang Blackwell bugs are being actively patched (so "fails
out-of-the-box" may already be partly fixed in newer SGLang). Heavy-tier facts rest on
quantizer-authored cards (QuantTrio) — **not** independently verified on sm_120. Throughput
figures are community-blog, disclosed-but-unreproduced methodology. The requested 1-5
coding/research/writing per-candidate scores did **not** survive verification (only
standardized SWE-bench positions did) — score them ourselves during preflight/benchmark.

## Open questions → feed into R005/R006 and a FAST-tier follow-up
1. **FAST tier (8-14B, sm_120, 200+ tok/s) is uncovered** — needs targeted research + a
   `models sync` scan of the local cache.
2. Has SGLang ≥0.5.10 actually fixed #16816 / #14814, and does `--attention-backend flashinfer`
   now serve FP8/hybrid-GDN MoE reliably on these cards?
3. Any non-quantizer confirmation that Qwen3.6-35B-A3B-AWQ loads + serves end-to-end on the
   PRO 6000 with 128K KV at usable throughput?
4. Community-AWQ Qwen3-Coder-30B-A3B verified on sm_120 under SGLang? Real coding quality vs
   the general 3.6-35B driver?
5. VRAM budget math: AWQ weights + activations + KV @128K on 96GB → what concurrency?

## Expanded multi-vendor field (2026-06-28 breadth follow-up)

Four breadth passes were run (non-Qwen HEAVY, non-Qwen FAST, practitioner/Reddit/HN/benchmark,
American/HF). **Process note:** running them concurrently tripped the Anthropic API rate limiter —
the non-Qwen-FAST pass completed with full 3-vote verification (**trustworthy**); the HEAVY and
practitioner passes returned good raw claims but their verifier step was rate-limited (**provisional,
unverified** — treat as leads, confirm at R006); the American/HF pass returned zero sources and was
**re-run solo** (pending). Empirical R006 preflight/benchmark is the real verification gate regardless.

### HEAVY tier (96GB) — non-Qwen options
| Model | Origin | Concrete repo | Size | Arch | Coding | Status |
|---|---|---|---|---|---|---|
| **gpt-oss-120b** | 🇺🇸 OpenAI | native MXFP4 | ~60.8GB | 117B-A5.1B MoE, 131K | SWE-bench 62.4% | provisional; **MXFP4→triton backend**, fits 96GB. Strongest US option. |
| **Seed-OSS-36B-Instruct** | 🇨🇳 ByteDance | `QuantTrio/Seed-OSS-36B-Instruct-AWQ` | ~20GB | dense 36B | SWE 56, LCB 67.4, MMLU-Pro 82.7, RULER@128K 94.6 | **verified**; documented SGLang launch + `seed_oss` parser. Strong all-rounder. |
| GLM-4.5-Air | 🇨🇳 Zhipu | `QuantTrio/GLM-4.5-Air-AWQ-FP16Mix` | ~69GB | 106B-A12B MoE | GLM-4.5 flagship SWE 64.2% (Air trails) | provisional |
| Llama-3.3-70B-Instruct | 🇺🇸 Meta | `lambdalabs/Llama-3.3-70B-Instruct-AWQ-4bit` | ~40GB | dense 70B | general-strong | provisional |
| Nemotron-Super-49B-v1.5 | 🇺🇸 NVIDIA | `nvidia/Llama-3_3-Nemotron-Super-49B-v1_5-FP8` | ~50GB | dense 50B (NAS) | reasoning-tuned | provisional; **sm_120 untested** (vendor lists only Hopper/Ampere) |
| ❌ GLM-4.6 (357B) | 🇨🇳 | `bullpoint/GLM-4.6-AWQ` | 176GB | MoE | — | **excluded** — needs 4 GPUs, doesn't fit 96GB |
| ❌ GLM-4.7-Flash BF16 @96GB | 🇨🇳 | `THUDM/GLM-4.7-Flash` | ~58GB | MoE | — | **flagged broken** — garbage output on sm_120 (sglang#18874) |

### FAST tier (32GB) — non-Qwen options (this pass was VERIFIED)
| Model | Origin | Concrete repo | Size | Arch | Coding | tok/s | Backend |
|---|---|---|---|---|---|---|---|
| **GLM-4.7-Flash** | 🇨🇳 Zhipu | `cyankiwi/GLM-4.7-Flash-AWQ-4bit` | ~20GB | 30B-A3B MoE, MIT, 128K | SWE 59.2, LCB 64.0 | best shot at 150+ (3B active) | **triton** |
| **Devstral-Small-2-24B** | 🇫🇷 Mistral | `cyankiwi/Devstral-Small-2-24B-Instruct-2512-AWQ-4bit` | ~13GB | dense 24B, Apache, 256K | **SWE 68.0% (highest)** | ~62-93 (dense, <150) | flashinfer; multimodal→`--language-only` |
| gpt-oss-20b | 🇺🇸 OpenAI | native MXFP4 | ~16GB | MoE, LCB 70 | LCB v6 70 | vLLM-batch only measured | **triton MXFP4** (flashinfer fails #13061) |
| Seed-Coder-8B | 🇨🇳 ByteDance | `ByteDance-Seed/...` bf16 | ~16GB | 8B dense (Llama-arch) | SWE claim refuted (0-3) | fast | standard |
| Gemma-3-12B | 🇺🇸 Google | `RedHatAI/...W4A16` | quantized | dense 12B | — | vLLM-batch only | — |

### Practitioner / benchmark signal (provisional — mostly GitHub-issue + leaderboard facts)
- **NVFP4 is broken on sm_120**, strongly corroborated: no CUTLASS MoE FP4 kernel for capability 120
  (vllm#24921); FlashInfer NVFP4 GEMM broken across all backends, CUTLASS silently returns zeros
  (flashinfer#2577). Hard-avoid NVFP4.
- **block-FP8 unsupported on sm_120** under SGLang → blocks GLM-4.5 FP8 checkpoints (sglang#9233).
- Leaderboards: best *open-weight* coders (GLM-5.2, MiniMax-M3, Kimi K2.6, DeepSeek V4 Pro) are all
  Chinese and **far too large to serve locally** — irrelevant to these tiers except as a ceiling.
- Measured: Qwen3-Coder-30B-A3B AWQ ~1,157 tok/s on a 5090 (vLLM, ~120K ctx); gpt-oss-120b 4-bit
  fits ~60-65GB on the PRO 6000.
- **GLM-5 on sm_120 needs BF16 KV** (FP8 KV → corrupted output) and SGLang is the only viable engine.

### Verdict so far on "more options" + American models
The field is genuinely wider now. Best deployable *coding* quality still skews non-American
(Devstral 68% dense, GLM-4.7-Flash, Seed-OSS, Qwen-Coder), **but gpt-oss is the standout American
option and is competitive** (120b SWE 62.4%; both sizes already on disk locally as GGUF). The one
real catch: gpt-oss MXFP4 serving on sm_120 needs the **triton MXFP4** path (FlashInfer kernel
currently fails) — a concrete preflight item. A dedicated American/HF pass (`wxuowjd08`) is re-running
to complete Meta/NVIDIA/Microsoft/Google/IBM/AI2 coverage.

## Cache-hygiene log (R007)

| Date | Removed | Size | Why | Reversible? |
|---|---|---|---|---|
| 2026-06-28 | `~/.cache/huggingface/hub/models--unsloth--Qwen3-Coder-Next-FP8` | ~75GB | Dead weight on this box: SGLang **hangs** on it (sm_120 FP8-MoE, gotcha #3 — corroborated by `examples/fakoli-dark/SETUP-STORY.md` "Abandoned it") **and** it's FP8 safetensors so llama.cpp/LM Studio can't load it either. Nothing on this hardware can serve it. | Re-downloadable from HF (unsloth/Qwen3-Coder-Next-FP8) |

**Deliberately KEPT** (SGLang-unusable but llama.cpp-usable — user's coders, their call): the GGUF
models — Ornith-1.0-35B (65GB), Qwen-AgentWorld-35B-A3B (65GB), Qwen3-Coder-30B-A3B (18GB), plus
the LM Studio GGUF set (~60GB: gpt-oss-120b/20b, gemma-3, Seed-OSS-36B, Mistral-7B, Qwen3-4B).

**FULL PRUNE (2026-06-28, user-confirmed "remove everything else"):** emptied the HF hub cache
(`~/.cache/huggingface/hub`) of ALL remaining models + datasets — Ornith-1.0-35B-GGUF (65GB),
Qwen-AgentWorld-35B-A3B-GGUF (65GB), Qwen3-Coder-30B-A3B-Instruct-GGUF (17GB), gemma-4-E2B-GGUF (4GB),
Qwen3.6-27B-MTP-GGUF (1GB), bge-small-en-v1.5 (129MB), and the two datasets Fable-5-traces +
claude-fable-5-claude-code (265MB). ~152GB reclaimed. Risk accepted by user (flagged: deletes the
llama.cpp coder fallback before SGLang replacements pass R006; deletes non-reproducible-maybe datasets).
LM Studio cache (`~/.lmstudio/models`, ~60GB GGUF) NOT touched (separate location, out of "HF cache" scope).
The new mixture downloads to `C:/Users/sdoum/models/`, not the cache.

## Local SGLang-servable inventory (post-cleanup)

Exactly **one** chat model is SGLang-servable on sm_120 locally: **`C:/Users/sdoum/models/qwen35-awq`**
(23GB, compressed-tensors 4-bit, multimodal → serve with `--language-only`) — the live
`qwen35-awq-local` baseline. The recommended heavy upgrade (Qwen3.6-35B-A3B-AWQ) and coding replica
(community Qwen3-Coder-30B-A3B-AWQ) are **not local** and must be downloaded.

> ⚠️ Tool gap (R004): `anvil-serving models sync` marked the now-deleted FP8 model `SGLang ✅`
> because its "SGLang?" column is **format-based, not sm_120-aware**. The catalog should flag
> FP8-MoE / NVFP4 on Blackwell as ⚠️, not ✅. Fold into the R004 task.

## Key sources
sglang#16816, #14814, #19603, #19644, #20786 · SGLang quantization.md & separate_reasoning ·
qwen.readthedocs SGLang deploy · HF QuantTrio/Qwen3.6-35B-A3B-AWQ & Qwen3.5-35B-A3B-AWQ ·
QwenLM/Qwen3-Coder · Scale SEAL SWE-bench Pro · nvidia/Qwen3.6-35B-A3B-NVFP4 discussions ·
CloudRift & lastloop-ai vllm-blackwell-guide (throughput).
</content>
