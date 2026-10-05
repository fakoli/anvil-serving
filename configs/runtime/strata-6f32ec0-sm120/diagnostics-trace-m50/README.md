# Bounded native prompt trace

This leaf extends the immutable diagnostic image without changing its server,
existing timing parser, preparation wrapper, metadata verifier, runtime profile,
engine arguments, memory limits, or entrypoint. Build installation verifies both
the original helper and patched server SHA256 before preserving the helper under
a fixed import alias and installing the extension.

With STRATA_DIAGNOSTIC_LINES=1 and STRATA_TRACE=1 in a reviewed recipe, managed
logs expose only three exact numeric trace grammars: slots lent, slots refilled,
and tokens read through the batched or verify-window path. One exact fixed banner
reports that at least one layer activated fused kernels. It does not establish
that all layers used those kernels. No arbitrary trace text is forwarded.

Refill elapsed time includes expert source lookup, CUDA copy submission, stream
synchronization, and residency upload; it is not a physical disk timing. The
inherited host-staging and PLE timing fields are cumulative within the engine,
whereas wall, GPU phases, and chunk setup/wait/callback fields describe one call.

Both STRATA_TRACE and STRATA_PREFILL_TIMING are presence-based upstream: omit
them entirely for headline measurements. Setting either to zero still enables
instrumentation. Keep trace trials separate from performance qualification.
