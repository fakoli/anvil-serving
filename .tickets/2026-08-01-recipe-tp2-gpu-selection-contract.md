# Make multi-GPU recipe selection a first-class contract

**Observed:** 2026-08-01

## Problem

The managed recipe loader can successfully preview a comma-separated pair of GPU
UUIDs through `--gpu-device`. The original Docker argv emitted the pair as an unquoted `--gpus` CSV
value, however, so Docker rejected it with `invalid count` before creating the
container. After fixing that boundary, the first real vLLM launch failed because
the loader also copied the physical UUID pair into the container's
`CUDA_VISIBLE_DEVICES`; vLLM's device mapper requires container-relative numeric
indices and raised `ValueError: invalid literal for int()` before TP initialization.
The help text also described the option as accepting "one" UUID or
index, and the recipe schema has no explicit tensor-parallel topology fields.

That mismatch makes an exclusive TP2 recipe look unsupported even though its
generated command is correct. It also leaves the relationship among GPU count,
`--tensor-parallel-size`, operating mode, and the exclusive serve manifest to
operator convention instead of a validated product contract.

## Reproduction

Run a load dry-run with two comma-separated discovered UUIDs, then apply it. The
preview succeeds, but the pre-fix apply fails at Docker argument parsing:

```text
anvil-serving models recipes load MODEL --container NAME \
  --gpu-device GPU-A,GPU-B --dry-run
```

```text
invalid argument "device=GPU-A,GPU-B" for "--gpus" flag: invalid count
```

## Campaign fix

The loader now retains the literal quotes Docker's CSV parser requires for a
multi-device `--gpus` request and rejects empty or duplicate device elements.
Portable TP2 recipes explicitly opt into `CUDA_VISIBLE_DEVICES=0,1`; validation
requires a distinct numeric index for every selected physical GPU. Focused tests
cover both sides of that mapping and cardinality failures.

## Proposed resolution

- Document and validate a bounded comma-separated GPU-device list. The executable
  quoting defect and basic empty/duplicate validation are fixed in this campaign;
  the remaining schema/topology work stays open below.
- Add optional recipe metadata for GPU count, tensor-parallel size, and operating
  mode, then fail closed when those declarations disagree with the selected devices
  or launch flags.
- Let an exclusive serve manifest reference a recipe directly instead of embedding
  a nested CLI command string.
- Preserve the existing single-device syntax and guarded ownership behavior.

## Acceptance

- Help and docs accurately describe single- and multi-device selection.
- Unit tests cover UUID pairs, index pairs, whitespace rejection, duplicates,
  topology-count mismatch, and TP-size mismatch.
- Exclusive-mode planning shows the resolved recipe, registry digest, both GPU
  owners, and the managed rollback command before apply.
