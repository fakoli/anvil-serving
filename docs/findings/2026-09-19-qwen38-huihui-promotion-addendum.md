# Huihui Qwen3.8 promotion addendum — 2026-09-19

**Status:** promoted. The exact baked candidate is running through the managed route.

The earlier [8K scout](2026-09-19-qwen38-huihui-ninfer.md) remains the historical `rejected`/`no-promotion` result for its original runtime. The [compatible-runtime follow-up](2026-09-19-qwen38-huihui-runtime-followup.md) remains the evidence basis for the later decision: the fixed runtime passed functional gates, the matched 8K MTP3/no-spec capacity cells completed 12/12, and the bounded 32K MTP3 cell completed 6/6. Its limits remain part of the record, including failed 128-word warmups, boundary normalization, the unpaired 32K comparison, and the higher post-workload GPU use than the cross-profile GGUF control.

The operator authorized promotion of the exact qualified candidate profile. The managed route is live, clean image recreation matched the required digest, and router preflight passed 6/6 through the alias. Workbench and Grafana historical identity and telemetry are live. Pi/OpenClaw catalogs explicitly omit the secondary to preserve the primary 65,536-token reserve; Hermes and Open WebUI retain the current 64K/2,048-token contract described below. Three fleet hosts and the separate Windows Pi installation reached an unchanged repeat catalog preview. Open WebUI verified both active users and a fresh successful scheduled receipt with no repeat changes.

## Fresh baked-image gate

The exact baked MTP3 32K image passed a fresh quality gate at the nominal 31K
target. Tool calling, unified-diff editing, timeout triage, and session recall
each passed 3/3. The tool argument was the string `"98101"`. The context
request used 29,503 actual prompt tokens (29,543 total including 40 completion
tokens); the configured cap was 31,488 tokens. This is one context timing
sample, not a capacity result. Fresh vision passed 12/12 and baked capacity
passed 18/18. The sanitized [gate summary](2026-09-19-qwen38-huihui-promotion-evidence/quality-baked-summary.json)
does not contain endpoint or response content.

## Historical benchmark publication staging

The historical benchmark importer retains six eligible capacity artifacts: no-spec 8K n=12, MTP3 8K n=12, GGUF-control 8K n=12, MTP3 32K with 24K input n=6, and final baked-image short-input n=12 and long-input n=6 cells. Failed 128-word warmups are deliberately excluded from performance import while remaining linked in the follow-up finding. The live metrics store reports 234 historical result rows, including the two 64K cells across the preserved inventory and readiness 1 for the promoted secondary.

The generated historical rows and Workbench cards remain separate from live readiness. Workbench publication is live.

The [identity receipt](2026-09-19-qwen38-huihui-promotion-evidence/identity-configuration-receipt.json) binds the tested recipe, runtime, model artifact, and individual results. The [media withdrawal receipt](2026-09-19-qwen38-huihui-promotion-evidence/media-withdrawal-receipt.json) records all workflows unavailable, no advertised A2A generation skills, and a rejected generation request while this profile owns the GPU.

## Client acceptance and remaining limits

Real Hermes, Pi, and OpenClaw tool-result continuation checks passed through the unchanged primary alias after reconciliation. The OpenClaw fixture initially forced reasoning off and failed its exact-answer check; its retry using the installed high-reasoning default passed all eleven checks. Both outcomes remain in private receipts. These are integration checks of the primary client path, not additional quality scores for the Qwen secondary. Qwen's independent tool, image, context, and routed preflight gates are recorded above.

The authenticated browser session has not yet been verified interactively. Native WebUI API access/catalog checks and scheduled reconciliation passed; this does not claim browser acceptance. The exact OCI image remains in the managed local store. Pinned build inputs are retained, but a fresh-host rebuild is not claimed to produce an identical digest and must be qualified before substitution.

## 64K C1 extension

The promoted Huihui NInfer MTP3 profile now uses 65,536 context tokens at C1. Direct preflight passed 7/7, routed preflight 6/6, vision 12/12, and native Hermes 9/9 with the exact local alias. Descriptive, canary-free C1 short-input capacity passed 12/12 at 182.1 mean decode tokens/s; the long-input cell passed 6/6 at 167.1, with 60,769-60,776 actual prompt tokens. Post-workload GPU use was 24,224 MiB. The three fleet hosts and Open WebUI converged with zero repeat changes. The 32K results remain historical evidence and a rollback profile. Endurance, interactive browser acceptance, and a matched 64K no-spec control remain unmeasured. See [the extension finding](2026-09-19-qwen38-huihui-64k.md).
