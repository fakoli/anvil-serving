# Huihui Qwen3.8 promotion addendum — 2026-09-19

**Status:** promoted. The exact baked candidate is running through the managed route.

The earlier [8K scout](2026-09-19-qwen38-huihui-ninfer.md) remains the historical `rejected`/`no-promotion` result for its original runtime. The [compatible-runtime follow-up](2026-09-19-qwen38-huihui-runtime-followup.md) remains the evidence basis for the later decision: the fixed runtime passed functional gates, the matched 8K MTP3/no-spec capacity cells completed 12/12, and the bounded 32K MTP3 cell completed 6/6. Its limits remain part of the record, including failed 128-word warmups, boundary normalization, the unpaired 32K comparison, and the higher post-workload GPU use than the cross-profile GGUF control.

The operator authorized promotion of the exact qualified candidate profile. The managed route is live, clean image recreation matched the required digest, and router preflight passed 6/6 through the alias. Workbench and Grafana historical identity and telemetry are live. Pi/OpenClaw convergence remains pending: the secondary is explicitly omitted to preserve the primary 65,536-token reserve.

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

The reviewed public follow-up bundle is staged privately for the existing historical benchmark importer. It registers four eligible capacity artifacts: no-spec 8K n=12, MTP3 8K n=12, GGUF-control 8K n=12, and MTP3 32K with 24K input n=6. Failed 128-word warmups are deliberately excluded from performance import while remaining linked in the follow-up finding.

The generated historical rows and Workbench cards remain separate from live readiness. Workbench publication is live.

The [identity receipt](2026-09-19-qwen38-huihui-promotion-evidence/identity-configuration-receipt.json) binds the tested recipe, runtime, model artifact, and individual results. The [media withdrawal receipt](2026-09-19-qwen38-huihui-promotion-evidence/media-withdrawal-receipt.json) records all workflows unavailable, no advertised A2A generation skills, and a rejected generation request while this profile owns the GPU.
