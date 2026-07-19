# Gemma 4 31B current-template optimization probe

**Captured:** 2026-07-17
**Host/topology:** Fakoli Dark, Docker Desktop on WSL2; one lab serve on the RTX PRO 6000 Blackwell Max-Q (96 GB, 300 W power limit)
**Engine:** `vllm/vllm-openai:v0.25.1`, V1 runner, FP8 KV cache, Triton attention selected by vLLM
**Model:** `google/gemma-4-31B-it-qat-w4a16-ct` at `a766e9afa44931dfa9ff5de90af9494ca193e74c`; current base tokenizer/template from `google/gemma-4-31B-it` at `b9ea41a2887d8607f594846523f94c6cc75ac8a4`

## Outcome

The current official 31B QAT W4A16 continuous-batching checkpoint is healthy with the new Google
chat template at 128K and passed the smoke plus structured-JSON preflight with thinking disabled.
Its warmed, single-request long generation was **62.3 tok/s**: two equal 512-token responses took
8.270 s and 8.171 s end-to-end. A prior 1,024-token run took 17.710 s (57.8 tok/s), including its
first request's Triton JIT warm-up.

The 128K capacity probe recorded **74.97 s TTFT**, but generated only 41 completion tokens, so it
is useful as a prefill observation rather than a decode-rate comparison. The local TTFT is behind
the current external single RTX PRO 6000 NVFP4 reports (about 46--48 s at 128K), although those
reports use a different NVFP4 checkpoint and likely a higher-power workstation card. This Max-Q
card is power-limited to 300 W, so the external number is an advisory target rather than a
like-for-like regression threshold.

No production recipe, router policy, or tier changed. The managed 12B Heavy serve remains the
rollback/production target and is restored after this lab capture.

## Native MTP result: blocked for this target

vLLM 0.25.1 successfully recognized `method=mtp` and loaded Google's
`google/gemma-4-31B-it-qat-q4_0-unquantized-assistant` (one speculative token). It then failed
during engine profiling with an incompatible projection dimension: target activations of 6400
versus assistant weights expecting 10752. This is a real incompatibility between the W4A16
continuous-batching target and that Q4 assistant, not a quoting or startup failure.

Consequently, do not add this assistant to the W4A16 recipe. Google's QAT documentation pairs
the Q4 assistant with the matching Q4 unquantized target; testing that distinct, much larger
target is a separate capacity and quality experiment, not a drop-in speed flag.

## Reproduction and raw evidence

- [128K baseline preflight](2026-07-17-gemma4-31b-optimization-evidence/vllm-31b-qat-128k-baseline-preflight.json)
- [128K capacity probe](2026-07-17-gemma4-31b-optimization-evidence/vllm-31b-qat-128k-baseline-c1.json)
- [1,024-token diagnostic](2026-07-17-gemma4-31b-optimization-evidence/vllm-31b-qat-128k-c1-longgen.json)
- [warmed 512-token repeats](2026-07-17-gemma4-31b-optimization-evidence/vllm-31b-qat-128k-c1-longgen-warm.json)

The long-generation suite asks for a trailing marker. Gemma continued emitting `x` through the
entire cap and omitted it, so the deterministic marker check correctly failed. Both warmed
attempts nevertheless returned exactly 512 completion tokens with `finish_reason: length`; their
recorded API timing is valid diagnostic throughput evidence, not a quality pass.

## Research implications

- Google's current template's thinking-off primer is a behavior/control change, not an expected
  source of this scale of decode slowdown. Retain the pinned current tokenizer revision and send
  `chat_template_kwargs={"enable_thinking": false}` for matched disabled-thinking benchmarks.
- At multi-turn boundaries, strip prior thought-channel content as required by Google's template
  guidance; retain it only across tool-call subturns. This avoids unnecessarily inflating context
  and preserves tool semantics.
- WSL2 is not the immediate explanation for the 128K gap: models are on an ext4 Docker volume,
  the recipe already enables `VLLM_WSL2_ENABLE_PIN_MEMORY=1`, and this model is fully GPU
  resident. Updating WSL and Docker Desktop is worthwhile for stability and GPU fixes, but raising
  WSL system-memory limits will not directly make GPU decode faster. Treat pinned-memory changes
  as a separate controlled A/B because WSL/WDDM page-locked memory can pressure the host.

## Sources

- Google, [Gemma thinking control](https://ai.google.dev/gemma/docs/capabilities/thinking), accessed 2026-07-17.
- Google, [Gemma 4 prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4), accessed 2026-07-17.
- vLLM, [MTP speculative decoding](https://docs.vllm.ai/en/stable/features/speculative_decoding/mtp/), accessed 2026-07-17.
- Millstone AI, [31B NVFP4 RTX PRO 6000 reference](https://www.millstoneai.com/inference-benchmark/gemma-4-31b-nvfp4-1x-rtx-pro-6000-blackwell), advisory external comparison, accessed 2026-07-17.

External results are not a local quality or promotion result. The official checkpoint/template and
the exact local artifact links above are the decision evidence for this finding.
