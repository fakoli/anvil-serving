# MiMo and uncensored GLM qualification scout

<!-- benchmark-result-card/v1 -->

> **Result card.** On two RTX PRO 6000 Blackwell Max-Q cards, the pinned MiMo V2.6 Flash RL no-spec TP2 candidate passed preflight 25/25 and resolved 4/5 frozen SWE tasks, but missed the required 18/18 agentic gate in three tested request policies (15/18, 17/18, and 17/18). The pinned Lovesenko GLM-5.3-Flash TR3 4-bpw abliterated candidate passed preflight 25/25 and agentic 18/18, then resolved 3/5 frozen SWE tasks, below the campaign’s 4/5 coding floor. Neither candidate is qualified or promoted. The 327,680-token/C4/eight-image values are configuration, not measured capacity, context, multimodal, or performance results. [Raw evidence](artifacts/2026-10-06-mimo-qualification/README.md).

## Scope and decision

This was an isolated direct-endpoint scout on a dual RTX PRO 6000 Blackwell Max-Q pair. The campaign tested two prospective replacements while preserving the incumbent route and aliases. The coding floor was 4/5 resolved on the frozen five-task SWE set.

- **MiMo V2.6 Flash RL:** `not-qualified`, `no-promotion`. The strongest observed SWE result was 4/5, but every native agentic profile missed the required 18/18 gate.
- **Lovesenko GLM-5.3-Flash TR3 4-bpw Abliterated:** `not-qualified`, `no-promotion`. Its 3/5 SWE result was fully submitted and graded with zero grader errors, but below the floor.
- **Incumbent:** exact r11 was restored and readmitted. Direct preflight passed 25/25, routed preflight passed 7/7, all four protected configuration hashes were unchanged, and the exclusive dual-GPU owner returned with zero cgroup OOM counters and no vLLM offload mapping in shared memory.

## Identity and configured recipes

MiMo used `XiaomiMiMo/MiMo-V2.6-Flash-RL@5711b268169967567844e1e560e8a3966da959b1`, LIL vLLM image digest `ad7b059336e539068fc8b829fee7e7db04b0c3d9c32c455e69056d704619c729`, TP2/DCP1, no speculative draft, configured 327,680 total tokens, C4 admission, eight-image limit, and native sampling temperature 1.0/top-p 0.95. The public reconstruction is [here](artifacts/2026-10-06-mimo-qualification/configs/mimo26-nospec-qualification.toml).

The uncensored GLM candidate used `lovesenko/GLM-5.3-Flash-tr3-4bpw-Abliterated@c8f58e6aa9117c73607d692978b22f091d80450c`, EXL3/TR3 4-bpw, TP2/EP2/DCP1, FP8 DS-MLA KV, configured 327,680 total tokens, C4 admission, eight-image limit, APC enabled, and speculation disabled. It is a distinct weight-space candidate from the base GLM and the earlier CSF/QAD lane. Its public reconstruction is [here](artifacts/2026-10-06-mimo-qualification/configs/glm53-abliterated-qualification.toml).

Configured values did not become measured context, concurrent-C4, multimodal, or speed claims because quality stopped the campaign first.

## Measured quality gates

| Candidate | Preflight | Agentic | Frozen SWE | Decision |
| --- | ---: | ---: | ---: | --- |
| Incumbent control | 25/25 baseline preflight | 18/18 | 3/5 | Control only; not a new qualification claim |
| MiMo native thinking | 25/25 | 15/18 | 4/5 | `not-qualified` |
| MiMo greedy | — | 17/18 | — | `not-qualified` |
| MiMo thinking-off | — | 17/18 | — | `not-qualified` |
| GLM abliterated | 25/25 | 18/18 | 3/5 | `not-qualified` |

MiMo’s raw greedy replay generated only one of two requested parallel calls and stated that it could call only one tool per turn. The parser matched that output, so this is not evidence of parser loss or cache corruption. The thinking-off miss was a request-compliance failure. These were native results, not an inference about general coding intelligence from formatting alone.

The GLM abliterated SWE failures matched the Django and Pylint misses in the baseline control. That common failure does not remove the frozen 4/5 floor or qualify the candidate.

The official grader completed all five uncensored submissions with zero grader errors, empty patches, timeouts, OOMs, parse errors, or step-limit failures. It resolved Xarray, Sphinx, and Sympy, and marked Django and Pylint unresolved. The retained [combined report](artifacts/2026-10-06-mimo-qualification/uncensored-r1-official-grade-combined.json) and per-instance failure reports preserve that distinction.


## Limits and unrun work

No full performance, actual 327,680-token context, simultaneous-C4, multimodal, endurance, or route/client acceptance benchmark was run after either candidate missed a required quality gate. No model was promoted. No speed winner is claimed.

The startup evidence reports an 825,268-token KV pool and no OOM for the uncensored GLM candidate. It is startup/compatibility evidence only; it does not prove the configured context contract or workload capacity.

## Evidence and public safety

The [raw artifact packet](artifacts/2026-10-06-mimo-qualification/README.md) preserves native schemas and failed states, with a [redaction ledger](artifacts/2026-10-06-mimo-qualification/redaction-ledger.json) binding each private source hash to the published redacted bytes. Its nested native hashes remain original provenance, not public-byte attestations. The finalized ten-role artifact manifest binds the complete public packet. Publication-summary is not applicable because no social copy or external post was requested.

## Restoration and cleanup

The incumbent restoration is verified. Direct preflight passed 25/25 and routed preflight passed 7/7 after router readmission. The baseline model, router, recipe, and serve configuration hashes were unchanged. r11 again holds both GPUs exclusively; cgroup OOM counters were zero and the bounded shared-memory inspection found no vLLM offload mapping. This is a restoration smoke, not a new context, C4, multimodal, endurance, or performance qualification.

The thinking-off Pi restoration probe failed its exact-output oracle because it emitted extra visible prose. One same-prompt/oracle Pi max diagnostic then passed; native `max` maps to `xhigh`, reasoning content was observed, and the raw wire-level effort was not captured. That single success does not establish equivalence to direct max or a thinking-off reliability claim.

Four exact unused cache revisions were removed. Native logical reclamation was 457,907,478,456 bytes and the bounded filesystem increase was 457,870,778,368 bytes (about 458 GB). The rejected MiMo removal reclaimed 3,057,541 bytes while preserving its shared revision; the uncensored removal reclaimed 175,786,995,640 bytes. No image was removed because saved Compose image references remained unresolved.
