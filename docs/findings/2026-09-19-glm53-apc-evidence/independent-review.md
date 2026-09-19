# Independent APC promotion review — 2026-09-19

**Recommendation:** `promote` r10 APC within the tested configuration, subject to the separate human promotion gate. `human_gate_required=true`; `promoted=false`.

**Independence:** campaign lead/evidence author is reported as GPT-6 Astra; the evaluated endpoint is GLM-5.3-Flash r9 versus r10 APC; reviewer is GPT-5.6 Sol. Neither author nor evaluated model is Sol.

## Evidence verdict

The frozen 32-request C4 cells are sufficient to stop capacity testing. Both arms completed 32/32 with strict output adherence and request canaries. On the shared 32K cell, r10 reduced mean visible TTFT 67.84%, reduced time-to-first-output 73.15%, and increased requests/second 216.55%. Isolated engine counters recorded a 75.58% prefix-cache hit fraction, providing causal APC evidence. On the unique 32K cell, requests/second regressed 2.39%, inside the frozen 5% limit; mean TTFT rose 4.02%, time-to-first-output 6.22%, and E2E 2.73% while completion volume rose 5.42%. This is an acceptable bounded cost under the stated throughput gate, not evidence of a universal latency improvement. Retain the negative eight-request scout as variance/cold-path evidence.

Candidate correctness evidence is broad enough for this runtime-only claim: direct preflight passed; candidate and restored-control quality checks each passed 12/12 across three repetitions with typed control evidence from the actual pinned template rendering native Max effort; candidate image/OCR and both arms' eight-image tests passed; 309,422-token needle and 110,760-token tool-call checks passed; C4 and the 327,680 configured limit were retained; and status evidence shows no OOM or swap event under the asserted 1 MiB candidate bound. This does **not** establish broad intelligence parity or exhaustive full-context C4 behavior.

## Gate and evidence status

Quality, functional, performance, containment, and restoration gates are closed. `restoration.json` verifies the exact r9 recipe/image/checkpoint, direct and authenticated routed preflight 6/6, route readmission, unchanged router configuration, and absence of the candidate container/listener. The corrected systemd receipt shows the wrapper remained `active (exited)` from September 13 and was not activated by this campaign. The candidate unique finalist lacks an embedded configuration fingerprint, but `recipe-bindings.json` binds its retained artifact hash and running-identity receipt to the exact normalized r10 recipe hash; this is adequate for this campaign while remaining a native-artifact limitation.

Recommend **human-approved bounded promotion of r10 APC** with the existing model, checkpoint, image, precision, 327K configuration, C4, vision-eight, and no-speculation settings unchanged. Keep `promoted=false` until that human-gated operation succeeds. Roll back on unique-throughput regression above 5%, correctness failure, OOM/containment failure, or loss of direct or routed acceptance. No further pre-promotion performance, vision, context, or quality tests are needed.

## Publication review

The final public finding and companion evidence bundle are accepted for publication finalization. Their performance, quality, limitation, restoration, and human-gate claims match the private evidence. Direct and routed endpoints are synthetic; the reviewed public files contain no operator path, host identity, GPU UUID, container ID, or private network identity. Finalization must copy this corrected review and regenerate the artifact hashes afterward.
