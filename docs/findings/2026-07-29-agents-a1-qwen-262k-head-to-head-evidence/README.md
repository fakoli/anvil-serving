# Agents-A1 FP8 versus Qwen3.5 122B 262K evidence

This directory retains the exact identities, startup records, request
artifacts, failures, comparison, and restoration evidence for the 2026-07-29
RTX PRO 6000 head-to-head.

The comparison uses the unchanged
`benchmarks/corpora/agents-a1-v1/corpus.json` manifest. Media bytes are never
embedded in request logs; paths, MIME types, byte counts, and hashes are
retained by `multimodal-benchmark-evidence/v1`.

No artifact in this directory authorizes a production route or model
promotion.

Start with [comparison.json](comparison.json), then inspect the paired
`preflight`, `capacity`, and `multimodal` artifacts. Startup logs retain the
lowest actionable failures, including Qwen's first GPU-UUID parser failure and
the NGC image's missing H.264 decoder. `serve-state-before.json` and
`serve-state-after.json` prove exact restoration.
