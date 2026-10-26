# Failures and fix-forward closure

| Failure | Durable correction and independent check | Disposition |
| --- | --- | --- |
| Broken incumbent Python client; in-place Windows executable locked | Versioned wheel/client installation; installed stdio bridge and clean-wheel checks | Closed; old clients preserved |
| HTTP controller rejected missing idempotency key and execution context | Shared MCP bridge supplies per-confirmed-call UUID and validated optional identity; six wire/validation tests; SDK2.3 security and complete bundled licenses | Closed; no auth gate weakened |
| Read-only worker source used as temporary directory | Explicit /tmp/h3-mcp in managed recipe; actual MCP startup and prompt completed | Closed |
| Internal-only Docker Desktop network hid published ports; authenticated lifecycle health was probed anonymously | Front bridge and authenticated dependency check; managed startup passed | Closed; offline mode is not egress isolation |
| Inherited controller healthcheck reported the gateway unhealthy despite working MCP | Gateway-specific /healthz check in private Compose; recreation through managed serves and independent healthy readback | Closed |
| Initial observer counted almost all base execution as queue time | Shared adapter checks running queue plus history; race/error regressions and real RUNNING state in final retry | Closed for future jobs; base native log144.77s retained;0.038s is not model runtime |
| Windows heartbeat atomic replace failed with WinError5 after outputs completed | Bounded PermissionError retry;20guard tests and native held-handle previous/fixed/installed probes; cold upscale under final wheel | Closed; exact competing reader unknown, permanent/stale failure still stops only owned worker |
| Different legacy GPU envelopes could miss H3 residency | Read-only H3 reservation row in old mounted manifest; both old resource controller and Qwen previews refused overlap | Closed; no competing model started |
| Private qualification fixture expected British-spelled cancelled | Actual terminal state was canceled; corrected fixture assertion and retained native response | Closed; product cancellation already succeeded |
| Full native Windows test collection imports Linux-only UnixStreamServer | Scoped media/bridge/auth/CLI tests pass; preserve baseline collection failure, require Linux CI | Open platform coverage limit; no unrelated test rewrite |
| H3 territorial/output permissions unresolved | Hold videos/frames/weights outside public bundle; publish technical facts only | Open external rights boundary |

No failed attempt is a model-quality comparison. Source commits and native Windows sharing probes
are the fix-forward artifacts; successful retries alone are not the basis for closing code defects.
