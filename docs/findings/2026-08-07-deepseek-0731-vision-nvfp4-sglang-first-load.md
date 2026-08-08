# DeepSeek V4 Flash 0731 Vision (NVFP4) — first GPU load and vision gate

**Capture window:** 2026-08-07 UTC<br>
**Decision:** `no-promotion`; failed back to the promoted 650K Primary in the
same session<br>
**Measured hardware:** 2x NVIDIA RTX PRO 6000 Blackwell Max-Q, exclusive TP=2
over PCIe without NVLink, WSL2<br>
**Evidence:** `compatibility-only` for the engine/checkpoint pairing, bounded
`functional` for the native text lane, bounded and negative `quality` for the
vision lane

## Outcome

The WebBrain 0731 vision overlay loads and serves on two sm_120 cards. That is
the whole of the positive result. Weights fit under TP=2, the custom SGLang
external model and processor register without the silent text-only fallback,
image input reaches the tower, and the model demonstrably conditions on the
image it is given. Text-lane smoke and strict-JSON gates pass.

The capabilities the lane was a candidate for — `vision.ocr` and
`vision.general` — fail. Dense OCR returns fluent placeholder text instead of
the characters on the card. A 56 px, four-line, black-on-white card is
transcribed as an unrelated document title. A blue button's label is named
incorrectly. The same model answers coarse whole-image questions (dominant
colour, button count) correctly on the same images in the same session, so the
failure is not a broken image path; it is a reading and grounding ceiling in
the projector/adapter.

Two further boundaries make additional configuration iteration unjustified
rather than merely unattractive: this checkpoint family has no chat template in
SGLang at the pinned tag (native `/generate` only, OpenAI multimodal chat parts
unsupported upstream), so it cannot serve router chat clients at all; and the
first correctness lane is a 4,096-token window whose headroom is consumed by
~88 GB/card of weights. The 650K Primary was restored and verified healthy the
same session. No alias, route, or promoted serve changed.

This is, as far as the record shows, the first GPU load of this assembled 0731
package anywhere. That is an inference, not a measurement: upstream's own
`VISION_ADAPTER_MANIFEST.json` declares
`gpu_validated_for_this_0731_package: false`, and the 2026-08-07 research sweep
recorded in the intake note found no deployment report for this exact package.
The sibling base-V4 package's B200 TP=4 evidence does not transfer.

## Immutable identity and translated recipe

