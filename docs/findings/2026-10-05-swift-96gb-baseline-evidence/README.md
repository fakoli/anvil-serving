# Swift15 96 GB host-cache promotion evidence

This closed model campaign promotes the same-image 8 GiB/eight-slot cache bundle after bounded reuse, regression and client acceptance. The tested Flash candidate was rejected after its latency gate and diagnostics. Every artifact is under 2 MiB. Package installation and native fleet-owner reconciliation are separate.

- [Decision summary](summary.json), [identity](configuration-identity.json),
  [executing source](launcher-identity.json), [source registry](source-registry.json).
- [Workload hashes](workload-manifest.json), [run controls](run-plan.json).
- Preflight [one](swift-preflight-001.json), [two](swift-preflight-002.json),
  [three](swift-preflight-003.json): all five families passed each repetition.
- Agentic [native evidence](swift-agentic-evidence.json) and
  [18 case observations](swift-agentic-cases.json).
- Context 128K [native evidence](swift-context128-evidence.json) and
  [27 cases](swift-context128-cases.json); context 262K
  [native evidence](swift-context262-evidence.json) and
  [nine cases](swift-context262-cases.json).
- [Image corpus and 12 attempts](swift-images-001.json).
- SWE [native evidence](swift-swe-evidence.json), [official results](swift-swe-result.json),
  image identities [after task stage](swe-image-identities-initial.json) and
  [after grading](swe-image-identities-final.json). These observations are not
  pre-task immutable image enforcement.
- [Failed strict capacity](swift-capacity-8k-c1.json),
  [stream/nonstream diagnostic counts and hashes](swift-stream-diagnostic-001.json).
- [Versioned service workload](workload-service-latency-v2.json), native
  [8K/C1](swift-service-v2-8k-c1.json), [8K/C4](swift-service-v2-8k-c4.json),
  [24K/C1](swift-service-v2-24k-c1.json), [24K/C4](swift-service-v2-24k-c4.json),
  and [derived medians](service-latency-summary.json): 12/12 each.
- [SMBIOS memory observation](physical-memory-modules.json); configured clock is not bandwidth.
- [Friction and lessons](friction-log.md), [restoration boundary](restoration.json).
- [Redaction and original-source hashes](redaction-provenance.json),
  [artifact manifest](artifact-manifest.json).

Native schemas and decision values remain intact. Prompt, response and reasoning
text, tool arguments, logs, commands and operator identities are withheld.
The provenance ledger binds each sanitized file to its private original and
records field redactions. It also identifies native evidence extracted from a
worker artifact's `results/evidence` object. Redacted text cannot be used to
rerun the scorers; retained case judgments and hashes remain auditable against
the private originals. Public fixture/source hashes identify the harness.

The failed strict capacity population remains ineligible. The separate v2
populations measure variable-output service latency, not fixed-output decode
speed. Functional timings overlapped build/download activity and are not ranked.

[Service latency chart](benchmark-matrix.svg) · [Chart data](benchmark-graph-data.json) · [Chart inputs](graph-manifest.json).

## Follow-up evidence at this checkpoint

- Distinct immutable-image SWE replay: [native result](swift-swe-paired-result.json),
  [native evidence](swift-swe-paired-evidence.json); 5 graded, 4 resolved.
- Flash initial: [preflight](flash-m50-preflight-001.json),
  [images](flash-m50-images-001.json), [12-request service cell](flash-m50-32k-service-v2-8k-c1.json),
  [failed latency comparison](flash-m50-32k-initial-comparison.json).
- Instrumented MMQ: [preflight](flash-diagnostic-mmq-preflight.json),
  [three-request diagnostic](flash-diagnostic-mmq-8k-c1.json),
  [phase observations](diagnostic-mmq-phases.json), [interpretation](strata-phase-interpretation.json).
- [Follow-up source, image, recipe and memory identity](follow-up-identity.json).

Instrumented observations are not pooled with the official service gate.
Flash is rejected. Final Swift selection, restoration and client acceptance are recorded in promotion-final.json and restoration.json.

