# Attribute native prompt transfer time without exposing request text

Status: implemented; independent review and managed image validation pending.

The pinned Strata diagnostic artifact reports batched prefill phase timing, but
the request's prompt timer also includes slot lending, short verify-window reads,
checkpoints, and restoring borrowed expert-cache slots. A local diagnostic run
showed substantially more prompt time than the batched prefill wall time. That
difference is an unresolved symptom, not evidence of disk latency or a kernel bug.

Add a separate immutable leaf under
`configs/runtime/strata-6f32ec0-sm120/diagnostics-trace-m50/`. Preserve the built
parent's server and timing parser; verify their exact hashes before extending the
helper with bounded, exact numeric lend/refill/read records and one fixed fused
activation banner. Keep engine arguments and resource limits unchanged.

Validation covers strict numeric parsing, rejection of arbitrary suffixes and
unapproved lines, delegation to the unchanged parser, and failure before writes
on source-identity mismatch. Capture the reviewed build identity and managed
runtime observations before attributing the missing time or changing chunk size.

The first managed build failed before layer execution because BuildKit parsed a
bare `FROM sha256:...` as a Docker Hub repository and tag. Use the exact local
parent name with its immutable `@sha256:...` identity, as the preceding successful
diagnostic build does. Do not replace the digest with a mutable tag fallback.

Interpretation requirements: host-staging and PLE fields are cumulative within
the engine. Native request file counters begin after prompt/refill and describe
decode traffic only. Neither mapped-file access counts nor refill wall time prove
physical storage misses. Omit both presence-based trace and prefill-timing flags
for final performance qualification.
