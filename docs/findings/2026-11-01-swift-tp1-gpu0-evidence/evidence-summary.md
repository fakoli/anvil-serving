# Qualification evidence summary

The named recipe passed bounded qualification. Deployment, routing and
client assignments are outside this public record.

Completed retained results:

- Historical r4 ordinary-summary comparison: 432 requests across two arms.
  At about 7.4K input, MTP decode p50 was 67.3 versus 25.4 tokens/s at C1,
  and 40.0 versus 19.5 at C2. This is a workload-specific observation.
- Fresh r4 direct image evidence: 12/12 deterministic cases passed and the
  ordered eight-image probe passed 2/2. Video remains excluded.
- Fresh r4 agentic evidence: 18/18 full-suite cases and 6/6 pilot cases passed.
- Fresh r4 C1 context evidence: all 36 cases passed with stop finishes. The
  18 near-full cases measured 245596–245626 input tokens, which is at least
  99.8046875% of the 245760 bucket; each used a 16384-token output reserve.
  These quality requests overlapped coding work, so their latency is excluded
  from performance comparisons.
- Fresh r4 SWE fixed campaign floor: all five tasks completed independent
  grading; four resolved and Django10097 remained unresolved. All five agent
  submissions reached `Submitted`, with no infrastructure failure. This is a
  five-task floor, not a broad coding-benchmark population estimate.
- Fresh r4 C2 concurrent context evidence: two distinct-prefix long-context
  requests both passed exact-answer scoring and overlapped for 260.115 seconds.
  During that overlap, retained samples reported `running=2` and peak used KV
  of 458520; the two actual prompt counts plus their output reserves totalled
  491236. This is observed pair evidence, not a hypothetical full-residency or
  general capacity claim.
- R5 at .94 memory allocation was rejected by the required GPU reserve policy;
  r4 at .90 was restored. This is not an OOM claim.

Limits carried forward:

- Strict controlled-output diagnostic: 4/4 failures, performance-ineligible.
- Historical MTP C16 had an effective active cap of 9; no-spec had 16. C1–C8
  are the matched portion of the historical comparison.
- Historical r4 performance activation differs from the fresh final r4
  activation. No metric is carried over as final-activation performance proof.
- Final stability, capacity, and resource-window evidence passed; the fresh
  final population is separate from the historical comparison.
- The C2 amendment's receipt digest follows its source canonical-JSON contract;
  the redaction ledger separately binds original and staged physical file-byte
  SHA-256 values and does not substitute one digest type for the other.

`redaction-ledger.json` records original and derived SHA-256 values. The staged
JSON preserves native schemas where present but is a redacted derivative, not
byte-identical raw native evidence.
