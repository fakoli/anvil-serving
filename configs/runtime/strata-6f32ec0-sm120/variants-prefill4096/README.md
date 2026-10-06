# Fixed 4096-token borrowed prefill experiment

This leaf changes only the fixed runtime's `--prefill auto` argument to
`--prefill 4096`, plus model/log/config identity labels. Borrowing stays enabled;
the resident budget remains 36GiB and the exact container ceiling remains 50GiB
with zero swap. The reviewed preparation, metadata, and trace helpers are inherited
from the immutable parent. No timing, trace, or fused environment is baked.

The hypothesis follows diagnostic observations of substantial time restoring
borrowed expert slots after prefill. A smaller chunk needs fewer borrowed slots,
but may perform more expert-streaming passes; neither faster execution nor model
qualification is assumed. The earlier diagnostic profile had a transport failure
in its concurrent preflight burst and remains unqualified.

Capture actual startup chunk, expert cache, residency, and memory pressure. Compare
the same frozen three diagnostic prompts before considering any broader trial.
Retain failures, repeat independent quality gates, and omit presence-based timing
and trace flags for final performance qualification. This recipe does not reduce
the host reserve, disable borrowing, or promote a model.
