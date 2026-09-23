# 2026-09-22 model shortlist validation evidence

This public-safe bundle records a bounded replacement screen and the initial
Qwen3.8 Flash Next TP2/C4 trial. It preserves native schemas after replacing
operator paths, host/container identifiers, network values, GPU UUIDs, and a
synthetic fixture secret with stable redaction tokens. All retained
`container_id` values, including nested status payloads, use
`REDACTED_CONTAINER_ID`. GPU-A-REDACTED and GPU-B-REDACTED preserve
distinct-card references, and public relative configuration names preserve the
four restoration hash-parity entries. No credential material is retained.

The two `router-final-status.json` snapshots retain their envelope shape but
replace active tier-to-model assignments and readiness state with
`REDACTED_ACTIVE_ROUTER_STATE`. The Qwen startup capture replaces its historical
loopback bind literal with `<redacted-loopback-bind>`; it does not assert a
different bind argument. Independent direct and routed probes retain the
bounded pass/fail outcomes used by the campaign decision.

The Qwen native agentic artifacts report 16/18 and 15/18 with their internal
0.75 pass-rate floor and `passed: true`. The campaign's predeclared
deterministic gate is 100%, so neither run advanced the candidate. The two
debug-loop failures were strict protocol failures. They are not evidence of
general coding inferiority or executed-code behavior.

The paired GLM scout also reports 16/18. Its two planning false negatives are
a scorer defect: an earlier incidental substring is selected before the
required next heading. The raw tie is retained but cannot rank broad
planning/coding quality.

The Qwen six serial C1 direct preflight groups passed, startup used 65.55 GiB
per rank, and the allocated shared KV pool reported 928,576 tokens. These
facts do not qualify throughput, quality, vision, large context, or capacity:
those stages were deferred after the strict agentic gate failed. Startup also
retained a nonfatal custom-allreduce UUID-to-index parse warning and fallback;
it did not prevent readiness or direct preflight.

The paired GLM agentic scout and final restoration are retained. Restoration
returned the exact baseline identity/configuration, passed direct and routed
smoke/JSON checks, and readmitted the router. This completes the bounded
campaign but cannot authorize a promotion.

| Area | Files |
|---|---|
| Run plan and source screen | [run plan](run-plan.md), [source registry](source-registry.json) |
| Qwen identity and startup | [settings](qwen-effective-settings.json), [startup](qwen-startup-complete.txt), [ready status](qwen-ready-status.json) |
| Direct functional checks | [basic](qwen-preflight-basic.json), [tools](qwen-preflight-tools.json) |
| Agentic gate | [greedy scout](qwen-agentic-scout-native.json), [native-sampling scout](qwen-agentic-native-sampling-native.json) |
| Campaign controls | [coverage and gaps](coverage-and-gaps.md), [friction log](friction-log.md), [summary](summary.json) |

## Final privacy review

Retained loopback URLs and startup listener ports use `<redacted-port>`.
`mode-restored.json` preserves the recorded mode, TP size and unresolved list
but replaces the private blocked-workload catalog, exclusive owner and GPU
role/owner mappings with `REDACTED_ACTIVE_TOPOLOGY`. Public restoration
claims use the retained configuration hash comparisons and independent probes;
redacted status fields do not prove private topology or route assignments.
