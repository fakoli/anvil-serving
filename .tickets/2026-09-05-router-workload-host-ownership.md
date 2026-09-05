# Close router workload startup ownership and empty-result identity

Status: specification complete; workload-visibility:T005.1/T005 implementation pending.

The workload endpoint needs a trusted host identity, but router startup has no
such field. A plain SourceResult also cannot identify an empty source. Add the
optional server workload_host and wire it with the exact existing registry;
return the canonical one-source NodeResult so even empty results are bound to
the configured host. Missing ownership refuses after the existing scoped gate.

The PRD now closes strict URL query decoding, error status/prose, registry and
clock wiring, built-in path collision handling and the downstream node decoder.
No host inference, alternate serializer, new privilege or live config mutation.

Separate the two-file startup parser slice (T005.1) from the four-file endpoint
integration (T005). This deterministic split follows the already closed
contract; no new product decisions or independent acceptance gate were added.
