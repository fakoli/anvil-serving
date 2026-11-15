# Prospective decode control integration

Frozen before new decode timing in `decode-contract.json`; exact prior request bytes
live under `decode-bodies/` with per-file hashes. The original decode-fixture.py is
unchanged. Requested temperature1/top_p.95/top_k20/seed42/output1024 and omitted
reasoning effort remain byte-identical; these requests do not prove actual sampling
or deterministic output. Parent's package sampling inspection remains authoritative.

Use existing run-phase.py, `--lane decode-c1` or `--lane decode-c4`, `--phase measured`,
with the existing selected arm, new output directory and parent request cutoff.
No prime/warmup is silently added. Existing fresh-incarnation and disjoint warmup
responsibilities remain with the parent. C1 sends index0; C4 sends indices0..3 through
four HTTP worker threads. Request budgets remain60seconds and existing5second
managed status/cgroup sampling surrounds the same HTTP child. No lifecycle changes.

Each response must expose exactly1024 valid raw IDs, usage completion1024/prompt7037,
length finish, aligned native timestamps and valid native clock ordering. The protected
metric is median per-request native decode rate:1023 inter-token intervals divided by
last minus first native token timestamp. This metric and output contract are fixed
before timing; client TTFT and E2E remain retained separately.

Reasoning matching uses the SHA256 of exact concatenated observed reasoning_content
text, or explicit native reasoning-token counts if supplied. Missing reasoning
observability remains null and makes comparison incomplete. Do not turn missing into
zero. Exact output IDs must match by case across arms; matching seed alone is insufficient.

All requests are drained before/after a batch. Complete native speculative counter
vectors, including batch/width labels, are retained ONCE as `batch_speculative_work`
in http/summary.json, with raw batch-before.prom/batch-after.prom. C4 counters are
never copied into request rows; request speculative_work is null with an explicit
batch-only scope. For normalized analyzer runs, attach this summary field at run level
alongside http/attempts.json. Analyzer requires equal whole-batch vectors as well as
matched per-request IDs/usage/reasoning. Missing counter families, inconsistent label
support, inconsistent totals or attempted per-request attribution fail the gate.

`check-decode.py` verifies exact historical bytes,1024-ID normalization, reasoning
spans and C4 batch matching, including negative/corrupted cases. Existing checks also
pass. No model request, lifecycle or new timing occurred during this integration.
The parent's restore-fidelity hold remains in force; runnable support does not clear it.