Fused follow-up: [preflight](flash-diagnostic-fused-preflight.json), [native three-request result](flash-diagnostic-fused-8k-c1.json), [activation](fused-activation-observation.json), [diagnostic comparison](prefill-diagnostic-comparison.json). No useful latency benefit observed; no new official gate.

Managed inspection recognizes the extracted paired SWE native evidence and completed 5/5 grading, but reports its missing native promotion-boundary field. The source is preserved; that historical SWE artifact alone supplies no promotion boundary. The separate promotion-final.json records the later campaign decision; no full qualification is inferred from the SWE artifact alone.

Trace follow-up: [failed preflight](flash-trace-preflight.json), [disposition](trace-preflight-disposition.json), [three diagnostic requests](flash-trace-8k-c1.json), [refill attribution](trace-refill-analysis.json). Unresolved disconnect and asynchronous memory-pressure counters prevent a qualification claim.

Final Flash trial: [fixed-4096 preflight](flash-prefill4096-preflight.json), [three diagnostic requests](flash-prefill4096-8k-c1.json), [paired analysis and no-promotion decision](prefill4096-analysis.json). Larger-context, C4 and finalist quality gates were not run after scout failure.

## Same-image Swift host-cache pilot

[Plan](swift-host-cache-experiment-plan.json), [paired comparison](swift-host-cache-comparison.json), [control analysis](swift-cache-control-analysis.json), [expanded analysis](swift-cache-expanded-analysis.json). Both arms passed 18/18; benefit population is 12 matched revisits each. Retention and average-middle-pair median TTFT/E2E improved; nearest-rank p50 is also retained because the populations are bimodal. Fresh service, agentic, long-context, image and explicitly cache-aware continuation gates passed; final selection and client acceptance subsequently passed.

- Control: [preflight](swift-cache-control-preflight.json), [cold](swift-cache-control-cold.json), [first revisit](swift-cache-control-revisit1.json), [second revisit](swift-cache-control-revisit2.json).
- Expanded: [preflight](swift-cache-expanded-preflight.json), [cold](swift-cache-expanded-cold.json), [first revisit](swift-cache-expanded-revisit1.json), [second revisit](swift-cache-expanded-revisit2.json).

## Expanded-cache regression checkpoint

The [48-request service regression](swift-cache-expanded-service-comparison.json) recomputes average-middle-pair medians from both raw populations; all cells passed the declared 1.10 ratio limit. Historical native p50 values remain unchanged. Expanded [agentic cases](swift-cache-expanded-agentic-cases.json), [262K context cases](swift-cache-expanded-context262-cases.json) and [image attempts](swift-cache-expanded-images.json) passed 18/18, 9/9 and 12/12. The supplemental explicit-history gate also passed; final endpoint and client acceptance subsequently passed.

## Explicit cached continuation

[Final outcome](swift-explicit-history-outcome.json), [prospective plan](swift-explicit-history-plan.json), [warm receipt](swift-explicit-history-warm.json), [pressure population](swift-explicit-history-pressure.json), and [four changed branches](swift-explicit-history-changed.json) bind the explicit cache-aware request shape. The two earlier zero-hit attempts remain separate: [changed suffix](swift-cache-continuation-outcome.json), [unmarked appended branches](swift-appended-history-changed.json), and [independently derived native observations](swift-appended-history-native-analysis.json). [Pinned-source admission diagnosis](swift-cache-admission-diagnosis.json) distinguishes observed facts from an inferred planner explanation. Full history/reasoning state and raw container captures remain private; native source hashes, recorded gates and case results remain public.

## Final promotion acceptance

- [swift-final-startup-identity-comparison.json](swift-final-startup-identity-comparison.json)
- [swift-final-full-protocol-preflight.json](swift-final-full-protocol-preflight.json)
- [swift-final-pi-smoke.json](swift-final-pi-smoke.json)
- [swift-final-routed-clients.json](swift-final-routed-clients.json)
- [promotion-final.json](promotion-final.json)
- [swift-final-acceptance-status.json](swift-final-acceptance-status.json)

The selected Swift cache bundle is promoted. Private raw native logs and rollback files remain private; sanitized acceptance and source hashes are retained above.
