# Publication summary: GLM-5.3-Flash native Linux versus Windows/WSL

<!-- benchmark-publication-summary/v1 -->

This file is derivative publishing copy. The linked dated finding and raw
artifacts are authoritative. No network posting was performed.

## Canonical facts

- **Model identity:** `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO@c3cbb9891b67c741bcbf6b176dd7af9265b069db`, served as `glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp`.
- **Runtime identity:** SGLang rc14 image `sha256:0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`; OCI revision `a547c90c74f1363920287eb80adc88a16d1e7005`; native package reports `0.0.0.dev1+g033446bb05`.
- **Local setup:** same physical 2× RTX PRO 6000 Blackwell Max-Q, TP2 over PCIe without NVLink; W4A16/NVFP4 weights, FP8 E4M3 KV, BF16 recurrent state, adaptive EAGLE [3,5], 393,216 configured context, C1.
- **Recipe:** [pinned Windows reference](https://github.com/fakoli/anvil-serving/blob/8abcc5dc75a1b2389210b553120abb76e5864d3a/configs/glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml) and [native differences](../2026-09-08-glm53-linux-wsl-comparison.md#exact-configuration-and-comparison-boundaries). Native adds `NCCL_P2P_DISABLE=1`; no native optimization was attempted.
- **Measurement path:** existing warm direct online service for capacity/endurance; shared prefixes, cache history not reset, thinking disabled, variable short outputs, 256-token completion ceiling. Routed protocol checks and an isolated macOS SWE worker are separate lanes.
- **Headline result:** native/WSL median decode 149.02/112.07 tok/s at 4K (+33.0%) and 120.29/99.79 at 380K (+20.5%); n=3 per cell/platform, 2,969/304,491 actual prompt tokens respectively. Long-context effective prefill remained within approximately ±2.3%. Endurance n=60 completed 60/60 at median decode 142.64/102.19 tok/s (+39.6%).
- **Capability result:** coding 15/15, image corpus 12/12, deep agentic 30/30; the identical `django__django-11099` official SWE smoke resolved 1/1. SWE agent stage nevertheless took 115.69 versus 34.22 seconds, with 22 versus 11 model requests.
- **Extended context:** default-thinking direct suite passed 128/150 through 376,484 actual prompt tokens; nominal 8K/32K/131K/262K/380K buckets passed 27/18/24/30/29 of 30. Failures: 9 empty length-terminated responses and 13 incorrect visible answers. Non-monotonic quality; native `effective_context=8192` is threshold-derived, not a physical cap. No matched Windows arm, so no Linux-caused regression claim.
- **Important caveat:** strict output passed 0/3 and unique natural canaries 1/10; both populations are excluded from performance claims. Routed 380K admission and reasoning-channel evidence fail despite corresponding direct passes. One direct image literal-phrase check fails; the separate image corpus passes. This compares the migrated stack with retained WSL, not OS-only causality, full SWE-bench, or strict finalist performance.
- **Decision:** `no-promotion`; functional/capacity/bounded-quality observations do not authorize any model, route, tuning or client change.
- **Canonical evidence:** [dated finding](https://fakoli.github.io/anvil-serving/findings/2026-09-08-glm53-linux-wsl-comparison/).
- **Artifact set:** [manifest](artifact-manifest.json) and [evidence index](README.md).

## X / short post

Preferred project limit: 260 literal characters including the URL. Hard limit:
280 characters. Recount immediately before posting.

```text
Local GLM-5.3-Flash, dual PRO 6000: Linux/WSL C1/n3 shared-prefix short decode +20.5–33%; context 128/150. Strict output failed; no promotion. https://fakoli.github.io/anvil-serving/findings/2026-09-08-glm53-linux-wsl-comparison/
```

## Reddit

Preferred title limit: 120 characters. Check the target community's current
rules before posting.

```text
Local GLM-5.3-Flash on dual PRO 6000: migrated Linux stack versus retained WSL, with strict-gate failures
```

```markdown
I compared the migrated native Linux stack with retained Windows/WSL results
on the same two RTX PRO 6000 Blackwell Max-Q cards. The exact GLM-5.3-Flash
W4A16/NVFP4 checkpoint and SGLang rc14 image match: TP2, FP8 KV, adaptive
EAGLE, 393,216 configured context and C1.

Warm direct online cells used three requests, shared prefixes and variable
short outputs, with a 256-token ceiling and thinking disabled. Median decode
rose from 112.07 to 149.02 tok/s at the 4K target and from 99.79 to 120.29 at
380K (304,491 actual prompt tokens). Across four targets, decode increased
20.5–33.0%; long effective prefill remained within approximately ±2.3%.
Endurance completed 60/60 at 142.64 versus 102.19 tok/s median decode.

Coding passed 15/15, the image corpus 12/12 and agentic 30/30. The identical
official SWE-bench smoke resolved 1/1, but its agent stage became slower:
115.69 versus 34.22 seconds, using 22 versus 11 model requests.

The new default-thinking direct context suite passed 128/150 through 376,484
actual prompt tokens, with 9 empty length-terminated responses and 13 incorrect
visible answers. Nominal 8K/32K/131K/262K/380K buckets passed 27/18/24/30/29
of 30. This non-monotonic curve has no matched Windows arm; its threshold-
derived 8,192 effective-context field is not a physical cap or an OS regression.

The important negative result: strict output passed 0/3 and unique natural
canaries only 1/10; those populations are excluded. Routed 380K admission and
reasoning-channel evidence failed despite direct passes, and a literal image
phrase check also failed. There is no strict finalist qualification or new
promotion. OS, driver/container stack, cache history and disabled NCCL P2P
differ, so this is a bounded whole-stack migration result, not OS-only causality
or a full SWE-bench score.

Full methodology, failures and raw artifacts:
https://fakoli.github.io/anvil-serving/findings/2026-09-08-glm53-linux-wsl-comparison/
```

## Screenshot alt text

GLM-5.3-Flash W4A16/NVFP4 on two RTX PRO 6000 Blackwell Max-Q cards,
SGLang rc14, TP2 and C1. Native Linux versus retained WSL warm direct decode
medians rise 20.5–33.0% over four nominal context targets from 4K to 380K,
with three requests per cell and variable short outputs sharing prefixes.
Endurance completed 60/60. Coding, image corpus, agentic and a one-instance
SWE smoke pass. Extended context passes 128/150 through 376,484 actual prompt
tokens, with 9 empty and 13 incorrect answers. Strict output and unique canary
gates fail; routed context
and reasoning evidence also fail. This is a whole-stack comparison with no
new promotion, not an OS-only causal result.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Same physical dual-card pair and exact model/image; native P2P disabled | TP2, 393,216 context, C1; host stack differs | [configuration and identity](configuration-and-identity.json); [configuration boundaries](../2026-09-08-glm53-linux-wsl-comparison.md#exact-configuration-and-comparison-boundaries) |
| Decode +20.5–33.0%; long prefill approximately ±2.3%; endurance 60/60 and +39.6% | Warm direct shared prefixes, variable short outputs; n3 capacity/n60 endurance per platform | [machine-derived comparison](historical-comparison.json); [historical-style methodology](../2026-09-08-glm53-linux-wsl-comparison.md#historical-style-performance) |
| Coding 15/15, images 12/12, agentic 30/30 | Bounded deterministic suites, separate from timed cells | [functional and quality results](../2026-09-08-glm53-linux-wsl-comparison.md#functional-quality-and-software-task-results); [agentic artifact](agentic/artifact.json) |
| SWE smoke 1/1 but slower agent stage and more requests | Same official instance; different trajectories and worker overhead | [SWE comparison](swe-comparison.json); [native SWE artifact](swe/artifact.json) |
| Strict output 0/3 and unique canaries 1/10 excluded; routed and image failures retained | No validator relaxation or failed-population speed claim | [failures and routed limits](../2026-09-08-glm53-linux-wsl-comparison.md#failures-and-routed-limits); [image phrase failure](../2026-09-08-glm53-linux-wsl-comparison.md#functional-quality-and-software-task-results) |
| Extended context 128/150; 9 empty and 13 incorrect, non-monotonic curve | Default thinking, five nominal buckets; no matched Windows arm; effective-context field is not a physical cap | [context summary](context-summary.json); [native context artifact](context/artifact.json) |
| Whole-stack comparison; no strict finalist qualification or promotion | Historical baseline/output/cache limits and no change authority | [result card](../2026-09-08-glm53-linux-wsl-comparison.md#result-card); [comparison boundaries](../2026-09-08-glm53-linux-wsl-comparison.md#exact-configuration-and-comparison-boundaries) |
