# Context-envelope publication summary

<!-- benchmark-publication-summary/v1 -->

**Status:** 160K/C1 is a qualified reference profile on the measured RTX 5090. Deployment state is private.

The direct-I/O profile passed preflight 8/8, vision 18/18, repeated bounded quality, depth retrieval 9/9 and unique-canary capacity (short 12/12; long 3/3). Maximum retained input was 150,144 tokens with an 8,192-token output allowance. Long-capacity GPU free floor was 4,598 MiB. Earlier 64K measurements remain a separate comparison.

| Claim | Evidence | Limit |
|---|---|---|
| Preflight 8/8 | [native result](preflight160-final.json) | Direct endpoint |
| Depth retrieval 9/9 | [native result](depths160-final.json) | Three fixed workloads; first cold/partial-cache, later warm |
| Long capacity 3/3 | [native result](capacity160-long.json) | Descriptive n=3; no endurance or matched speed claim |
| Native client compatibility | [summary](client-compatibility.json) | Bounded tool probes; macOS shell-marker limitation retained |

Short copy: The measured 160K/C1 profile passed bounded correctness and capacity checks with about 4.5 GiB GPU free. The output allowance is a request cap, not an observed generated-length result.

Alt text: A table of qualified context, correctness and capacity results with workload and sampling limits.
