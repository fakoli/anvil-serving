# Failed and incomplete runs — 2026-07-10 Blackwell local-model bakeoff

Negative results are evidence. Nothing here is a universal judgment about a
model family — every entry is scoped to the exact engine, quantization,
context, and hardware configuration tested.

## 1. Nemotron-3-Nano-30B-A3B NVFP4 @ 131k + CUDA graphs — engine-init hang

- **Model:** `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` (nemotron_h hybrid Mamba-Transformer MoE)
- **Engine:** vLLM v0.19.0+6bc3197f.nv26.04 (image `nvcr.io/nvidia/vllm:26.04-py3`)
- **Config:** `--quantization modelopt_fp4 --max-model-len 131072`, CUDA graphs on
  (FULL_AND_PIECEWISE), fp8 KV, RTX 5090 (sm_120), WSL2/Docker
- **Symptom:** engine initialization never completes. Last log line is FLASHINFER
  attention-backend selection; zero further output for 44+ minutes. The API server
  answers `/v1/models` (which fooled a naive readiness probe) but every inference
  request dies with connection aborts (WinError 10053/10054). No crash, no OOM,
  no Traceback — a silent wedge.
- **Notable pre-hang log lines:** `Updating mamba_ssm_cache_dtype to 'float32'`,
  `Setting attention block size to 4176 tokens to ensure attention page size >=
  mamba page size`, NVFP4 MoE backend `FLASHINFER_CUTLASS`.
- **Evidence:** `candidate-nemotron-text-vllm-nvfp4-131k-enginehang.failure.json`
  (all suites failed on connection errors).
- **Isolation performed:**
  - `--enforce-eager` + 64k context → engine comes up in ~120 s and passes
    smoke/tool/session/context suites (see
    `candidate-nemotron-text-vllm-nvfp4-eager-64k.bakeoff.json`) — so weights,
    kernels, and the model itself are fine; the wedge is in graph
    capture and/or the 131k Mamba/attention page-size interplay.
  - 64k + CUDA graphs on: run recorded separately (see finding narrative for
    the outcome) to split "131k" from "graph capture" as the trigger.
- **Believed cause class:** engine/kernel (sm_120 hybrid-Mamba path), not
  model weights. Consistent with repo gotcha #16's "hybrid attention
  re-introduces sm_120 issues" pattern.
- **Not attempted:** vLLM nightly image, SGLang, BF16 checkpoint (2× VRAM),
  the model card's custom `nano_v3` reasoning-parser plugin at 131k.
- **Production impact:** none (5090 track; production heavy untouched).

## 2. Nemotron-3-Nano-Omni-30B NVFP4 — unsupported architecture (load failure)

- **Model:** `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4`
  (arch `NemotronH_Nano_Omni_Reasoning_V3`, any-to-any multimodal)
- **Engine:** vLLM v0.19.0 (NGC `nvcr.io/nvidia/vllm:26.04-py3`)
- **Symptom:** container exits during config validation — pydantic
  ValidationError: architecture "not supported for now". Secondary
  `ModuleNotFoundError: mamba_ssm` in the same image. No GPU work ever starts.
- **Classification:** engine-support failure (the image supports text
  `NemotronHForCausalLM` and vision `NemotronH_Nano_VL_V2`, but not the Omni
  Reasoning V3 arch). Not model-, quant-, or hardware-specific.
- **Evidence:** `candidate-nemotron-omni-vllm-nvfp4-loadfailure.failure.json`
- **Not attempted:** newer vLLM images, transformers custom_code path.
- **Verdict:** reject under tested configuration; watch for runtime fixes.

## 3. Fast-baseline default-thinking artifact (measurement note, not a model failure)

- Benchmarking the production fast serve (`qwen36-35b-a3b-nvfp4`) with default
  thinking mode reproduces repo gotcha #6/#9: valid JSON, **empty content** on
  session/intelligence checks. The production router disables thinking for this
  tier, so the faithful baseline uses `--thinking-mode disabled`.
- Kept as labeled evidence:
  `baseline-qwen36-35b-a3b-vllm-nvfp4-32k-thinking-default.bakeoff.json`.

## 4. Gemma-4-31B-IT-NVFP4 @ 32k on RTX 5090 32 GB — KV-cache OOM ladder

- **Model:** `nvidia/Gemma-4-31B-IT-NVFP4` (~18 GB weights); **image:**
  `vllm/vllm-openai:gemma4-unified`; fp8 KV; RTX 5090 32 GB; legacy model
  runner forced by WSL2 (`VLLM_USE_V2_MODEL_RUNNER=0`).
- Every config below loaded weights and completed torch.compile, then died at
  cache-block allocation ("No available memory for the cache blocks"):
  | # | Config | Available KV |
  |---|--------|--------------|
  | 1 | 32k, 2 seqs, gmu 0.88 | negative (OOM) |
  | 2 | 32k, 2 seqs, gmu 0.94 | −0.66 GiB |
  | 3 | 16k, 1 seq, gmu 0.94 | negative (OOM) |
  | 4 | 32k, 2 seqs, gmu 0.94, `--limit-mm-per-prompt image/video=0` | −2.61 GiB |
  | 5 | 32k, 2 seqs, gmu 0.92, `--language-model-only --max-num-batched-tokens 2048` | −3.21 GiB |
  | 6 | 16k, 1 seq, gmu 0.92, `--language-model-only --max-num-batched-tokens 2048 --enforce-eager` | −1.05 GiB |
