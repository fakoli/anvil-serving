# Native evidence

These public copies preserve the native schemas and complete responses. Operator
metadata transformations and original/public byte hashes are in
[redaction-lineage.json](redaction-lineage.json).

| Lane | QUASAR | Swift15 |
| --- | --- | --- |
| Preflight | [native](native/quasar-32k-preflight.json) | [native](native/swift15-text32k-preflight.json) |
| Agentic | [16/18](native/quasar-text32k-agentic-raw.json) | [18/18](native/swift15-text32k-agentic-raw.json) |
| Coding responses and reasoning | [native](native/quasar-text32k-coding-raw.json) | [native](native/swift15-text32k-coding-raw.json) |
| Frozen list-pair grade | [119/123; 20/21](native/quasar-text32k-coding-grade.json) | [120/123; 20/21](native/swift15-text32k-coding-grade.json) |
| Post hoc tuple-pair sensitivity | [123/123; 21/21](native/quasar-text32k-coding-grade-tuple-edges.json) | [120/123; 20/21](native/swift15-text32k-coding-grade-tuple-edges.json) |

All 21 collected coding attempts per model completed with stop reasons, reasoning
evidence and declared budgets. Collection markers alone are not correctness gates;
[the frozen executable fixture](fixture/README.md) supplies independent case checks.
The public redacted copies reproduce every original frozen case result.

[Harness provenance](native/harness-provenance.json) identifies installed 1.5.0
and its source-file fingerprint. The publication worktree's 1.2.1 version is a
separate identity. [Restored preflight](native/final-restored-preflight.json) is
public; underlying detailed operator state remains private and is summarized by
[the independent restoration verification](native/final-restored-verification.json).
Timing is descriptive only; no SWE, capacity, or general ranking claim is implied.
