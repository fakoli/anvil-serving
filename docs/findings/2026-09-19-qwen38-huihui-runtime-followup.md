# Huihui Qwen3.8 NInfer compatible-runtime follow-up

**Date:** 2026-09-19

**Status:** corrected runtime recovery and managed restoration are verified. No promotion, route, or live-client change.

<!-- benchmark-result-card/v1 -->
## Result card

| Setup | Result |
|---|---|
| Candidate | Huihui NVFP4 artifact `18144690`, NInfer `70434721`, direct RTX 5090 8K/C1 |
| Isolated MTP comparison | strict 32-word seed-43 n12: no-spec/MTP3 71.4/176.2 mean decode tok/s; 908/524 mean E2E ms |
| Cross-profile comparison | workload-matched GGUF 107.7 tok/s and 2,572 ms mean E2E; distinct checkpoint, engine/runtime, and quantization |
| Footprint | MTP3 21,852 MiB versus GGUF 19,022 MiB post-workload including desktop/driver; requirement missed |
| Decision | working candidate retained, `no-promotion`; finalist, endurance, application, deployment, and live-client gates remain open |

**Important caveat:** the MTP gain is isolated only against NInfer no-spec. The GGUF comparison is complete-profile evidence, not a causal speculation result.

Evidence: [bundle README](2026-09-19-qwen38-huihui-runtime-followup-evidence/README.md) Â· [artifact manifest](2026-09-19-qwen38-huihui-runtime-followup-evidence/artifact-manifest.json) Â· [configuration](2026-09-19-qwen38-huihui-runtime-followup-evidence/configuration.json) Â· [pinned recipe](2026-09-19-qwen38-huihui-runtime-followup-evidence/recipe-schemafix-cold-build.toml)

The original pinned runtime exposed malformed tool arguments and boundary coercion/line-ending failures. This follow-up preserves that failure while adding fresh bounded proof from a compatible repaired runtime.

The original artifact is version 2 and pins NInfer `a99407c63fc5bbd25d9fb597cbb8ab352bdb01ef`. Current upstream head is version-3-only and incompatible without conversion. The compatible investigation target is `70434721b1ae29d0616f3de9b376c8a4d91590b5`, following schema-typing commit `0e4cdf84f04d74abf6e28b6021b2bd83239d5a1b`; independent review found that it retains version-2 support and fixes numeric-shaped string handling.

The compatible `70434721` runtime passes smoke 2/2, default core quality 9/9, low-effort core quality 9/9, and C1 protocol 5/5. A 20-request tool burst completed 17/20; the remaining three requests received HTTP 429 and do not establish higher-concurrency admission. Vision passed 12/12 with the schema-fix runtime. Its MTP3 8K lane passes core quality 9/9, preflight 7/7, and vision 12/12.

The matched seed-43 strict 32-word experiment passed 12/12 in both NInfer arms: no-spec mean decode 71.4 tok/s versus MTP3 176.2 tok/s, with mean E2E 908 versus 524 ms. The fresh GGUF 8K control passed preflight 4/4 and core 9/9. Its matched short-output cell was 12/12 with 107.7 tok/s mean decode and 2.57-second mean E2E, versus Huihui MTP3 176.2 tok/s and 0.524-second mean E2E. This is an explicitly cross-profile observation: checkpoint, runtime, and quantization differ, so only the NInfer no-spec/MTP3 pair isolates speculation. The [derived chart](2026-09-19-qwen38-huihui-runtime-followup-evidence/benchmark-matrix.svg) and [graph data](2026-09-19-qwen38-huihui-runtime-followup-evidence/benchmark-graph-data.json) separate these panels. The no-spec off and low, MTP3, and GGUF 128-word warmups each failed 0/2; no throughput claim is made from any warmup.

At 8K, post-workload GPU use including desktop/driver was GGUF 19,022 MiB, NInfer no-spec 21,390 MiB, and Huihui MTP3 21,852 MiB; MTP3 at 32K was 23,106 MiB. These are not peak/RSS measures. Huihui MTP3 uses about 14.9% more GPU memory than GGUF at 8K, so the user footprint requirement is not met. MTP3 32K has preflight 7/7 including a nominal 24K needle, core quality 9/9, session 3/3, a 23,252-token context summary, and strict seed-44 24K-input capacity 6/6 at 3.59-second mean E2E. Without a no-spec 32K pair, it does not establish whether the 8K gain extends.

Managed restoration is verified: `media-worker` and `media-mcp` are running and HTTP 200, split-mode media compute remains owned by `media-worker`, and `qwen-local` remains absent. GPU use returned to the 686 MiB follow-up baseline; temporary containers are absent; the original stopped/exited/unhealthy incumbent is unchanged; and all three operator manifest hashes match their before/after values. The existing [8K scout](2026-09-19-qwen38-huihui-ninfer.md) remains historical original-runtime evidence; this finding supersedes only its runtime-failure interpretation.

## Retained starting evidence

The original-runtime tool artifact records raw `{"zip":98101}` for all three failed attempts. The compatible runtime records string `"98101"`. The original no-thinking boundary artifact passed 9/21. Compatible boundary results were 15/21 off, 12/21 low, and 18/21 under generic tool instructions. The generic-instruction probe, including its GGUF control, retained only CRLF-to-LF normalization failures. The lower off/low scores remain separate retained failures. The evidence distinguishes a recovered argument-contract behavior from remaining line-ending behavior.

## Source boundary

The [research notes](2026-09-19-qwen38-huihui-runtime-followup-evidence/research-notes.md) and [source registry](2026-09-19-qwen38-huihui-runtime-followup-evidence/source-registry.json) classify upstream material as advisory external priors. They guide a local test only and cannot support promotion. The local raw artifacts ground the recovery observations. Full finalist, endurance, application, deployment, and live-client acceptance remain unqualified.
