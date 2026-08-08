# DeepSeek V4 Flash 0731 Vision (NVFP4) — recipe intake and translation (draft)

Status: intake record (START_HERE Steps 1–3) for the campaign completed the
same day. The execution outcome, measurements, and `no-promotion` decision are
in the companion finding:
[2026-08-07-deepseek-0731-vision-nvfp4-sglang-first-load.md](2026-08-07-deepseek-0731-vision-nvfp4-sglang-first-load.md).

## Step 1: source preservation

- URL: <https://huggingface.co/webbrain-one/DeepSeek-V4-Flash-0731-Vision-NVFP4>
- Pinned revision: `3a8f168ccafeb4304b7490f773ad1bd18852e215`
  (lastModified 2026-08-05, 107 files)
- Publisher: WebBrain (webbrain-one); observed 2026-08-07; age class: current.
- Evidence class: community recipe / experimental development checkpoint.
- Package: complete text checkpoint with vision overlay
  (`VISION_ADAPTER_MANIFEST.json`, schema v2):
  - Text: exact mirror of `MJPansa/DeepSeek-V4-Flash-0731-NVFP4` @
    `64d64cd89bc63a66aa46506da89d7821f7491c62` (48 shards, 175,573,280,882
    bytes, tensors unmodified; mixed precision — routed experts NVFP4, other
    paths preserve 0731 source formats). Conversion source
    `deepseek-ai/DeepSeek-V4-Flash-0731` @ `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`.
  - Vision tower: MoonViT-3d from `moonshotai/Kimi-K2.6` @
    `7eb5002f6aadc958aed6a9177b7ed26bb94011bb`, BF16, 833,765,768 bytes,
    SHA-256 `1382c41f…8f15`, frozen.
  - Projector: WebBrain PatchMerger, 40,119,040 params BF16, 80,238,568 bytes,
    SHA-256 `7024d9d5…577a`, `LayerNorm -> 2x2 merge -> Linear(4608,4608) ->
    GELU -> Linear(4608,4096)`.
  - Routing bridge: text routing IDs preserved; image positions cycle a
    checked-in 64-ID palette; substitution applies during extend/prefill only.
- Serving: custom SGLang external model/processor (`sglang_ext/`), pinned
  SGLang source commit `fdebc938f7f4d16fe6b9f55dcd9a767cf0899ea1`, two narrow
  source patches (embedding injection + opt-in portable sparse decode).
  Stock SGLang unsupported. OpenAI multimodal chat parts unsupported — native
  `/generate` with `<image>` marker + base64 `image_data` only.