- Config 5 confirmed `language_model_only: True` took effect (no encoder-cache
  reservation in the log) — the residual consumer is compile/CUDA-graph
  workspace on this image vintage.
- **Structural context (community + docs):** with the legacy runner, Gemma 4's
  50-of-60 sliding-window layers are priced like global-attention layers, wasting
  20–35% of KV budget; the hybrid KV allocator that fixes this lives in the MRV2
  stack, which WSL2's missing UVA forces off (repo gotcha #14). Long-context
  Gemma 4 on this host is structurally expensive under vLLM until that changes.
- **No working configuration was found on the 32 GB card** — six configs
  (down to 16k, 1 seq, text-only, eager) never reached a non-negative KV
  budget. Verdict: reject under tested configuration (gemma4-unified image +
  NVFP4 + WSL2 legacy runner on 32 GB); believed engine-stack-specific, not
  model-specific.
- **Not attempted:** NGC 26.04 image (`Gemma4ForConditionalGeneration` is in
  its supported list), encoder-stripped community checkpoint
  (`LilaRest/gemma-4-31B-it-NVFP4-turbo`, reports ~25k ctx on a 5090),
  llama.cpp GGUF path (iSWA-native KV), the 96 GB PRO 6000, native-Linux
  MRV2 stack.

## 5. DeepSeek-V4-Flash-NVFP4 — aborted by operator (incomplete run)

- **Model:** `nvidia/DeepSeek-V4-Flash-NVFP4` (158B MoE, ~83 GB NVFP4 weights)
- **Attempt 1 — NGC `nvcr.io/nvidia/vllm:26.04-py3`:** rejected at config parse:
  the image's bundled transformers does not recognize model type `deepseek_v4`.
  Engine-version failure; no GPU work started.
- **Attempt 2 — `vllm/vllm-openai:nightly` (v0.23.1rc1):** config accepted,
  weights loading proceeded but pathologically slowly (~80 s per shard × 46
  shards ≈ 60+ min projected, vs ~2 min for the 70 GB MiniMax checkpoint from
  the same volume). Possible aggravators: concurrent Omni compile on the other
  GPU, and an operator page-cache drop mid-load. At shard 18/46 (~36 min wall-clock, including engine init and IO stalls) the
  operator aborted the run to end host memory/IO pressure that risked
  contaminating the concurrent Nemotron Omni measurements.
- The container refused normal stop (zombie loader PID under WSL2 — a known
  hazard of huge mmap'd loads); `docker rm -f` cleared it.
- **Verdict: not enough evidence.** The 96 GB fit question (83 GB weights +
  overhead at 16k ctx) remains open. Re-attempt in isolation, ideally warming
  the page cache first and with no concurrent serves.
- **Production impact:** none beyond the planned heavy-tier downtime window.

## 6. Interrupted pull stream during host storage resize (operational note)

- The initial PRO-6000-track download stream was interrupted by a Docker
  shutdown (host storage resize), leaving `.incomplete` blobs for
  `deepreinforce-ai/Ornith-1.0-35B-FP8` and no data for the MiniMax/DeepSeek
  repos. `hf download` resumed cleanly into the same named volume.
- Secondary effect: the pre-fix (unauthenticated-token) pull loop survived a
  partial kill and raced the authenticated retry for the same repo's blob
  locks (repo gotcha #12 shape: the stall is the lock, not a rate limit).
  Killing the stale container released the locks; throughput jumped from
  ~3.7 MB/s (anonymous, rate-limited) to ~90 MB/s (authenticated).

## 7. Extension-round operational notes (2026-07-11)

- **CLI surface changed under us mid-exercise:** Operator CLI v2 (merged to
  main between rounds) removed `preflight`/`benchmark` (now `eval preflight` /
  `eval benchmark run`) and gated `serves up/down` behind `--confirm`. First
  extension pipelines no-op'd their measurement steps with migration notices;
  reproduction docs updated. The fail-closed `--confirm` gate correctly
  protected production from an unconfirmed stop.
- **llama.cpp `-hf` downloads land in the HF hub cache layout**
  (`/root/.cache/huggingface`), not `~/.cache/llama.cpp` — with only the
  legacy mount, ~20 GB of GGUF landed in the container writable layer,
  silently (this build logs no download progress at default verbosity).
  Compose anchor now mounts the volume at both paths. A salvage copy of the
  layer was aborted by rotating the card mid-stream (operator error;
  re-download exposure only).
- **llama.cpp endpoints do not advertise max_model_len** — the context suite
  probed 105k/68k tokens against a 64k window and recorded a false context
  failure until rerun with an explicit `--max-model-len` hint.
- **Gemma E4B default-mode zero-token artifact:** with `--jinja` +
  reasoning-format auto, a benchmark probe recorded 0 completion tokens (all
  output routed to `reasoning_content`); rerun with thinking suppressed.
  Measurement trap, not a serving failure.
- **MTP A/B methodology:** the standard short-completion benchmark cannot
  measure speculative decoding (10-31-token completions; overhead never
  amortizes). Long-generation probes (3x1024 tokens, temp 0) are the deciding
  artifacts for both MTP candidates.
