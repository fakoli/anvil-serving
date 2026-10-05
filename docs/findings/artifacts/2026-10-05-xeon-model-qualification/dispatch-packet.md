# Campaign dispatch packet

- **Campaign ID:** `2026-10-05-xeon-model-qualification`
- **Task ID:** `candidate-load-feasibility`
- **Stage and gate:** feasibility; prove the pinned candidate starts as the requested checkpoint before functional or performance work.
- **Objective:** retain native startup evidence for `local-inference-lab/Qwen3.8-Flash-Next-NVFP4@6909a5bed089a48fa07e956d3915af2537de9368` under the pinned current runtime.
- **Owned outputs:** new native candidate startup/feasibility artifacts added by the campaign owner; this packet does not authorize route or promotion changes.
- **Authoritative inputs:** [source-registry.json](source-registry.json), [feasibility-initial.json](feasibility-initial.json), and the current artifact ledger.
- **Authority:** managed qualification only. Service, route, cache, network, and promotion mutations outside the declared candidate lifecycle are forbidden.
- **Stop conditions:** stop the cell on wrong revision, unsupported quantization, startup failure, resource failure, or missing startup identity; retain the earliest actionable evidence.
- **Verification:** native startup artifact confirms runtime digest, model revision, TP2/no-spec settings, mixed ModelOpt layout, and resolved context/KV capacity.
- **Return contract:** status, bounded facts, retained evidence paths, failure and fix-forward hypothesis, and one independent artifact check.