| Component | Pinned value |
|---|---|
| Model | `webbrain-one/DeepSeek-V4-Flash-0731-Vision-NVFP4` |
| Model revision | `3a8f168ccafeb4304b7490f773ad1bd18852e215` |
| Text weights | exact mirror of `MJPansa/DeepSeek-V4-Flash-0731-NVFP4` @ `64d64cd89bc63a66aa46506da89d7821f7491c62` (175,573,280,882 bytes, 48 shards) |
| Vision tower | MoonViT-3d from `moonshotai/Kimi-K2.6` @ `7eb5002f6aadc958aed6a9177b7ed26bb94011bb`, BF16, frozen |
| Projector | WebBrain PatchMerger, 40,119,040 params, BF16 |
| Image | `lmsysorg/sglang@sha256:7b6a35df9839fd593a94a1eaee82d7777f472225d9f3ad1f8a2e0cb2bd1785d0` (tag `v0.5.16-cu130`) |
| SGLang source commit | `fdebc938f7f4d16fe6b9f55dcd9a767cf0899ea1` (tag-identical to the checkpoint's pinned commit; patched in place, no source build) |
| Quantization | mixed, auto-detected at load: routed experts NVFP4, attention/shared/head/MTP FP8-family preserved |
| KV | `fp8_e4m3` |
| Kernel profile | `--fp4-gemm-backend marlin`, `--moe-runner-backend marlin` |
| Parallelism | TP=2, exclusive `dual-gpu-exclusive` ownership |
| Context and admission | 4,096 tokens, one image per request, chunked prefill 8,192, CUDA graph disabled, server warmup skipped |
| Memory | `--mem-fraction-static 0.97` |
| Serve / container / port | `tp2-deepseek-0731-vision-nvfp4-sglang-4k` / `sglang-tp2-deepseek-0731-vision-4k` / 39070 |
| Served model name | `deepseek-v4-flash-0731-vision-nvfp4-sglang-tp2-4k` |

The managed candidate is
[`configs/deepseek-v4-flash-0731-vision-nvfp4-sglang-4k-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-vision-nvfp4-sglang-4k-recipe.toml).
Source preservation, the B200→Fakoli Dark translation table, the field-report
priors, and the operator decision bar are in the
[recipe intake note](2026-08-07-deepseek-0731-vision-nvfp4-recipe-intake.md).

Three narrow source patches are applied inside the container at start, all with
exact-anchor checks that fail the start if the anchor drifts: the vendor's two
(`python -m deepseek_vision_sglang.patch --apply` — embedding injection and
opt-in portable sparse decode) plus one local patch that forces
`MultimemAllGatherer(enabled=False)` in `logits_processor.py`. See the attempts
ledger for why the third exists. No patch is silent: an anchor mismatch exits
non-zero before the server starts.

## Load and memory (measured)

| Quantity | `dark-compute-a` | `dark-compute-b` |
|---|---:|---:|
| Weight residency at load end | 88.08 GB | 87.87 GB |
| Steady-state used, server healthy | 95,164 MiB / 97,887 MiB | 93,992 MiB / 97,887 MiB |
| Derived reported-free | 2,723 MiB | 3,895 MiB |

Load elapsed 180.6 s from a warm host page cache. `serves mode enter` completed
the exclusive-mode transaction and the serve returned HTTP 200 on its health
gate. Against the standing 3 GiB reported-free policy, `dark-compute-a` fails
and `dark-compute-b` passes; free-VRAM figures are derived from the measured
used/total pair, and WSL/WDDM reports global allocations that include host
display and runtime use.

Startup-log observations recorded at the successful start: the
`Ignore import error when loading deepseek_vision_sglang` marker was **absent**
(so the external multimodal model registered, not the text-only native one);
hybrid FP8+NVFP4 was auto-detected; `marlin`/`marlin` active; KV `fp8_e4m3`.

Single-stream decode was approximately 11 tokens/s on the longest sampled
completion (195 tokens in 17.04 s engine-reported end-to-end, 26-token prompt).
This is one sample, derived by division, with CUDA graph disabled and the
marlin path active. It is an observation, not a throughput measurement, and no
capacity or concurrency lane was run.

## Attempts ledger

Seven starts on 2026-08-07. Full narrative, stack traces, and the isolated
repro attempts are retained in
`.tickets/closed/2026-08-07-deepseek-0731-vision-tp2-memfraction-fit.md`.

| # | Config delta | Outcome |
|---:|---|---|
| 1 | `MEM_FRACTION_STATIC=0.94` | Weights loaded on both ranks; scheduler exited — `Loaded weights leave no GPU memory for the KV cache under --mem-fraction-static=0.94`, engine-measured floor 0.9411. |
| 2 | `0.94` → `0.96` | KV floor cleared; pool allocation still failed (`Not enough memory`). BF16 tower + projector, indexer transients, and the TokenizerManager CUDA context on GPU 0 are outside the static accounting. |
| 3 | `0.96` → `0.97` | Server came up (`The server is fired up and ready`), then every health check failed with a detokenizer-heartbeat message; a `/generate` was accepted by HTTP but never reached the GPUs. Health gate timed out; container stopped and preserved. Isolated repros of the same init path all passed in seconds. |
| 4 | JIT cache volume `deepseek-0731-vision-jit:/root/.cache/tvm-ffi`; dropped `--skip-server-warmup`; `startup_timeout_seconds` 1800 → 4200 | Root cause found, not yet fixed. A SIGABRT stack showed the scheduler inside `fused_marlin_moe -> moe_wna16_marlin_gemm -> load_jit -> tvm_ffi build_inline -> ninja`: the first marlin NVFP4 MoE call JIT-compiles in-container (~133% CPU/rank, GPU 0%). The "no response from detokenizer" text is a misnomer — `/health` issues a 1-token generate whose completion path runs through the scheduler. |
| 5 | (attempt 4 config) | SGLang's stock warmup ran and failed immediately: `texts cannot be empty and tokenizer must be initialized`. The vendor's launch wrapper skips warmup deliberately; the stock warmup request shape is incompatible with this external-processor integration. |
| 6 | Restored `--skip-server-warmup`; kept the JIT volume so the health gate's own generate drives the compile | marlin JIT completed and persisted; the full 43-layer forward ran; then `Fatal Python error: Floating point exception` in `LogitsProcessor._get_logits -> triton_symm_mem_ag.MultimemAllGatherer -> torch.distributed._symmetric_memory` rendezvous. The SIGFPE is at C level, so the class's own try/except NCCL fallback never fires, and v0.5.16 exposes no kill switch. |
| 7 | Added the fail-closed `enabled=False` patch for the multimem gatherer (NCCL all-gather path) | **Started.** Exclusive-mode transaction completed, health 200, all gates below ran against this configuration. |

Two of these are worth upstreaming and are not local misconfigurations:
`--skip-server-warmup` makes readiness a lie by construction on any lane with a
first-request JIT compile, and the symmetric-memory gatherer should be guarded
by a multicast-support probe (or a kill-switch env) rather than derived from TP
topology alone on PCIe hosts without NVLink or fabric.

## Text-lane gates

Native `/generate`, documented DeepSeek encoding with a `</think>` prime,
`temperature=0`. Artifact: `gates-20260807-023153.json`.

| Gate | Visible answer | Verdict |
|---|---|---|
| text-exact | `VISION LANE TEXT OK` | pass, 1.56 s e2e |
| text-json | `{"a":1,"b":[2,3]}` | pass, valid JSON, 1.18 s |
| text-arith (`17 * 23`, number only) | `357` | fail, 0.26 s, 2 completion tokens |

The arithmetic failure was probed further rather than left as a single data
point (artifact `discrim-20260807-023250.json`):

- Without the `</think>` prime, the same question returned `352` in 0.27 s.
  Both quick answers are wrong and both are two-token, zero-reasoning-token
  completions.
- Asked to use long multiplication and show partial products, the model
  produced both partial products correctly (`17 x 3 = 51`, `17 x 2 = 34` placed
  as `340`) and the correct final answer `391`, finishing normally on the stop
  token after 195 completion tokens.

Measured, therefore: the backbone can do the arithmetic when it writes the
work; thinking-disabled snap answers on this lane are unreliable. The
inference — consistent with a 13B-active MoE and with this being an unmodified
mirror of the already-qualified 0731 text weights — is that this is a decoding
posture, not a defect introduced by the vision overlay. It was not isolated
against the promoted text serve, so it stays an inference.

## Vision: grounding proven

Same prompt, opposite images, back to back (`discrim-20260807-023250.json`):

| Probe | Question | Answer | Ground truth |
|---|---|---|---|
| `probe_ocr_dense.png` | dominant background colour, one word | `White.` | white card — correct |
| `probe_scene.png` | dominant background colour, one word | `Blue.` | `#87b5d9` sky fills the upper two-thirds — correct |
| `probe_gui_mock.png` | how many rounded rectangular buttons, number only | `2.` | two rounded rectangles (Deploy Model, Rollback) — correct |

Two identical prompts on different images produce different, image-appropriate
answers. Image tokens were reported per request (532–561), and the image path,
processor, and conditioning are working. This is the load-validation result.

## Vision: quality failures

All quotes are exact visible answers. Probes are deterministic generated RGB
PNGs; their generators are archived with the artifacts, so the ground truth is
exact rather than judged.

**Dense OCR — confabulated placeholders.** Prompt: report the username, email,
and commit hash exactly as shown. Answer:

```text
Username: johndoe
Email: johndoe@example.com
Commit hash: 1234567890abcdef
```

The card actually reads `kd_marchetti_82`, a synthetic plus-addressed email
(masked here because tracked findings are public; the exact string is
preserved with the probe image and its SHA-256 in the private evidence
archive), and `3a8f168ccafeb4304b7490f773ad1bd18852e215`. All identity strings
on the probe cards are synthetic, generated for this probe; they correspond to
no real person, account, or service. Nothing in the answer is a misreading of
what is on the image; all three fields are generic filler rendered in the
requested shape, with no hedge. This is precisely the failure
mode WebBrain's own vision shootout flags as the critical browser-agent
weakness.

**Large-font OCR — unrelated document.** A 1024x512 white card with four
56 px black lines (`USER: anvil_kd82`, `CODE: XR-7734-BQ`, `PORT: 39070`,
`STATUS: HEALTHY`), prompted to transcribe every line exactly and mark
unreadable parts. Answer:

```text
Title: The Water Cycle.
```

Because the text is 56 px on white, this rules out a resolution or
merged-token ceiling as the explanation for the dense-OCR failure.

**GUI affordance — degenerate, then wrong.** Prompted to list every clickable
control with label and type, the model answered `Yes.` (3 tokens) to a
non-yes/no question. Re-asked the narrowest possible version — name the label
on the blue button — it answered `Contact Us.`; the blue button reads
`Deploy Model`. The mock contains no "Contact Us" element.

**Natural-scene description — confabulated.** Prompted to describe
`probe_scene.png` in two sentences, the model answered:

```text
The image shows a green triangle on a white background.  The triangle is
pointing up.
```

The image is a light-blue sky over a green ground band with a yellow sun disc,
two grey mountain polygons, a brown house with a red roof, and a blue pond. The
gate runner recorded `hard_pass: true` for this row only because its
`expect_contains` list was empty; its `soft_contains` probes (`mountain`,
`sun`, `house`) scored zero hits. Treat this row as a failure — the runner's
boolean is vacuous here, and the finding follows the artifact, not the boolean.

**Calibration works when it is asked for.** Given the dense card and explicit
permission to decline — "if you cannot read them clearly, say 'not clearly
readable' instead of guessing" — the model answered `Not clearly readable.`
The capability to abstain exists; the default posture is to confabulate
confidently. Artifact: `final-20260807-023416.json`.

### Probe set identity

| File | SHA-256 |
|---|---|
| `probe_ocr_dense.png` | `ef7bc66e4e150842463c25bdf9c06e7002d9f520e5ff71d66046c55c02bed11d` |
| `probe_gui_mock.png` | `67c9aa0b4002d26832fb4ed4108ebf0f1f3783903d74a5b5e4facaa17884456e` |
| `probe_scene.png` | `748087c24d34f8bd741a1f5aaefd8912c83591a33f52592f7ec75915c142e5c3` |
| `probe_ocr_bigfont.png` | `e8cabe36645721975e364e25f94f58541723467093a004fecd271cb72df94f0d` |

## Caveats

- This is a single serve window on one 4,096-token correctness lane at
  concurrency 1. There is no capacity, concurrency, long-context, multi-image,
  or repeated-attempt evidence, and none was attempted.
- The vision quality result is a bounded probe set of four synthetic images, not
  a benchmark. It is sufficient to fail the target capability because the
  failures are confabulations against exactly-known ground truth, not
  near-misses.
- Only the `marlin`/`marlin` kernel pair was exercised. The upstream
  `blackwell-native` flashinfer_trtllm pair is SM100-gated and confirmed broken
  on sm_120; `flashinfer_cudnn` FP4 GEMM remains an untried second lane. A
  kernel change would alter throughput, not the BF16 tower and projector that
  produce the image features — so it is not a plausible route to fixing the
  vision quality result.
- The text-arithmetic failures were not A/B'd against the promoted 650K Primary
  serve, so "not a lane defect" remains an inference.
- Timings are client- or engine-observed single samples on a warm system and
  are not comparable to any other lane's published metrics.
- No licensing review was performed on the assembled package or the patched
  runtime; this result does not authorize redistribution of either.

## Decision and promotion boundary

Decision label: `no-promotion`. Evidence labels: `compatibility-only` and
bounded `functional` for the load and text path, bounded negative `quality` for
vision. The lane is **not-qualified** for `vision.ocr` or `vision.general`.

Grounds, in the order they bind:

1. **No chat template.** The checkpoint's tokenizer reports
   `chat_template=None` and OpenAI multimodal chat parts are unsupported
   upstream at this pin. The serve speaks native `/generate` only, so it cannot
   serve a router chat client regardless of quality. Nothing in the anvil
   routing contract admits a tier that cannot answer a chat request.
2. **The target capability confabulates.** OCR and GUI grounding are exactly
   what `vision.ocr` and `vision.general` would be used for, and both produce
   confident, well-formed, wrong output.
3. **4,096-token first lane.** ~88 GB/card of weights leaves no room to raise
   this materially at this quantization mix; 4K–8K is the realistic ceiling for
   this checkpoint under TP=2 here.
4. **Adapter-inherent ceiling.** The tower and projector are BF16 and untouched
   by MoE kernel selection, so further engine-configuration iteration cannot
   move the quality result.

The overnight authorization was to leave a genuine stand-in serving for morning
review or otherwise fail back. It failed back:
`tp2-deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k` was restored and
its health verified in the same session. Publishing this finding does not
authorize `serves up`, `serves promote`, router configuration, alias changes,
or teardown; those remain separately human-gated.

## Raw artifacts

Raw gate JSONs, probe PNGs, probe generators, gate runners, and the container
startup logs are archived in the private operator repository under
`evidence/2026-08-07-deepseek-0731-vision-first-load/`:

- `gates-20260807-023153.json` — six-gate evidence ladder (three text, three
  image) with full request, response, `meta_info`, and per-gate assertions.
- `discrim-20260807-023250.json` — image-discrimination pair, button count, and
  the arithmetic discrimination probes.
- `final-20260807-023416.json` — big-font OCR, calibrated dense OCR, and the
  narrowed blue-button probe.
- `probe_ocr_dense.png`, `probe_gui_mock.png`, `probe_scene.png`,
  `probe_ocr_bigfont.png` and their generators.

The attempt-by-attempt operator log is
`.tickets/closed/2026-08-07-deepseek-0731-vision-tp2-memfraction-fit.md`. Source
preservation and the upstream translation table are in the
[recipe intake note](2026-08-07-deepseek-0731-vision-nvfp4-recipe-intake.md).
