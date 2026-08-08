# SGLang ignores pinned revision during ModelOpt cache detection

Status: fixed locally

## Symptom

After the Inkling loader dependency was fixed, the managed offline launch found
the pinned model configuration but misclassified the native NVFP4 checkpoint as
an unquantized base model. It entered SGLang's standard quantization workflow and
then failed trying to resolve a separate base model from the network.

The checkpoint's `hf_quant_config.json` is present under exact revision
`b6a99534467840620d411e4cd4ad5819b2610d9c`. SGLang's
`ModelConfig._is_already_quantized()` called `has_hf_quant_config()` without the
model revision, so Hugging Face cache lookup checked mutable `main` instead of
the exact snapshot. A read-only probe returned no default-revision match and the
expected file for the pinned revision.

## Required fix

Pass the configured revision through both the early ModelOpt quantization
detector and Inkling's subsequent raw quantization-config reader. Use it for
local cache, snapshot, and remote file-existence checks. Preserve exact offline
revision pinning; do not create a synthetic mutable `main` ref as a workaround.

## Fix

The pinned Inkling SGLang derived image applies
`configs/runtime-patches/sglang/b90c0d76-inkling-accelerate/pinned-hf-quant-config.patch`.
It adds an optional revision argument to `has_hf_quant_config()`, forwards
`ModelConfig.revision`, and supplies the same revision to Inkling's
`snapshot_download()` config-only lookup. The patch is limited to checkpoint
classification/config resolution and does not change quantization kernels or
weights.

## Verification

The final derived image is pinned as
`anvil-sglang@sha256:6a8afc5ca0036c1be8810443636d6f835702d1e2ae5a1d717990b0baf8e70a2f`.
A read-only offline container probe returned `True` for the exact checkpoint
revision.

The final managed TP=2 rerun entered direct prequantized loading on both ranks
without a base-model Hub request, loaded the exact snapshot offline, and passed
the final functional, capacity, and repeated quality gates.