- Upstream validation boundary (author's own claim): repository assembly,
  hashes, and config/index augmentation verified; **no GPU loader/startup or
  image smoke test has ever been run on this assembled 0731 package**
  (`gpu_validated_for_this_0731_package: false`). The sibling base-V4 package
  passed B200 TP=4 startup and two image smokes; that evidence does not
  transfer.
- Decision the source changes: candidate path to vision on the exact 0731
  backbone family already promoted as Primary; would be a `vision.*` challenger
  only, `no-promotion` by default.

## Step 2: translation table (B200 recipe -> Fakoli Dark)

| Upstream assumption | Local value | Evidence or change required |
|---|---|---|
| Checkpoint/revision | `webbrain-one/DeepSeek-V4-Flash-0731-Vision-NVFP4` @ `3a8f168ccafeb4304b7490f773ad1bd18852e215` | Pin in recipe; re-verify manifest SHA-256s after pull. |
| Engine/image | SGLang built from source commit `fdebc938…` + `sglang_ext` PYTHONPATH + `patch.py --apply` | No known image tag pinned by upstream; must locate/build a container image whose SGLang matches the pinned commit. Existing SGLang lanes (`fast-glm47-flash-sglang`, `cand-laguna-xs-21-nvfp4-sglang`) are precedent for SGLang serves but their image commits are unverified against this pin. |
| GPU/topology | B200 (sm_100), TP=4, `DEEPSEEK_VISION_TP=4` | 2× RTX PRO 6000 Blackwell Max-Q (sm_120), PCIe, TP=2 exclusive mode. `DEEPSEEK_VISION_TP=2`. Record NCCL/P2P from startup logs. |
| Kernel profile | `blackwell-native`: `flashinfer_trtllm` FP4 GEMM + `flashinfer_trtllm_routed` MoE | trtllm-gen kernel availability on sm_120 unproven; wrapper ships a `marlin` fallback profile — plan first lane on `marlin`, attempt `blackwell-native` second. Pinned SGLang contains `flash_mla_with_kvcache_sm120` (native SM120 MLA), a materially good sign. |
| Quantization/KV | Mixed: routed experts NVFP4; attention/shared/head/MTP retain 0731 source formats (FP8 family) | Confirm exact formats from startup logs; do not conflate with our pinned W4A16/W4A4 comparison profiles. |
| Context/concurrency | `context-length 4096`, 1 image/request, ≤512 merged image tokens, `mem-fraction-static 0.85`, CUDA graph disabled, warmup skipped | Keep 4096/c1 for first correctness lane. **Fit risk:** ~175.6 GB weights TP=2-sharded ≈ ~88 GB/card on 96 GB cards leaves ~6–8 GB/card for KV, activations, and CUDA context; `mem-fraction-static` likely needs raising above 0.85 just to fit weights. TP=2 fit is unproven and is the first gate. |
| Parsers/tools | DeepSeek template with `</think>` prime; native `/generate`; no tool-calling claim for image lanes | Visible-answer gating per evidence ladder; router/OpenAI integration out of scope (unsupported upstream). |
| Speculation | None in the vision profile (MTP tensors preserved but unused) | No spec config in first lanes; DSpark comparisons are a separate text-lane concern. |
| CPU/KV offload | None | Not applicable; still inspect `host shared-memory status` before/after per the open P1 guard. |

## Step 3: starting-state snapshot (2026-08-07, read-only)

- Worktree: `.claude/worktrees/deepseek-v4-flash-vision-7f65c4`, branch
  `claude/deepseek-v4-flash-vision-7f65c4`, HEAD `4aee758`, clean.
- Mode: `dual-gpu-exclusive`; exclusive owner
  `tp2-deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k` (TP=2, healthy,
  HTTP 200), owning both `dark-compute-a` and `dark-compute-b`; both cards
  ~94.7 GiB committed, ~94 MiB free. All other serves absent/blocked.
- Shared memory: available, zero active offload containers, zero mmap files,
  zero reclaimable bytes.
- Model cache: 1.13 TB available of 2.16 TB. Neither
  `webbrain-one/DeepSeek-V4-Flash-0731-Vision-NVFP4` nor
  `MJPansa/DeepSeek-V4-Flash-0731-NVFP4` present in the cache repository
  inventory — a pull is the full ~176 GB.
- Private operator-home snapshot (`hosts/dark/operator-home`) still fails
  manifest validation on the known missing router-profile closure
  (`anvil-router.deepseek-pi.toml`, `anvil-router.qwen35-rollback.toml`);
  live CLI default home was used for this snapshot, consistent with
  `CURRENT_STATE.md` and `migration/2026-08-02-dark-operator-home.md`.

## Blocking decision points (operator)

1. **Primary downtime.** Any load requires the exclusive TP=2 slot, i.e.
   stopping the promoted 650K Primary that Pi/OpenClaw currently use.
2. **~176 GB pull** of one exact revision (storage is gated: 1.13 TB free).
3. **Engine build lane.** Sourcing or building an SGLang image at the pinned
   commit is new engineering; decide whether that build effort is worth an
   experimental adapter whose 0731 assembly has never been GPU-validated
   anywhere.

## Field-report priors (2026-08-07 research sweep)

- No deployment reports exist for this exact 0731 package (HF discussions: one
  unanswered "why not k3?" question; no commits after `3a8f168`).
- A community sibling of the same architecture pattern (MoonViT bridge over a
  DeepSeek spec-decode backbone, "FlyCockpit" on the NVIDIA DGX Spark forum)
  reports: strong on screenshots/UI/on-screen text, "decent but generic" on
  natural photos — "a screenshot specialist, not a general VLM" — and that the
  vision wrapper initially broke DSpark speculative-decode acceptance (50–64%
  after fixing). Our SGLang lane runs no speculation, so the breakage does not
  apply, but any future DSpark+vision lane must A/B acceptance rates.
- Baseten's GLM-5.2-Vision (the credited method source) needed an RL stage
  after SFT to fix verbose/rambling long descriptions; WebBrain describes no RL
  stage, so long-description degeneration is a likely failure mode.
- WebBrain's own vision shootout flags mangled OCR identifiers and
  "Unknowns: None" overconfidence as the critical browser-agent weaknesses.

Image probe set derived from these priors: (1) dense small-text OCR fidelity
(usernames/emails/code); (2) ambiguous-UI uncertainty calibration; (3) GUI
affordance classification (dropdown vs static text vs button); (4) long-form
description degeneration; (5) natural-photo sanity check; (6) known-image
smoke via the repo's own script.

## Overnight decision bar (operator pre-authorization, 2026-08-07)

The operator authorized an autonomous overnight run: iterate configurations,
fail back to the 650K Primary unless the candidate is genuinely promotable as a
stand-in, in which case leave it serving for morning review. Per the repo
contract, "promotable tonight" requires at minimum: the full thinking-disabled
functional ladder (smoke/JSON/tools/malformed/recovery, 100% deterministic,
visible answers), coding + multi-step tool tasks, text-lane non-regression at
the promoted 262144/8192 envelope with measured capacity/latency, concurrency
and per-card reserve sampling, and a passing native-`/generate` vision
preflight. Sustained multi-turn reasoning evidence and broad multi-agent
quality history structurally cannot be produced in one night, so if the
candidate is left serving it is labeled `challenger` with explicit morning
review required — never `current`. Any failed MUST gate, instability, or doubt
means restore `tp2-deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k` and
verify health before finishing. All outcomes publish the full artifact matrix
(finding + runs row + dossier + hardware page + validation suite).

## Next actions when approved

1. Resolve an SGLang image/build matching `fdebc938…`; verify
   `patch.py --check` passes against it offline.
2. Author the serve recipe (marlin first lane, TP=2, 4096/c1) with exact pins.
3. Schedule the Primary downtime window; pull, load, and run the evidence
   ladder (text-parity smoke first, then one known image, then fresh GUI
   probes), restore Primary, publish.
