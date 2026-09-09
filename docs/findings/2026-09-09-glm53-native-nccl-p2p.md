# GLM-5.3-Flash native NCCL P2P transport A/B

**Date:** 2026-09-09

**Decision:** The user-authorized native GLM default retains `NCCL_P2P_DISABLE=0`. This is a bounded P2P transport decision for the pinned TP=2/C1 profile, not a global NCCL, Windows/WSL, BIOS, ACS, model, image, or client-contract change.

<!-- benchmark-result-card/v1 -->
## Result card

| Setup | Qualified value |
|---|---|
| Model/runtime | `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO@c3cbb9891b67c741bcbf6b176dd7af9265b069db`; pinned SGLang image `0c063795` |
| Hardware | Two RTX PRO 6000 Blackwell Max-Q GPUs; native Linux TP=2, 393,216 tokens, C1 |
| Delta | `NCCL_P2P_DISABLE=1` to `0`; cuMem remains `0`, adaptive MTP remains fixed |
| 4K target | 2,970 actual prompt tokens p50; n=12 per arm: median TTFT -9.0%, E2E -7.4%, decode +2.1% |
| 120K target | 97,159 actual prompt tokens p50; n=3 per arm: median TTFT -8.9%, E2E -8.9%, decode +1.4% |
| Evidence | [manifest](2026-09-09-glm53-native-nccl-p2p-evidence/artifact-manifest.json) · [index](2026-09-09-glm53-native-nccl-p2p-evidence/README.md) |

**Why it matters:** the selected native recipe now uses its observed P2P/IPC paths on both TP directions, with lower median request latency in the two eligible matched cells.

**Important caveat:** this is fixed 32-word, unique-prefix C1 evidence with partial cache metadata. It has no p99, service-tail, broad-throughput, or high-context performance claim.

## Gates and retained failures

Both arms passed the ten-check direct preflight. Both passed exact 380K-target retrieval and a long-tool assertion; the candidate passed routed smoke and JSON after readmission. The 380K capacity cell in each arm returned 33 rather than 32 code words at 304,588 actual prompt tokens, so neither timing is performance-eligible. The initial 128-word scout also failed before the shared 32-word protocol was adopted.

The candidate PowerShell-plan case has a raw 1/2 pass rate. Independent Sol review found a demonstrable keyword-validator false negative; the raw failure is unchanged and the diagnostic is not executed-code or broad quality proof. See [adjudication](2026-09-09-glm53-native-nccl-p2p-evidence/diagnostic-adjudication.json).

## Evidence boundary

NCCL startup reported `2.30.7+cuda13.3` and P2P/IPC in both directions without an SHM path; see the [sanitized observation](2026-09-09-glm53-native-nccl-p2p-evidence/candidate/nccl-transport-observation.md). The candidate long-tool gate passed at 320,794 actual prompt tokens. Restoration retained the authorized new default, same container identity, active enabled owner, zero restarts, clean error scan, and routed smoke/JSON pass. [Restoration evidence](2026-09-09-glm53-native-nccl-p2p-evidence/restoration.json) does not authorize any broader promotion.
