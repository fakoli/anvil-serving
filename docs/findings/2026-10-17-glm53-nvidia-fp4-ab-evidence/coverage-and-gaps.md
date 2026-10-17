# Request coverage

| Request | Evidence condition | Outcome |
|---|---|---|
| NVIDIA recipe with slight modifications | Pinned source, exact launch, explicit delta | [r2 configuration](configuration.json); upstream inference controls retained |
| Controlled A/B against current r11 | Fresh controls and matched independent checks | Initial context baseline18/18 versus candidate17/18; [native results](summary.json) |
| Tools, JSON, vision and synthetic agent tasks | Independent deterministic checks | Both passed applicable checks; repository-scale coding remains untested |
| Retry/debug the failed case and investigate configuration | Preserve original failure; predeclared repeats and conditional single-variable arm | Unchanged r2 full replay18/18; failed case3/3 follow-ups. [Diagnostic](diagnostic-result.json) did not reproduce the miss; no configuration cause or repair established. MTP-off condition not met |
| Preserve working deployment | Exact identity/config hashes and authenticated route | [Final restoration](restoration.json) verified original r11 and direct/routed checks2/2 each |
| Promotion | Separate authorization and complete qualification | Not authorized or performed |

This closed scout and diagnostic does not provide a candidate performance ranking, sustained-concurrency qualification, four simultaneous327K requests, repository-scale coding score or endurance result. The initial failure remains failed. Subsequent passes are reported separately and do not establish a general failure probability or prove any causal component.
