# Requested coverage

| Outcome | Required proof | Final state |
|---|---|---|
| Exact model and RTX 5090 target | Pinned model/runtime and measured local hardware | Passed; [identity](model-identities.json) and [source ledger](operational-source-hashes.json) |
| Complete local weights | Snapshot, vision/MTP/processor assets | Passed: 26 files, 23,447,947,228 bytes; 333 BF16 vision tensors |
| Resource fit | Measured allocation and headroom | Selected 32K/C1: weights 22.14 GiB, KV 4.73 GiB, runtime cache 56,262 tokens; minimum sampled free VRAM 3,342 MiB ([telemetry](telemetry-summary.json)) |
| Functional gates | Text/JSON/tools/streaming/tool continuation | Selected profile 27/27 ([results](triton-bf16-32k-result-summary.json)) |
| Required vision | Images/OCR/charts/screenshots/spatial/two-image checks | Selected profile 18/18; two images admitted, video disabled |
| Reasoning | Independent controls and repeated quality | 8/8 control observations; ten-question diagnostic repeated three times 30/30. FP8 cache rejected after 27/30 and three budget exhaustions |
| Controlled capacity | Strict output, canaries, unique prompts, adequate population | Selected profile 100/100; median decode 158.355 tokens/s, TTFT 455.442 ms. Actual prompt 3,609-3,687 tokens, 45 completion tokens; thinking off |
| Optimization | Complete qualifying configuration comparisons | Eager/graphs and target-Triton MTP configurations measured; BF16/FP8 cache comparison measured. No isolated MTP speedup claim |
| Useful context | Actual tokens, output reserve, retrieval | [60/60 passed](context-result-summary.json): 30 at actual 8,198-8,229 prompt tokens, 30 at 29,578-29,629; 2,048 total completion cap. Configured maximum 32,768; larger inputs unmeasured |
| Concurrency | Simultaneous request correctness | C1 only; C2 and multiple-user capacity unmeasured |
| Agentic behavior | Independent multi-step tool checks | [2/2 synthetic cases](agentic-result-summary.json), six API turns, four tool calls, safe error retry passed |
| SWE | Isolated pinned worker, resolved repository tasks | Missing; no isolated worker configured, no SWE claim |
| Video | Separate corpus and admission | Outside requested image contract; disabled and unmeasured |
| Stability/energy | Telemetry and endurance evidence | 1,681 valid five-second samples; selected window 263 samples, peak sampled 478.03 W / 66 C. No long soak, energy-per-token or peak-capture guarantee |
| Ending state | Managed status and independent HTTP checks | [Verified candidate retained](restoration.json), exact selected identity; native health and models HTTP 200 |
| Publication and repository checks | Cross-linked evidence and explicit gate status | Evidence matrix retained; [validation](validation-final.json) separates documentation checks from the failed unrelated Windows Pi full-suite gate |

Failed 256-word scouts and the Flash-MTP 99/100 population remain ineligible for clean performance comparison. The small quality diagnostic is not full MMLU-Pro, and no BF16-weight reference was measured. Runtime cache capacity is configuration-specific; a 16K cache count does not establish a universal physical ceiling. The initial 4 GiB free-VRAM reserve assumption was not maintained; the selected profile's observed minimum was 3,342 MiB.
