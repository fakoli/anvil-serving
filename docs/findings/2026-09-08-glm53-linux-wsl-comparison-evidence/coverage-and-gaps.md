# Coverage and gaps

| Request or gate | Evidence and outcome |
|---|---|
| Exact migrated configuration | [Identity](configuration-and-identity.json); matching model/image, explicit NCCL and host-stack differences |
| Historical speed comparison | [Four matched prompt cells](historical-comparison.json), C1 n3 each; descriptive variable-output baseline |
| Controlled output and unique cache | [Strict scout](controlled-scout-4k-r3.json) 0/3; [unique natural](linux-unique-natural-4k-r10.json) 1/10 canary compliant; dependent cells stopped |
| Direct protocol gates | [Disabled](direct-preflight-disabled.json) 9/10; [enabled](direct-preflight-thinking-enabled.json) passes |
| Coding quality | [Five cases x3](coding-quality.json), 15/15 |
| Image/OCR corpus | [Six cases x2](image-corpus.json), 12/12 |
| Extended context | [Native sweep](context/artifact.json) 128/150; 9 empty length-terminated and 13 incorrect visible answers; non-monotonic curve |
| Agentic recovery and long sessions | [Deep profile](agentic/artifact.json), 30/30 |
| Matched repository-agent smoke | [Official SWE grader](swe/artifact.json), resolved 1/1; 22 requests versus historical 11 |
| Sustained reliability | [Endurance](endurance-4k-r60.json), 60/60 |
| Routed protocol gates | [Disabled](routed-preflight-disabled.json) 9/10; nominal 380K HTTP413; [260K](routed-needle-260k.json) passes; [enabled reasoning evidence](routed-thinking-enabled.json) fails projection |
| Post-run state | [Verified unchanged serving state](post-state.json); [routed smoke/JSON](post-routed-smoke.json) passed |

The Windows arm is retained evidence rather than a newly rerun installation.
Strict controlled-output performance qualification did not pass. Cache state,
driver and transport prevent an OS-only causal claim. C1 is the deployed engine
ceiling; higher concurrency, full SWE-bench, video and speculation retuning are
outside this deployed-profile comparison. No profile promotion is authorized.
