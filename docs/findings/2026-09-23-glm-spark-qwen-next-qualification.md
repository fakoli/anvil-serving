# GLM Spark and Qwen Flash Next qualification — no promotion

**Date:** 2026-09-23

**Decision:** retain the current GLM-5.3-Flash EXL3 r10 APC reference. The authorized conditional promotion did not advance because every candidate and the incumbent control failed the new strict C4 capacity gate.

<!-- benchmark-result-card/v1 -->

## Result card

| Item | Result |
|---|---|
| Hardware and workload | Two RTX PRO 6000 Blackwell Max-Q GPUs, PCIe TP2; frozen five-task SWE scout, Oracle7, vision/context diagnostics, and strict C4 capacity. |
| Qwen Flash Next NVFP4 | Medium profile resolved 4/5 frozen SWE tasks and passed Oracle7 18/18, but its four-request strict repair population was 0/4. Default-xhigh diagnostics are separate from medium. |
| GLM Spark NVFP4 | Resolved 2/5 attempted SWE tasks; 3 were submitted/graded and 2 attempts hit the 60-call limit. Oracle7 was 16/18 and strict C4 was 0/16. |
| Incumbent control | Resolved 3/5 SWE tasks and Oracle7 18/18, but strict C4 was 0/16 (14 wrong counts, two truncated captures). Retention is not a pass for this new gate. |
| Evidence | [Bundle README](2026-09-23-glm-spark-qwen-next-qualification-evidence/README.md) · [manifest](2026-09-23-glm-spark-qwen-next-qualification-evidence/artifact-manifest.json) |
| Decision | No promotion, route, catalog, dashboard, or client change. A new supported profile must pass the failed gates and full frozen qualification. |

## Scope and method

The campaign used the same frozen five-task SWE selection for all three profiles. Oracle7 required 100% in this campaign; its native suite's 75% threshold remains a separate, lower threshold. The strict cell used unique cache, 128-word requests, nominal 32K context, an 8,192-token completion cap, C4, and canaries. Failed strict populations are performance-ineligible.

This is a bounded local qualification, not a full SWE-bench, general-intelligence, or controlled speed comparison. Agent durations were descriptive: incumbent 2,050.639483 s, Qwen medium 536.237341 s, and Spark 1,441.833844 s. Spark overlapped offline product tests, so these values do not rank speed.

The exact candidates were `local-inference-lab/Qwen3.8-Flash-Next-NVFP4@7c4f1bc1a2d6847e0cbc01ac6b823f00251de8dd` on the beta3221 vLLM image and `local-inference-lab/GLM-5.3-Flash-NVFP4-Spark@a608241037e4c2565356bff7ca293f2133888f88` on the canonical pinned image. Both used TP2 and up to eight images; the [sanitized configuration records](../benchmarks/configurations.md#configuration-index) retain context, C/request controls, and exact image digests.

## Gate results and limits

Qwen medium submitted all five SWE tasks and resolved four. It passed Oracle7 18/18, but strict capacity was 0/4. Its separate default-xhigh profile passed six preflight groups (26/26 observations), four eight-image requests, and retrieval at 412,589 actual tokens. Its long-guide diagnostic produced 23,150 visible tokens; none of those default-xhigh results qualify medium.

Spark attempted five SWE tasks: three were submitted/graded and two attempts reached the 60-call limit; two submitted tasks resolved. It passed the eight-image and context diagnostics, but Oracle7 was 16/18 and strict capacity 0/16. Its long guide stopped after 37,029 visible tokens. Both candidate long guides completed 24 chapters, but retained static and instruction-integration defects mean they are diagnostics, not executed application correctness.

The incumbent submitted five SWE tasks and resolved three. Its 64K diagnostic ended at the 65,536-token length limit with 11 complete of 12 started chapters and 70,772 visible characters; the native reasoning/visible token split is unknown. Independent review retained parser and scheduler defects, without observing a visible repetition loop. The strict incumbent population had 14 wrong counts and two length-truncated captures; its own marker appeared first in all 16 captures and no foreign marker was observed. That differs from a claim of spontaneous off-topic degeneration; deliberate repeated-code count overshoots are retained separately.

## Restoration and evidence

The exact incumbent was restored and admitted. Authenticated smoke and JSON checks passed; its mode was exact. The post-12:18 kernel window recorded no Xid or OOM. Both GPU workers were proven in the incumbent cgroup; end VRAM was 95,509/95,489 MiB versus 94,505 MiB each at start, with allocation cause unisolated. No router configuration edit occurred; available readmission hash and metadata matched before and after. The initial router fingerprint is absent.

The [evidence bundle](2026-09-23-glm-spark-qwen-next-qualification-evidence/README.md) retains the native results, sanitized recipes, derived [summary](2026-09-23-glm-spark-qwen-next-qualification-evidence/summary.json), restoration record, and final manifest. Its research screen covered 219 reports; screening does not verify every external claim.
