# Publication summary

The pinned SGLang `v0.1.1-rc.14` GLM-5.3-Flash W4A16 recipe is locally
qualified on two RTX PRO 6000 Blackwell Max-Q cards under WSL2 after four
durable, hash-gated compatibility fixes. The selected envelope is TP=2,
adaptive EAGLE MTP `[3,5]`, one running request, and 245,760 configured tokens.

It passed all functional checks, thinking enabled/disabled control, 15/15
deterministic coding-agent attempts, 12/12 image/OCR attempts, and 60/60
endurance requests. At nominal 4K/120K/230K, median decode was
108.57/93.35/95.00 tok/s and median effective prefill was
16,545/5,763/5,608 tok/s. The deepest point measured 189,627 prompt tokens.
After the complete workload, both cards retained 3,487 MiB free, clearing the
3,072 MiB reserve gate.

The larger 499,712 and 393,216 envelopes are not recommended: the former left
almost no idle reserve and the latter fell to 2,101 MiB per card after a full
workload. The 499,712/C4 short run reached 147.54 aggregate output tok/s, but
its long C4 run degraded to 148.86 seconds median TTFT and 0.61 aggregate
output tok/s and the profile failed reserve policy.

The result is a verified local challenger, not a promotion. The exact starting
incumbent container, image, model, 524,288-token context, loopback binding,
exclusive ownership, router readiness, and empty shared-memory state were
restored.
