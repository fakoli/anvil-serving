# GLM runtime investigation — 2026-09-23

## Scope and authority
User authorized skill/tool improvement, model stress and configuration investigation, and repeatable recipe documentation. No production promotion or host/driver changes. Existing baseline remains rollback. No changes to cloud providers or client catalogs. Live request windows use current idle observation; stop on unrelated traffic or faults. All raw logs remain private outside Git.

## Ordered experiments
1. Validate skill with independent behavioral review and runner with synthetic HTTP tests. Freeze code identity.
2. Baseline preflight; small overlap calibration; serial then overlap at 175K/32K and 201K/47K actual target input. Three repeated-prefix rounds if gates pass. Confirm client overlap separately from engine scheduler proof.
3. On an incident, preserve earliest model/router/kernel evidence before managed recovery. Test a supported single delta (APC or DCP/collective path implicated by evidence); use serial C1 as mitigation control if appropriate. No speculative-decoding hypothesis for this no-spec incumbent.
4. Once baseline behavior is characterized, screen one conservative batching or context/concurrency delta, retaining parent control. Explore larger inputs only from passing smaller points. No full Cartesian sweep.
5. Qualify promising configuration with independent retrieval, tools, JSON, vision and long-output checks, plus repeated matched capacity lanes. If no improvement is established, retain baseline and state unresolved limits.
6. Restore and verify starting recipe/router state, publish source-pinned method, all failed trials, tested matrix and bounded shareable recipe guidance.

## Bounds and stopping
Each replay: at most 20 rounds, two streams per round, 600s per request for the first cohort; stop following rounds on failure or missed coverage. Initial live allocation 90 minutes with 30 minutes held for recovery/verification; reassess against evidence rather than blindly exhausting a matrix. Host containment remains 52GiB plus 1MiB swap; no limits are increased. Stop immediately on new Xid, OOM, engine death, identity drift or unrelated active workload. A client timeout is not proof the engine is idle. Incident remains open until the trigger and remedy have appropriate evidence.

## Acceptance
Skill packaging and independent behavioral review are distinct. Runner tests must reject missed overlap, malformed/incomplete streams and token mismatch. Runtime, coverage, retrieval, output formatting and performance outcomes remain separate. The runner verifies managed container identity before and after each round, including recipe/registry/revision labels. This is managed provenance, not attestation that every engine setting loaded. Retain effective startup logs separately.

## Evidence-driven change at 17:08 UTC

Original crash reproduced at the first large overlap on the upgraded driver. Stop the 201K/envelope/batch1024 branch. After exact baseline recovery and independent review, test DCP1 as a single causal-path isolation arm with the same containment and request policy. Measure actual KV capacity before context expansion. Run small preflight, the failed overlap, and repeated larger cases only from passing points. A successful DCP1 arm is a mitigation candidate; production promotion remains separate.
