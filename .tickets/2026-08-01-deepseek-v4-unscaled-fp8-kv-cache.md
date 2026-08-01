# Avoid unscaled FP8 KV cache in the DeepSeek V4 correctness recipe

**Observed:** 2026-08-01

## Problem

The first exact DeepSeek V4 Flash 0731 TP2 launch requested
`--kv-cache-dtype fp8_e4m3`. The pinned SGLang runtime reported that the
checkpoint provides no KV scaling factors, defaulted every scale to 1.0, and
warned that this may reduce accuracy.

The publisher's current SGLang example does not request an FP8 KV-cache dtype.
However, the pinned runtime's DeepSeek-specific defaults force FP8 even when the
flag is omitted. Its CLI advertises both `bf16` and `bfloat16`, but the exact
DeepSeek V4 post-processor rejects the `bf16` alias and accepts only the literal
`bfloat16`. The campaign recipe is limited to 32K context and has sufficient
memory for BF16, so retaining the warning would trade correctness for unneeded
capacity.

## Impact

- Quality and agent-tool results could be degraded by uncalibrated KV values.
- Community benchmark data would conflate model quality with a locally imposed
  lossy cache configuration.
- The warning is easy to miss after the 48-shard model load.

## Investigation outcome

Two BF16 spellings were tested before any benchmark request. `bf16` failed the
DeepSeek hook's accepted-value assertion even though the CLI advertises it.
`bfloat16` passed argument validation and loaded all weights, but both schedulers
then failed because the CUDA DeepSeek V4 memory pool asserts byte storage
(`store_dtype == torch.uint8`). The exact pinned CUDA path therefore requires
FP8 E4M3 KV storage.

Restore the working `--kv-cache-dtype fp8_e4m3` recipe, keep the weight
quantization and FlashInfer MXFP4 expert backend unchanged, and publish the
missing-scale warning as an accuracy caveat. Do not describe this lane as a
strict-quality reference until calibrated scale support exists.

## Acceptance

- Startup reaches health on the required FP8 E4M3 cache path.
- The exact 32K TP2 recipe reaches health within the bounded startup window.
- Functional, context, tool, capacity, and repeated quality gates run only on
  the corrected serve.
- Functional and repeated quality gates quantify the working path rather than
  hiding the warning.
- Any later calibrated FP8 KV A/B uses a separate config ID.

## Runtime gap

SGLang should normalize the documented `bf16` alias before its DeepSeek V4
assertion, make its CUDA DeepSeek V4 memory pool support the accepted
`bfloat16` dtype, or stop advertising that combination. The runtime also needs
a calibrated FP8 scale path or a less alarming model-specific explanation when
unit scaling is intentional.
