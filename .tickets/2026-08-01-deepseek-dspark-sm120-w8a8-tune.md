# Qualify the missing DeepSeek DSpark SM120 dense W8A8 kernel configuration

**Observed:** 2026-08-01

## Problem

Both TP ranks warn that no exact W8A8 block-FP8 configuration exists for:

`N=4096,K=12288,dtype=fp8_w8a8,block_shape=[128,128]`

on `NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition`. vLLM therefore
uses its default Triton configuration. The warning is nonfatal and is independent
of the later 128K KV-cache failure.

The pinned engine already includes the upstream SM12x low-M fallback from commit
`f8f18ce66b5378e00124436e5b7b183b8269a602`; an absent JSON file does not prove
that a generated tune will improve end-to-end performance. Prior vLLM operator
evidence also records generated W8A8 configurations that regressed performance.

## Tuner qualification note

The pinned official tuner evaluates 1,280 candidate configurations per batch
size. For the exact missing shape and its complete 18-size batch surface, that is
23,040 configuration trials and 345,600 kernel launches including five warmups
and ten timed iterations per trial. Run a bounded completed-batch pilot before
publishing a duration estimate.

The current tuner reports latency with an extra division by ten in
`benchmark_config`. That constant does not change which configuration wins, but
its printed microsecond values must not be published as calibrated latency until
the reporting defect is fixed or independently timed.

## Resolution path

1. First complete the functional DSpark startup and default-kernel end-to-end
   baseline with three warmed repetitions.
2. Run the pinned official tuner in an isolated managed workload against the
   exact engine, GPU product, TP=2 geometry, dtype, block shape, and full batch
   surface.
3. Store the artifact under the engine-revision/GPU compatibility key with a
   `kernel-tune-manifest/v1` record and the engine-required filename.
4. Activate it only through a read-only managed mount or pinned derived image and
   prove the exact file was selected in startup logs.
5. Adopt only for at least 5% primary end-to-end improvement across three warmed
   runs, with no protected lane regressing more than 3% and all hard gates passing.

## Acceptance

- Default and tuned recipes differ only in explicit tune activation.
- The tuned startup has exact selection proof and no fallback warning for the
  `N=4096,K=12288` shape.
- Functional gates remain at 100%.
- The manifest records tuner duration, complete batch coverage, raw evidence,
  measured deltas, and an accepted/rejected/inconclusive decision.
- A microbenchmark-only win is not sufficient for adoption.
