# Kernel tune contract

## Canonical storage

Store repository-owned tunes at:

```text
configs/kernel-tunes/<engine>/<engine-revision>/<gpu-slug>/
├── manifest.json
└── configs/
    └── <exact engine-required filename>
```

Use the immutable engine commit or build revision, not a floating release tag.
Derive `gpu-slug` from the complete GPU product name and keep the GPU UUID in
the manifest as run evidence, not as the portability key.

Store raw logs and default/tuned benchmark output under the dated finding's
evidence directory. Link them from the manifest instead of copying large logs
into `configs/kernel-tunes/`.

## `kernel-tune-manifest/v1`

Record:

- schema, decision (`accepted`, `rejected`, or `inconclusive`), observed date,
  artifact path, byte count, and SHA-256;
- runtime image tag and digest, engine/tuner revisions, tuner arguments and
  pinned tuner-only dependencies;
- model repository and revision, served name, kernel family, dtype, tensor
  parallel size, model geometry, and complete tuned batch-size list;
- GPU product name, UUID, compute capability, memory, driver, CUDA, and Triton;
- start/end timestamps, duration, completion state, and any retries or
  detached-client events;
- default and tuned recipe identities plus raw functional, capacity,
  microbenchmark, startup-log, and restoration evidence links;
- predeclared decision thresholds, measured deltas, recommendation, and exact
  applicability boundary;
- activation environment variable, container path, read-only mount or pinned
  derived-image identity, and managed recipe references.

## Compatibility key

Treat a tune as incompatible until requalified when any of these change:

- GPU product or compute capability;
- engine, tuner, CUDA, or Triton build;
- kernel family or quantization dtype;
- tensor-parallel size;
- expert count, hidden/intermediate dimensions, block shape, or other
  filename/config lookup geometry;
- engine config schema.

A driver change requires at least startup proof and the paired representative
performance lane. A material driver/CUDA transition requires a full retune.

## Selection and activation

The runtime-required config filename is part of the lookup contract. Preserve
it byte-for-byte. Select only an artifact whose manifest matches the active
compatibility key and whose decision is `accepted`.

Storage is inert. A tune is active only when a managed recipe supplies its
`configs/` directory through a read-only mount or pinned derived image layer,
sets the engine-supported config-folder control, and startup logs prove that
exact artifact was selected. For vLLM, use `VLLM_TUNED_CONFIG_FOLDER` only
with a runtime version that documents or implements that lookup.

Never copy a tune into a global cache or base image without its manifest and an
explicit recipe reference. Never silently fall back to a nearby GPU name or
floating engine tag.

## Evidence and decision

Run an identical default-versus-tuned A/B with at least three warmed
repetitions. Hold model, revision, runtime, GPU, recipe flags, prompts, context,
concurrency, sampling, and request order constant.

Functional hard gates must remain at 100 percent. Prefer end-to-end request
metrics over tuner microbenchmarks. If the campaign declares no threshold, use
at least 5 percent improvement in the primary throughput or p95-latency lane
with no protected-lane regression greater than 3 percent. Otherwise mark the
result inconclusive or rejected.
