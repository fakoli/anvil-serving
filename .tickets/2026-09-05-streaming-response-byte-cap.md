# Bound all upstream SSE response bytes before parsing

Status: fixed; independent adversarial review passed
Priority: P1

Adversarial review reproduced a pre-existing response-cap bypass: the relay
counted emitted text deltas, while tool arguments, usage events, comments, and
framing were not counted. With an eight-byte cap, a synthetic tool-only SSE
event containing 100,000 argument bytes could be assembled successfully. An
untrusted upstream could therefore grow retained response data beyond the
configured bound.

Fix forward: count every raw upstream response byte before handing it to the
SSE parser, across both dialects and injected transports. Exceeding the bound
must close the stream, release admission, and record one content-free terminal
failure. Add regressions for tool-only events, event fragments, comments, and
normal responses at the boundary. Keep error bodies and tool arguments out of
diagnostic evidence.

Final implementation, independent disposition, and repository verification are
tracked in the [delivery ticket](2026-09-05-router-request-diagnostics.md).

The implemented bound uses limited raw line reads before parsing, including
tool arguments, comments, malformed events, usage, and framing. The plain-JSON
fallback also uses a bounded read. Additional probes found provider error
frames and missing stream terminators could appear successful; both dialects
now raise a sanitized failure for those cases. Dedicated regressions prove one
terminal failure and upstream closure, including after partial content.
