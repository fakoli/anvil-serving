# T011 OCR bring-up + dark-fast resident-set rebalance (gpu-reservations:T011)

**Point-in-time record, 2026-07-13.** The operator resolved the T015 capacity conflict
documented by the first T011 pass (fast 18432 + embeddings 3200 + reranker 3456 left
351 MiB free — `serves up ocr` correctly denied): rebalance the dark-fast ledger so the
FULL resident set (fast + embeddings + reranker + ocr) is admitted, bring the
PaddleOCR-VL-1.6 serve up resident, redeploy the router image with the T011 routes, and
verify a routed OCR request end-to-end.

## What changed

| Item | Before | After |
|---|---|---|
| fast serve checkpoint | `google/gemma-4-E4B-it` bf16 (gemma4-unified image) | `leon-se/gemma-4-E4B-it-FP8-Dynamic` @ `56e30bf6…` (standard pinned vLLM image, native `gemma4_mm`) |
| fast `vram_mib` / util | 18432 / 0.5653 | 14336 / 0.4396 |
| dark-fast `reserve_mib` | 7168 | 4608 |
| ocr serve | declared, admission-denied | **resident, running** (:30007, 5120 MiB) |
| deployed router | `anvil-serving:0.13.0rc1-t010` (predates ocr routes + image wire fidelity) | `anvil-serving:0.13.0rc1-t011` (built from this branch) |
| router config | `2026-07-13.fakoli-dark-gemma4-e4b-fast` | `2026-07-13.fakoli-dark-t011-ocr-fp8-fast` (ocr-local tier + `ocr` preset + fast `quantization = "fp8"`) |
| profile | fast-local rows on the bf16-serve fingerprint | re-stamped to `de402664…` (the fp8 tier identity), verdicts unchanged, still uncalibrated ([promotion-profile.json](2026-07-13-t011-ocr-rebalance-evidence/promotion-profile.json)) |

Promotion path: `router up` (image recreate, healthz 200) then `router promote --profile …
--config … --image anvil-serving:0.13.0rc1-t011` — image-loader validate (`--validate-only`
passed first), volume backup, atomic write, reload, crash-loop verify: all passed.

## Honest deviation from the operator's numbers

The operator target was fast at **10240 MiB (E4B fp8)**. Measured on this box, that is
physically unreachable — E4B's bf16 embeddings / per-layer embeddings survive every fp8
shape:

* online `--quantization fp8` in the gemma4-unified image: CUTLASS fp8 GEMM is broken
  (torch stable-ABI rejects fp8 dtypes, "Not yet supported ScalarType 46");
* `VLLM_TEST_FORCE_FP8_MARLIN=1`: loads at **11.03 GiB** (already > 10240) and crashes in
  the fp8-quantized vision tower ("unsupported `a` scalar_type" — the transformers-backend
  forward casts pixel_values to the fp8 weight dtype);
* the pre-quantized FP8-Dynamic checkpoint (towers/lm_head kept bf16 in its `ignore`
  list): loads at **11.49 GiB**.

**14336 MiB** is the honest-measured fast reservation for the FP8-Dynamic serve: 11.49 GiB
weights + 0.8 GiB KV (43,708 tokens > the 32768 window, 1.33× max concurrency) + CUDA-graph
/ runtime overhead at `--gpu-memory-utilization 0.4396`.

## Post-rebalance ledger (live, 2026-07-13)

```
gpu_role 'dark-fast': capacity 32607 MiB, reserve 4608 MiB, committed 26112 MiB, free 1887 MiB
  fast 14336 MiB (resident, running)
  embeddings 3200 MiB (resident, running)
  reranker 3456 MiB (resident, running)
  ocr 5120 MiB (resident, running)
```

Honesty note (also in serves.toml): observed non-ledger draw (Windows display + the voice
STT/TTS sidecars) was ~5.7 GiB — above the 4608 reserve — so the ledger free figure runs
~1.1 GiB optimistic; physical device-used with the full set resident is ~30.0 of 32.6 GiB.

## Live verification

* fast (FP8-Dynamic): `/health` 200; direct `gemma4-e4b-it` completion exact-match; routed
  `chat-fast` completion through the deployed tailnet front door exact-match, `finish stop`.
* ocr: admitted by the ledger, `/health` 200; direct extraction of the PaddleOCR demo page
  ([direct-ocr-extraction.txt](2026-07-13-t011-ocr-rebalance-evidence/direct-ocr-extraction.txt)):
  1234 prompt → 1606 completion tokens, `finish_reason: stop`.
* routed `model: "ocr"` + `image_url` through the DEPLOYED router
  ([routed-ocr-extraction.txt](2026-07-13-t011-ocr-rebalance-evidence/routed-ocr-extraction.txt)):
  same 1234→1606 token shape, 2550 chars extracted, `finish_reason: stop`.
* `/v1/models` discovery: `['planning', 'quick-edit', 'review', 'chat', 'chat-fast',
  'long-context', 'ocr']`.
* T010 purpose routes intact on the new image: routed `/v1/embeddings` → 1024-dim vector.
* Voice sidecars untouched (STT/TTS `/health` 200); PRO 6000 heavy untouched.

## Pending (flagged, not silently skipped)

* **OpenClaw gateway apply on Fakoli Mini**: `harness sync openclaw --config … --dry-run`
  renders `anvil/ocr` into the provider allowlist (model_count 7). The remote apply
  (`--gateway-host fakoli-mini --confirm`) was denied by the operator's execution policy in
  this session — run it from an authorized session to complete the ADR/CLAUDE.md lockstep.
* fast-local profile rows remain **uncalibrated** on the new fp8 fingerprint
  (gpu-reservations:T008 / `eval calibrate` is the measured write-back path).
