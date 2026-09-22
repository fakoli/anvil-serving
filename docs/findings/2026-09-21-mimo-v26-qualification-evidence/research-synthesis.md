# Candidate research synthesis

Observed 2026-09-21 Pacific / 2026-09-22 UTC. Three independent research passes selected SGLang v0.5.20 with tensor parallelism two, the pinned official mixed-MXFP4 MiMo artifact, and no speculation for the first load attempt. No hardware-matched published MiMo v2.6 benchmark was found; all sources below are external priors, not local qualification evidence.

The selected SGLang release commit is `94602c9c2b7cbdb8efd5c52802dac6a1c180089e`. The pinned MiMo revision is `3b38d063180c3e4aed9691fdc735f3d10b266ee4`. The release is a load candidate only. A vLLM nightly path after 2026-09-20 remains a supported successor if a retained observed failure warrants configuration search; it is not evidence for the initial configuration.

The upstream deployment examples, checkpoint parameter counts, and tensor-parallel divisibility do not prove runtime fit or performance on the measured two-GPU lane. Candidate results remain unmeasured.

Sources are recorded in [source-registry.json](source-registry.json).
