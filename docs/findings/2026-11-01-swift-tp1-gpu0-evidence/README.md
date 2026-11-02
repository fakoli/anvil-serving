# Swift TP1 qualification evidence

This bundle contains redacted derivatives of retained qualification snapshots.
Original native artifacts remain authoritative and private; the
[redaction ledger](redaction-ledger.json) binds original and derivative hashes.
It makes no claim about current deployment, routing, aliases or clients.

The tested BF16/FP8-KV SGLang/MTP3 recipe passed the bounded qualification gates.
The fresh ten-cell capacity population (72 requests) is separate from the
historical two-arm comparison (432 requests). C1–C8 were matched historically;
MTP C16 had an active cap of nine versus sixteen without speculation. Strict
formatting failed 4/4, R5 violated the reserve policy without OOM, and the final
32K/C2 decode median missed the soft 15 tokens/s target. SWE resolved 4/5.

- [Dated finding](../2026-11-01-swift-tp1-gpu0.md)
- [Manifest and file hashes](artifact-manifest.json)
- [Dated sources](source-registry.json)
- [Measured results and decision](summary.json)
- [Evidence summary](evidence-summary.md)
- [Historical frozen plan](plans/run-plan-r4-summary-complete.json)
- [Configuration snapshot](configuration/model-config.json)
- [Final capacity](raw/capacity-summary.json)
- [Resource validation](raw/resource-final-validation.json)
- [Historical comparison chart](graphs/historical-r4-ordinary-summary.svg)
- [Failures and friction](friction-log.md)
- [Limits and withheld records](missing-data.md)

Snapshot states such as `running` describe the measurement time only.
Operational custody and post-campaign restoration decisions are withheld.
