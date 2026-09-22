# Coverage and gaps

| Requested outcome | Status | Evidence or gap |
|---|---|---|
| External shortlist screen | retained | [source registry](source-registry.json) records advisory priors only. |
| Qwen startup and direct preflight | covered | [startup](qwen-startup-complete.txt), [basic](qwen-preflight-basic.json), and [tools](qwen-preflight-tools.json). |
| Deterministic agentic correctness | blocked by harness validity | Qwen native outcomes were 16/18 and 15/18; paired GLM was 16/18 with planning false negatives from scorer ordering. |
| Qwen capacity, C4 performance, vision, deep coding, and large context | not run | Deferred after the strict agentic gate did not pass. |
| GLM paired scout and exact restoration | covered | Paired scout is retained; exact baseline identity/configuration, direct/routed checks, and router readmission passed. |
| Promotion decision | covered | Retain GLM; Qwen remains an interesting but unqualified candidate. |
