# Requested coverage

| Outcome | Required proof | Current evidence |
|---|---|---|
| At least 128K context | Healthy 131,072 configuration and near-limit retrieval with output reserve | Current MTP/UVA3 startup cache 138,519 tokens; scout 2/2 at actual 32,658 and 125,838 input tokens, with 4,096 completion-token cap. Full 60-case text matrix passed: all 60 completed with `stop`; actual inputs 32,658–32,689 and 125,838–125,869, with a 4,096-token output reserve |
| Complete weights and vision | Pinned complete snapshot and actual image behavior | Complete snapshot retained; functional 27/27 and vision 18/18 passed |
| Reasoning quality | Independent controls and repeated ten-item diagnostic | Current MTP/UVA3 controls 9/9; quality **30/30 passed**, all normal stop. Prior no-MTP branch failed 27/30 on a repeated wrong answer and was rejected |
| Useful speed | Strict short-output population | Current MTP/UVA3 100/100 strict, zero failures: 51.8855 tok/s p50 decode, 3,003.0448 ms p50 TTFT; whole-recipe comparison to faster 32K profile |
| GPU and host memory | Measured reserve and stable host | [Final resource summary](final-resource-summary.json): 1,440 MiB GPU free at final observation; 1,561 valid five-second samples through the full context matrix remained at or above the 1,024 MiB floor. The buffered series does not continuously cover the later brief agentic gate. Generic UVA offloads 3.02 GiB; after all gates Windows free physical memory was 7.014 GiB and free virtual memory 9.036 GiB |
| Tool workflows | Deterministic multi-step and error recovery | Functional tools passed; synthetic agentic passed 2/2 across six API turns and four tool calls |
| Final deployment | Qualified selected identity or exact rollback | Current MTP/UVA3 completed its declared direct gates and remains a bounded retained 128K candidate; no route, client, or promotion change |

Scope is C1, two configured images, no video. The vision fixtures are 640 x 360 pixels, including a two-image comparison. Separate near-limit text and small-image tests do not prove a maximum-size two-image request at the context boundary. Exact-total and one-over-limit admission requests were not run. C2, SWE and long-duration soak are untested. The earlier unrelated Windows Pi repository-test failure remains visible; no product source changed.
