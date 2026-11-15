# Align managed prepare preview help and parser

Status: open.

The managed prepare/preview path advertised a preview option, but the frozen
parser rejected it before producing a preview. The campaign retained the
failure in `stock-qwen27-fp8-tp1-bf16kv-131k-xhigh-swe5-preview-erratum.json`.

Either provide a side-effect-free preview for the documented path or remove
the unsupported option from its help. Keep preparation and lifecycle mutation
behind their existing confirmation boundary.

Acceptance: focused help/parser coverage proves every advertised preview flag
is accepted and performs no mutation, or that no unsupported preview flag is
advertised. The command manifest and focused help agree.
