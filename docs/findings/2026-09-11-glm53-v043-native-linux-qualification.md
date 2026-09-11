# GLM-5.3-Flash v0.4.3 native-Linux qualification

**Date:** 2026-09-11 UTC. **Campaign decision:** qualified; the dated human-authorized promotion gate passed. Current route and startup assignments are private operator state. The exact ormandj weights remain unchanged; this is a runtime and capacity upgrade.

| Field | Measured result |
|---|---|
| Hardware | 2x RTX PRO 6000 Blackwell Max-Q 96 GB, native Linux, exclusive TP2 |
| Runtime | v0.4.3 image `ec4243f940a179a27fea21895077efd47cd050501f99a1d2a5fecf7df2e7be71` |
| Model | ormandj W4A16/NVFP4, revision `c3cbb9891b67c741bcbf6b176dd7af9265b069db` |
| Capacity | 524,288 shared/context tokens; C4; 28 recurrent slots |
| Controls | FP8 E4M3 KV, BF16 recurrent state, adaptive EAGLE [3,5], prefill 4096, HiCache off |
| Functional | Thinking off/on smoke and JSON each 3/3; tools 20/20; streaming, continuation, Responses, image/OCR pass |
| Long context | 12/12 retrieval cells, deepest actual prompt 497,724 tokens; long tool pass at 126,727 |
| Reused context | Ten incremental turns through 69,736 tokens, retained anchor and subsequent tool result correct; five post-stress smokes pass |
| Concurrency | Corrected C4 probes 4/4 short and 4/4 at about 106.5K tokens per request |
| Pi | 14/14 executable tasks; zero human interventions, truncations, or protocol errors |
| Routed acceptance | Exact routed identity verified; fresh Pi, Hermes and OpenClaw terminal turns pass |
| Stability | No observed OOM or automatic restart; minimum sampled GPU free memory 567 MiB per card |

## What changed and what did not

The old rc14 baseline had a 393,216-token pool and C1. The qualified v0.4.3 profile provides a 524,288-token **shared** pool and four-request scheduling. C4 does not mean four simultaneous full 524K windows. The quant, model revision, KV precision and adaptive speculation remain fixed. HiCache is disabled because its upstream allocation is unsuitable for this host's RAM budget.

The [separate completion-budget campaign](2026-09-11-glm53-output-budget.md) did not establish a repeatable benefit from raising the default. The recommended default remains **4,096 output tokens**. Qualification explicitly exercised 8,192 and 16,384 in isolated Pi lanes; those allowances are not the recommended default.

The original managed qualification (including the 14-task Pi panel and 12-cell
retrieval sweep) used a private 64 GiB IPC namespace. A final packaging audit
aligned the tested profile to the requested **host IPC** mode without changing model,
runtime or GPU-serving controls. The affected direct, routed, long-context,
C4 and fresh-client gates are retained separately as `host-ipc-*` evidence.
The final host-IPC run passed C4 long requests 4/4, deep retrieval at 497,729
prompt tokens, thinking off/on, routed preflight and all three fresh client turns.
Host IPC uses the host's approximately 45 GiB `/dev/shm`; the authored Docker
`shm_size=64g` does not enlarge that host mount. No host mount was resized.

## Correctness and Pi evidence

[Native artifacts](2026-09-11-glm53-v043-native-linux-qualification-evidence/README.md) retain direct preflight, exact token observations, original failures, corrected runs, frozen Pi tasks, and executable-validator checks. Retrieval used unique request prefixes and two needle positions at each of six context targets. Incremental testing retained growing conversation prefixes and changed the requested answer each turn. This is bounded correctness evidence, not exhaustive endurance coverage.

The 14 Pi tasks cover three straightforward edits, three debugging tasks, three multi-file changes, three tool-heavy loops, and two repository-context tasks with about 44K input tokens. All buggy fixtures failed their checkers before execution; independently authored reference fixes passed them. Source and checker hashes remained intact. The panel took 66.68 seconds in aggregate under its recorded cache conditions. There is no matched incumbent latency panel, so this is **not a speedup claim**. Fourteen successes and zero interventions reach the panel's success ceiling and intervention floor; they do not manufacture a measured baseline comparison.

GLM exposes a binary `enable_thinking` template control, not verified numeric low/high effort. The qualification's ordinary lane explicitly mapped to thinking off plus 8K output; its hard lane mapped to thinking on plus 16K. Upstream request traces verified those controls. Generic client reasoning-level labels must not be interpreted as native GLM effort levels.

## Failures retained and fixes

- Stock v0.4.3 always opened `<think>`, including when `enable_thinking=false`; five disabled JSON attempts failed. A new hash-gated template suffix respects that flag. Default/enabled rendering remains byte-identical; all three disabled and enabled repeats then passed. No old rc14 logits patch was carried forward.
- The original C4 canary probe failed 0/4 because the prompt allowed a colon after the marker while its strict grader required whitespace. The prompt now explicitly requires one space. The grader was unchanged; original failures and diagnostic outputs remain. Corrected short and long C4 cells passed. Their timing is not compared against the invalid original cell.
- Promotion initially deadlocked its own authority lock by spawning a router-transition child process. The shared transition now uses the same CLI handler in the lock-owning thread, retaining refusal semantics.
- The legacy config installer wrote an unused named volume while this deployment used a file bind. It now resolves the deployed configuration mount, preserves file metadata during install/rollback, and refuses unsupported layouts. The initial false-success result is retained; only the later exact-identity/readmitted result counts as promotion evidence.
- TileLang startup data-race warnings remain an upstream diagnostic limitation. They were not suppressed or declared harmless. Supported qualification workloads completed without an observed crash or OOM. Headroom is narrow and the existing model-only reserve approval remains the governing boundary.
- OpenClaw's scoped terminal smoke succeeded, while its unrelated controller MCP integration reported a missing credential in that test process. That MCP integration is not claimed validated here.

## Promotion and rollback

The stock attempt was followed by restoration of the exact rc14 recipe, direct acceptance and later routed/real-Pi baseline checks. That is retained rollback evidence. The campaign concluded with a human-authorized promotion gate rather than another restoration; this is dated acceptance evidence, not a live deployment declaration. The managed reload on the evaluated endpoint passed thinking-off/on gates, tools and retrieval, then the router verified the exact served model and readmitted it.

The evaluated model-free client node’s retained Hermes profiles, Pi and OpenClaw received 524K context metadata with the 4K output cap; a second sync was a no-op. Cloud providers and secondary-model routing remain separate. Current startup and route selections are recorded only in private operator configuration. A reboot and a fresh-machine rebuild were not tested.

The [reproducible managed recipe](2026-09-11-glm53-v043-native-linux-qualification-evidence/recipe.toml) retains the exact as-run pins, template guard and historical pre-qualification metadata. The catalog recipe now reports `verified`; that metadata-only update does not rewrite the recorded recipe digest.

See the [identity record](2026-09-11-glm53-v043-native-linux-qualification-evidence/identity.json), [decision summary](2026-09-11-glm53-v043-native-linux-qualification-evidence/summary.json), and [artifact inventory](2026-09-11-glm53-v043-native-linux-qualification-evidence/artifact-manifest.json). The campaign verified recovery to the exact rc14 recipe. No other model tournament was started.
