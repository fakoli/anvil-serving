# GLM-5.3-Flash

**2026-10-25 sampling-fix trial:** the [candidate kernel gate](../../findings/2026-10-25-sampling-fix.md) failed ordinary-logit exact parity after partial forced-token success. Candidate weights and model inference never started; the earlier model reasoning-boundary failure remains unresolved. No promotion; Ignis held.

**2026-10-25 isolated reasoning-boundary diagnostic:** one C1 request on the dual RTX PRO 6000 Blackwell Max-Q incumbent failed the numeric budget transition. See the [measured outcome and limitations](../../findings/2026-10-25-glm-diagnostics.md#isolated-reasoning-boundary-follow-up). Functional diagnostic; `no-promotion`, Ignis held, runtime unchanged.

**2026-10-21–23 Flare engine investigations:** seven closed findings retain the
candidate scout, tuning, cache, state, and current-upstream diagnostics. The
result is diagnostic-only: protected and numerical gates remain incomplete or
held, the incumbent was restored after each window, and no promotion follows.
See [engine testing](../engine-testing.md) for the ordered reports.

**Current primary, 2026-10-17:** the operator selected the pinned NVIDIA
GLM-5.3-Flash FP4 recipe as the best current fit for this deployment. Its
TP2/DCP2, FP8-KV, 327,680-token/C4/MTP3 configuration is human-approved and
promoted; EXL3 r11 is retained as rollback. Direct/routed and fresh C1/C2/C4
acceptance passed. Subsequent checks accepted five Pi process combinations,
both primary Hermes CLI profiles, Companion Node/Mac OpenClaw Gateway, and
WebUI catalog reconciliation. The complete controlled A/B remains the numeric
comparison: faster C1 prefill/decode, mixed concurrent latency, and narrow GPU
headroom. Full fleet catalog/session propagation remains blocked by the older
13-target NativeWorkflows custody gap; public web publication remains pending.
[Promotion record and KV accounting](../../findings/2026-10-17-glm53-nvidia-fp4-promotion.md).

**2026-10-17 GLM serving-performance comparison:** a separate controlled serving campaign retained 12 matched EXL3 r11-control/NVIDIA-FP4 candidate pairs: 24 accepted cells, 84 requests per arm, four actual-input strata, and C1/C2/C4. All required and supplemental measurement coverage is retained in the [canonical finding](../../findings/2026-10-17-glm53-serving-performance.md), rather than repeated here. Its original 42-cell matrix closed partial after one visible-output-overrun failure and 17 stop-rule-blocked cells. The earlier NVIDIA FP4 quality/context campaign's timing-unmeasured limitation applies to that earlier quality evidence, not this later serving comparison; its 316266-token successful context result remains separate. No SWE, endurance, no-speculation, or MTP-off comparison ran, and no candidate qualified or was promoted. Exact r11 restoration passed direct and routed smokes and router readmission. Allocated KV tokens and configured 327680/C4 do not establish simultaneous full-window capacity.

**2026-10-16 SGLang scout:** the original GLM baseline C1 record completed 0/4 performance-eligible requests, so it is not a performance control. The pinned SGLang GLM profile passed native preflight 6/6 and Oracle 54/54, but its terminal SWE artifact resolved 0/5 with 2/5 officially graded. It missed the frozen 4/5 floor; later gates were deliberately stopped. [Finding](../../findings/2026-10-16-sglang-qwen-comparison.md)

**2026-10-16 v0.9.1 trial:** the R2 profile passed native preflight 6/6, Oracle 54/54, vision 18/18, bounded retrieval/tool-context, and the sampled reserve gate, then resolved 3/5 pinned SWE cases against a four-case floor. It is `rejected`/`no-promotion`. The exact restored incumbent passed direct/routed 6/6, a Pi read turn, fresh Oracle 54/54, vision 18/18, matching long-context probes, and the matched SWE suite at 3/5. The common resolved count and cases do not equate detailed grader failures or support comparative, causal, or performance claims. [Finding](../../findings/2026-10-16-glm-v091-trial.md)

**2026-10-15 replacement evaluation:** no challenger cleared the completed frozen gates. Exact GLM was restored and retained as the baseline; this is continuity evidence, not a new GLM qualification. [Finding](../../findings/2026-10-15-replacement-evaluation.md)

**Historical bounded recommendation, 2026-10-13:** The retained recommendation was the non-abliterated official-base GLM TR3 EXL3 4-bpw R11 baseline: TP2/EP2/DCP1, FP8 DS-MLA KV, 327680 configured tokens, C4, APC, no speculation, and vision 8. R19 accepted exact restoration with direct 13/13, routed tools/SSE/JSON_OBJECT and vision/OCR, Pi settled read, Hermes, OpenClaw high 9/9, and Hindsight recall/retain. Thinking-off remains failed and unified evaluation is not blanket green. The fresh fixed-five baseline is 2/5; retention does not claim a universal quality winner or a new full-context C4 measurement. [Exact configuration and evidence](../../findings/2026-10-13-full-qualification.md).

**2026-10-06 uncensored GLM scout:** the separate `lovesenko/GLM-5.3-Flash-tr3-4bpw-Abliterated@c8f58e6a` candidate is distinct from the base and CSF/QAD lanes. Its TP2/EP2/DCP1 no-spec recipe passed preflight 25/25 and agentic 18/18, then resolved 3/5 frozen SWE tasks, below the 4/5 floor. It is `not-qualified`/`no-promotion`; no capacity, context, multimodal, or speed result is claimed. Exact r11 restoration is verified: direct 25/25, routed 7/7, configuration hashes unchanged, and router readmission complete. See the [dated scout](../../findings/2026-10-06-mimo-qualification.md).

**2026-09-23 runtime investigation:** the DCP2 EXL3 configuration reproduced
an illegal-memory-access/Xid31 crash during 175K decode plus a fresh 32K
prefill, while its serial control passed. A single-setting DCP1 trial passed
three matched overlaps, with a smaller reported KV pool. It remains an
experimental mitigation; the exact baseline was restored. First-fault
attribution and broader qualification remain open. See the
[dated finding](../../findings/2026-09-23-glm-runtime-stability.md).

**2026-09-23 DCP1 qualification:** the publication-verified DCP1/batch-2,048/C4/0.97 continuation
retains bounded 201K and 310K direct observations, quality 12/12, agentic
18/18, and absolute SWE 4/5. The SWE baseline pair is environment-ineligible,
so it supports no causal score claim. An offered-C8 strict/canary diagnostic
failed 1/16, so C8 stopped. r11 was selected and deployed at that time after retained
direct/routed and native-client acceptance; independent reviews, dashboard
receipt, successful Pages publication, and HTTP/browser readback are retained.
DCP1's KV pool is 41.73% below retained DCP2,
so four full 327K windows are unsupported. See the
[working finding](../../findings/2026-09-23-glm-dcp1-qualification.md).

**Eight-image follow-up, 2026-09-18:** [GLM EXL3 r9](../../findings/2026-09-18-glm53-vision.md#eight-image-follow-up) passed four eight-image comparison requests, one eight-image high-resolution request. Limit now eight/request; no concurrent eight-image soak.


<!-- benchmark-dossier/v2 -->

## Current status and review date

**2026-10-25 Go/Rust diagnostics:** the [16K/32K Aider completion-cap diagnostic](../../findings/2026-10-25-glm-diagnostics.md) closed six completed measurements with one passing and five failing task grades; two supported cells remained unstarted and four reasoning controls were blocked. Raw grades remain separate from lifecycle and measurement eligibility. The mixed Rust result establishes no causal benefit from the larger cap. Diagnostic only; no Ignis run, broad model ranking or promotion. [Authoritative summary](../../findings/2026-10-25-glm-diagnostics-evidence/summary.json) · [Evidence index](../../findings/2026-10-25-glm-diagnostics-evidence/README.md).

**2026-10-25 R12/R13 successor:** R12 submitted 48 incumbent jobs: 44 completed with complete measurements and grades (38 pass, six fail); four cancelled grades remain unknown. The candidate was never measured. Exact incumbent restoration and explicit minimal-thinking Pi acceptance passed; historical thinking-off failure remains open. This does not change the incumbent assignment or establish a comparative ranking. [Finding](../../findings/2026-10-24-evaluation-platform-ignis.md#2026-10-25-successor-r12-baseline-and-r13-admission-refusal) · [Authoritative summary](../../findings/2026-10-25-evaluation-platform-r12-r13-evidence/summary.json).

**Historical R5–R10 diagnostic addendum, 2026-10-25:** the [evaluation-platform pilot](../../findings/2026-10-24-evaluation-platform-ignis.md) retains a censored incumbent population and separate harness canaries. Exact NVIDIA FP4 service restoration and routed preflight passed after candidate admission attempts; strict Pi final-text acceptance failed. The incumbent assignment remains unchanged. This is an incomplete pilot, not a new qualification or performance comparison.

**2026-10-05 CSF/QAD scout:** the separate pinned GLM candidate passed disabled
preflight 2/2 and HIGH preflight 25/25. Its first Oracle was 17/18 because a
structured edit returned a Markdown fence instead of JSON; the strict C4
128-word cell was 0/4 performance-eligible. The initial explicit
enabled-thinking plus `max` request conflicted before inference. A corrected
`thinking_mode=default` plus `reasoning_effort=max` Oracle derived the pinned
API's effective enabled-thinking behavior and passed 18/18. The same max
controls passed functional preflight 25/25 and the single five-task SWE scout
resolved 4/5, with only Django failed. Fresh-reload A1 then attempted five SWE
tasks: four graded, with only Sphinx and Xarray resolved; Django and Pylint
graded false, and Sympy reached the 60-step limit ungraded. The native graded
resolve rate is 2/4 = 0.5 and the campaign result is 2/5 attempts, below the
4/5 floor. The prior 4/5 scout therefore does not establish a reliable floor.
B1, B2, and A2 did not run, so there is no comparative speed result. The
candidate was `rejected`/`no-promotion`; selected r11 DCP1 remained current at that campaign close.
Its exact restoration completed with direct 25/25, routed 7/7, and a fresh Pi
read-tool turn; routes and manifest hashes were unchanged.
See the [active finding](../../findings/2026-10-05-xeon-model-qualification.md).

The candidate subsequently passed exact eight-image 4/4, context 4/4 at
247,617–247,638 actual input tokens with 65,536 maximum output, near-full 4/4
at 316,766–316,774 with 8,192 maximum output, and an offered-C4 long summary
4/4 at 165,903–165,908 actual input tokens (nominal 201,000). These are not
generated-output lengths, full 327,680-token requests, simultaneous occupancy,
or speed evidence. The failed A1 quality gate stopped the fresh-reload ABBA
plan before B1/B2/A2. An independent review follows restoration and closure;
it cannot turn these acceptance gates into capacity or speed evidence.

**2026-09-18 update:** [Bounded vision acceptance](../../findings/2026-09-18-glm53-vision.md) passed 10/10 image attempts and routed checks and a matching Pi output transcript with unchanged 4-bpw weights, FP8 KV and disabled speculation. Eight images/request; configured 327K/C4 retained. A 259,922-token synthetic retrieval passed on retry after an initial refusal; this is not a full-window soak. The following September 14 snapshot remains the prior text-only evidence.

**2026-09-19 APC campaign (historical performance lineage):** matched shared-prefix mean visible TTFT improved 67.84% and request throughput rose 3.17×; unique-prefix throughput fell 2.39%, within the 5% gate. Final diagnostic quality passed 12/12 per arm, with image and long-context gates passed. The campaign's r9 restoration and direct/routed acceptance are retained historical evidence. See the [dated finding](../../findings/2026-09-19-glm53-apc.md).

**2026-09-19 APC promotion follow-up (historical):** explicit human approval and fresh managed acceptance selected bounded r10 APC at that time. It was superseded by the publication-verified r11 DCP1 selection on 2026-09-23; r10 is the documented rollback. See the [follow-up](../../findings/2026-09-19-glm53-apc-promotion.md).

!!! info "Decision snapshot"

    - **Product role:** selected primary for this deployment: text, tools, image, and OCR.
    - **Selected or best-qualified configuration:** NVIDIA GLM-5.3-Flash FP4 r2,
      TP2/DCP2, FP8 KV, batch 4,096, 327,680 configured tokens, C4, MTP3,
      and host embeddings. The [recipe comparison](../../findings/2026-10-17-glm53-serving-performance.md#recipes-and-workload)
      retains model/image pins and reconstruction details.
    - **Measured hardware:** two RTX PRO 6000 Blackwell Max-Q cards, native Linux.
    - **Evidence:** 12 matched serving pairs and 84 scored requests per arm;
      all 17 required metric families. Fresh direct/routed/image/context and
      12 scored C1/C2/C4 acceptance requests passed; bounded Pi, Hermes,
      OpenClaw and WebUI checks are retained in the promotion record.
    - **Decision:** human-approved `current`; operator's best current fit for
      this deployment. Exact EXL3 r11 is retained as `rollback`.
    - **Important limitation:** concurrent latency is mixed; sampled free GPU
      memory reached 0.87 GiB. Strict controlled output failed both recipes.
      No new SWE/endurance/MTP-off or four simultaneous full-window result.
      The older native workflow still blocks full fleet convergence.
    - **Review dates:** evidence cutoff and dossier review 2026-10-17;
      historical R19/r11 and earlier quality evidence remain separately dated.

### Review narrative

#### 2026-09-23 — Spark challenger and r11 incumbent strict-capacity control

The Spark NVFP4 challenger resolved 2/5 attempted frozen SWE tasks; three were submitted/graded and two attempts hit the 60-call limit. It reached 16/18 Oracle7 and failed strict C4 capacity 0/16. The restored r11 incumbent resolved 3/5 and reached Oracle7 18/18, but also failed strict C4 0/16; 14 captures had wrong counts and two were length-truncated. All failed strict cells are performance-ineligible. Retain selected r11 DCP1: retention does not claim it passed this new gate. No route, client, catalog, or dashboard change followed. [Evidence](../../findings/2026-09-23-glm-spark-qwen-next-qualification.md).

#### 2026-09-17 — mixed 3.5-bpw startup failure

**Status:** failed `compatibility-only` attempt; rejected configuration, `no-promotion`. **Measured:** one managed load of pinned mixed K3/K4 EXL3 on dual RTX PRO 6000 Max-Q, TP2/DCP1, NVFP4 KV, configured 327,680 tokens/C1. Host RAM and 8 GiB swap exhausted before readiness; the desktop session shut down. **Limits:** zero candidate inference requests; no quality, context, latency, or GPU-fit conclusion. Retry requires host-memory containment and loader diagnosis. The exact selected 4-bpw baseline was restored and passed direct and routed short checks. **Evidence:** [dated finding and raw excerpts](../../findings/2026-09-17-glm53-mixed35-startup.md).

#### 2026-09-17 — bounded R7 loader fix-forward

**Status:** C1/C4 functional; experimental and `no-promotion`. **Measured:** the pinned mixed K3/K4 EXL3 candidate loaded under managed containment, passed C4 preflight and three-repetition chat/tool/intelligence scouts, and retrieved exact `ZEBRA` at 216,307 actual input tokens. One strict C4 capacity cell passed 4/4 and reported 98.17 tok/s only for that cell; a second passed 3/4 after a 65-versus-64 code-word failure. The matched baseline also passed only 2/4 after two 63-versus-64 failures. **Limits:** no overall win, full 327,680 context, C4 long-context capacity, representative repository/session/client acceptance, or repeated strict-capacity improvement. **Evidence:** [dated finding and sanitized artifacts](../../findings/2026-09-17-glm53-mixed35-fix-forward.md).

#### 2026-09-14 — selected EXL3 r7 no-spec text lane

**Status:** `quality`, `functional`, and bounded `capacity`; selected on September 14 for text only. **Measured:** 4-bpw EXL3 at 327,680 total tokens with a 65,536-token output reserve and C4. The fixed one-pass MMLU-Pro scout scored 90/100; native agentic completed 30/30; the frozen official SWE scout resolved 4/5; strict120 completed 120/120; and Pi, Hermes, and OpenClaw tool checks passed. Post-promotion context completed 9/9 at 255,647–255,672 actual prompt tokens plus the 65,536-token reserve. The one matched no-spec C4 population had 38.908 tok/s median decode. **Limits:** no exhaustive intelligence winner, broad speed ranking, full-window concurrency soak, or fresh boot/reboot proof. **Evidence:** [dated finding](../../findings/2026-09-13-intelligence-context-scout.md) and [sanitized evidence index](../../findings/2026-09-13-intelligence-context-scout-evidence/README.md).

#### 2026-09-11 — completion-budget diagnosis

**Status:** bounded functional, no-promotion. **Measured:** real Pi on native Linux, same rc14/393K/C1 backend; 4096 versus 16384 completed 6/8 versus 7/8 valid tasks. **Limits:** eight coding runs invalid; larger budget did not rescue the reasoning fixture in both repetitions. Keep the existing output default. **Evidence:** [dated finding](../../findings/2026-09-11-glm53-output-budget.md). Dossier review for this bounded result: 2026-09-11; earlier qualification dates remain separate.

#### 2026-09-11 — operator-reported daily experience, with confirmed deltas versus the previous serving

**Status:** operational narrative plus controlled-comparison deltas. **Previous
serving:** SGLang rc14 on Windows/WSL2, TP2, 393,216-token context, C1 —
deployed 2026-09-02 and serving until the native migration. **Confirmed deltas
(same rc14 image, same weights, same hardware, both sides):** per-request decode
medians rose from 112.07 → 149.02 tok/s at the 4K target (+33.0%), 96.17 →
125.03 at 120K (+30.0%), 102.42 → 124.46 at 262K (+21.5%), and 99.79 → 120.29
at 380K (+20.5%); the 60-sample endurance lane rose 102.19 → 142.64 tok/s
(+39.5%). Time-to-first-token at the 4K target fell from 0.178 s to 0.123 s
median, with the p95 pair at 0.193 s → 0.129 s; long-context TTFT stayed
prefill-bound and effectively unchanged (effective prefill ±2.3%). The
v0.4.3 runtime then added the capacity gains on top: the 524,288-token shared
pool with C4 four-request scheduling in place of C1 serialization, stock
template support for `enable_thinking=false`, and host-IPC deployment parity.
After promotion, the transition's final axis was restored: GPU peer-to-peer.
The two RTX PRO 6000 Max-Q cards share one PCIe host bridge (no NVLink on
Max-Q) and the driver reports bidirectional PCIe P2P OK; WSL2 could not use it
(the translated-IOMMU diagnosis). The 2026-09-08 comparison therefore
deliberately ran with `NCCL_P2P_DISABLE=1` on both sides — its deltas are the
OS-migration effect alone. The P2P axis was then restored and measured separately: the
2026-09-09 authorized transport A/B re-enabled NCCL P2P on the native recipe
with matched evidence (4K n=12: median TTFT −9.0%, E2E −7.4%; 120K n=3:
TTFT −8.9%) and retained it as the default. The live v0.4.3 serving adds the
FlashInfer PCIe IPC all-reduce on top of that transport (startup proof
2026-09-11 09:45 UTC, `max_numel=786432`, custom all-reduce disabled in its
favor); that layer currently runs untuned seed configurations per the engine
log, so the FlashInfer workspace tune and a dedicated IPC all-reduce
measurement are the recorded follow-up.

**Reported experience:** the served v0.4.3 configuration is the operator's
daily driver for agentic coding sessions, long-document review, and routed tool
work through the Capability Gateway, with dependable day-to-day response
quality, tool calling, and long-context behavior since the promotion gate.
The operator further reports it as probably the strongest model they have used
for this work — the first local configuration where the routine hard parts of
agentic work complete locally, with escalation to the remote lead models
becoming the exception for genuinely novel problems rather than the normal
unblocking step. This is subjective operator experience with no escalation-rate
metric behind it; it is recorded here because it is the qualification
program's purpose outcome, and it should be re-examined as v0.4.3 operational
traffic accumulates in Grafana.

**Limits:** the deltas are local whole-stack measurements from the
[2026-09-08 controlled comparison](../../findings/2026-09-08-glm53-linux-wsl-comparison.md)
— not a universal Linux speedup claim. The Grafana/Prometheus operational
scrape (started 2026-09-09 09:52 UTC) covers the final WSL2 serving window
through the 2026-09-11 07:50–08:10 UTC cutover: on real agent traffic the
WSL2 baseline recorded TTFT median 0.49 s / p95 0.53 s and an engine
inter-token gap of 6.0 ms median over about 185 scrape windows, while the
native-era live window holds only the qualification campaign's traffic
(TTFT p95 0.16 s, inter-token 5.9 ms median, about 30 minutes of samples),
so like-for-like operational era comparison needs more accumulated v0.4.3
traffic; the confirmed cross-era deltas remain the controlled A/B. Subjective
quality judgments remain operator experience; the measured record above and in
the linked dated findings remains the decision evidence for any configuration
change.

#### 2026-08-29 — initial Cardillo/Purtell qualification

The `brandonmusic/GLM-5.3-Flash-tr3-4bpw` 262K/524K campaign remains retained
historical evidence and the 262K image remains the one-week rollback. Adaptive
K1-K5 plus ReplaySSM stays rejected for tool-call corruption. The unserved
0xSero 3.0-bpw release remains watch-only.

#### 2026-08-30 — 1M optimization

The DFlash2 fixed-K5 profile with 2,048-token batching became the selected
same-model configuration in the dated campaign. The matched K3 arm remained a
verified alternate, while the 4,096-token scheduler-chunk arm was rejected.

#### 2026-08-31 — xgrammar fix-forward at 524K

The fix-forward qualification selected the exact
`wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1` target with the DFlash2 fixed-K5
draft and a corrected xgrammar runtime as the replacement default. The
published contract covers text, tools, image, and OCR at 524,288 configured
tokens, router concurrency 16, and up to 16 images. Video is unsupported. The
former 1M K5 profile remains the first rollback.

#### 2026-09-02 — pinned SGLang SM120 qualification and promotion

The exact ormandj W4A16/NVFP4 checkpoint and rc14 SGLang image were translated
from an external native-Linux recipe to WSL2 and qualified under managed
Anvil Serving lifecycle. A matched 131K adaptive-MTP/no-spec A/B showed
adaptive MTP improving decode by 39.7% at 4K and 29.2% at the 120K target,
with the 2.8% long-prefill difference inside the 3% equivalence band. The
393,216-token/C1 lane passed direct function, tools, long tool, image/OCR,
reasoning, 15/15 coding, 12/12 media, 60/60 endurance, and the full
4K/120K/262K/380K performance sweep through 304,491 measured prompt tokens.
It initially failed only the standing 3 GiB reserve policy at 2,101 MiB/card.
After the operator declared the pair model-only and waived that reserve, a
managed promotion passed direct, routed, and real Pi/OpenClaw/Hermes gates.
The exact 524K incumbent remains the immediate rollback. The 245,760/C1 lane
is a conservative verified fallback; the 499K lanes remain rejected or
unverified.

#### 2026-09-02 — isolated-worker SWE-bench smoke

An isolated macOS arm64 worker ran the fixed `django__django-11099` smoke
through the declared routed endpoint with thinking enabled. Mini-SWE-agent made 11 routed model
requests, submitted a patch, and the pinned official SWE-bench grader resolved
the instance. The run attempted, graded, and resolved 1/1 in 63.495 seconds of
worker time. This adds bounded repository-agent evidence but is not a
representative full-suite score. The campaign also fixed the managed harness's
isolated Python environment, detached Docker discovery, ambient global config,
venv validation, and missing-trajectory classification; see the
[SWE smoke finding](../../findings/2026-09-02-glm53-sglang-sm120-swe-smoke.md).

#### 2026-09-03 — concurrency and KV-capacity interpretation

A cross-run review separated scheduler ceilings from KV-resident capacity and
measured long-context concurrency. The current SGLang profile remains C1 with
a configured 393,216-token shared pool. The corrected 524K rollback reports
2,493,817 KV tokens and proved C2 at 206,630 prompt tokens/request. The
historical BrandonMusic fixed-K5 524K lane completed C16 only with 4K prompts;
its reported pool was 565,898 tokens, or 1.08 complete windows. Thus maxseq16
is not evidence for sixteen full 524K conversations. See the
[cross-run interpretation](../../findings/2026-09-03-glm53-concurrency-capacity-interpretation.md).

#### 2026-09-08 — native Linux comparison

The existing pinned SGLang profile on the same physical GPU pair showed
20.5–33.0% higher historical-style decode than retained WSL cells. The
comparison preserves variable short outputs and shared-prefix cache history;
it does not establish strict controlled performance or an OS-only speedup.
Coding, images, agentic and the identical official SWE smoke passed bounded
checks, but the SWE agent trajectory became slower. Failed strict-output,
canary, routed context/reasoning and literal image checks remain visible.
No new promotion occurred. See the
[native Linux comparison](../../findings/2026-09-08-glm53-linux-wsl-comparison.md).

## Immutable identity

The current FP4 recipe pins
`nvidia/GLM-5.3-Flash-FP4-Local@76348ddb2be2e35e39ecfc00e75dc07703f4f3d5`
and `ghcr.io/local-inference-lab/vllm:release-10152026` image digest
`sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29`.
The served name is `glm53-nvidia-fp4-local-tp2-327k-c4-mtp3-r2`.
[Exact recipe comparison](../../findings/2026-10-17-glm53-serving-performance.md#recipes-and-workload).
The identities below are historical lanes, including the retained EXL3 rollback.

The rejected 2026-10-06 uncensored lane is separately pinned to
`lovesenko/GLM-5.3-Flash-tr3-4bpw-Abliterated@c8f58e6aa9117c73607d692978b22f091d80450c`.
Its [public reconstruction](../../findings/artifacts/2026-10-06-mimo-qualification/configs/glm53-abliterated-qualification.toml)
retains the base EXL3 runtime image; edited weights do not inherit the base model's qualification.

The selected 2026-09-14 text lane retains `brandonmusic/GLM-5.3-Flash-tr3-4bpw`
at `a5fee929cf4888b1824323e33e8a19b60129e025` and
`verdictai/glm53-flash-exl3-k4:r19-sm120-tp2-ep2-dcp2-v84-language-only@sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692`.
See the [native promoted-context artifact](../../findings/2026-09-13-intelligence-context-scout-evidence/native/glm-r7-nospec-promoted-context258k-r1/artifact.json)
and [configuration identity](../../findings/2026-09-13-intelligence-context-scout-evidence/configuration-identity.json).

The selected native v0.4.3 profile uses ormandj revision
`c3cbb9891b67c741bcbf6b176dd7af9265b069db`, image
`sha256:ec4243f940a179a27fea21895077efd47cd050501f99a1d2a5fecf7df2e7be71`,
served identity `glm-5.3-flash` and derived template SHA256
`3406cc91800b56c06439e65aaea1929e6bc803cdb62f7f995f25ab082efd3408`.
[Full configuration](../../findings/2026-09-11-glm53-v043-native-linux-qualification-evidence/identity.json).
The remaining identities in this section are retained historical profiles.

- Target: `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1`
- Target revision: `319d66a8b53092b491f698440ecea781e4ddd4e4`
- Draft: `incoai/GLM-5.3-Flash-DFlash2`
- Draft revision: `dc77ff1c99eeb2df044ee3d4f0094eb033fee410`
- Runtime image:
  `sha256:4909e318ba1348a179824e210f90c268d6fc68e8b4e514af4782e26e6a1e5939`
- Runtime source base: `vllm-project/vllm@487ecf187`, with the hash-gated
  xgrammar reasoning-end/speculative-validation patch under
  `configs/runtime-patches/vllm/487ecf187-xgrammar-spec-reasoning-end/`
- Runtime-reported vLLM: `0.1.dev20051+g487ecf187`
- Served identity:
  `glm53-flash-exl3-k3-dflash2-k5-fp8-tp2-524k-vision-xgfix`
- Quantization: EXL3/MCG K3 routed experts with native
  attention/shared/vision/MTP tensors; FP8 DS-MLA target KV; BF16 DFlash2
  draft KV

The 2026-09-02 current SGLang profile has a separate immutable identity:

- Target: `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO`
- Target revision: `c3cbb9891b67c741bcbf6b176dd7af9265b069db`
- Runtime image:
  `ghcr.io/ormandj/sglang-glm53-flash-sm120@sha256:0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`
- Runtime source label: `ormandj/sglang-glm53-flash-sm120@a547c90`
- SGLang source label: `sgl-project/sglang@4c2c169`
- Served identity:
  `glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp`
- Quantization: ModelOpt W4A16/NVFP4 K32 experts, FP8 weight-only dense
  tensors, FP8 KV, and adaptive EAGLE speculative decoding

The target, draft, and runtime are third-party artifacts. The DFlash2 draft is
CC-BY-NC-ND-4.0, so the combined recipe is evaluation/noncommercial unless
separate permission is obtained.

## Tested hardware and topology

The current FP4 comparison and promotion acceptance measured the same native
Linux dual-card TP2 host. Host embeddings are enabled, so this is not strictly
GPU-only residency. Measured GPU/cgroup/host memory and concurrency are retained
in the [full comparison](../../findings/2026-10-17-glm53-serving-performance.md).
The following dated topology descriptions do not redefine that current recipe.

The 2026-09-08 native Linux comparison measures the same physical dual-PRO
pair at TP2/393,216/C1. It preserves the exact SGLang image and checkpoint,
adds `NCCL_P2P_DISABLE=1`, and retains the existing compatibility patches.
OS, driver/container stack and cache history differ from the historical
baseline. The reconstruction boundary is recorded in the
[comparison configuration](../../findings/2026-09-08-glm53-linux-wsl-comparison.md#exact-configuration-and-comparison-boundaries).

The following topology describes the retained EXL3/WSL qualification lane.


Two RTX PRO 6000 Blackwell Max-Q cards, 96 GB each, under Windows 11,
Docker Desktop, and WSL2. They are assigned exclusively to one TP=2/DCP=2
owner over PCIe without NVLink. Aggregate VRAM is 192 GB, not unified memory.
The selected profile uses the V2 runner, B12x sparse MLA attention,
2,048-token batching, maxseq16, prefix caching, the visual tower, and
524,288 configured context.

CUDA-IPC peer access is unavailable in this WSL2 topology. The qualified
translation uses PyNCCL and disables custom PCIe all-reduce, B12X DCP A2A,
and top-k owner exchange while preserving EXL3 K3 compute, sparse B12X MLA,
FP8 target KV, and the DFlash2 draft.

## Engine, quantization, KV, context, and concurrency recipe

| Recipe | Role | Context | Decision |
|---|---|---:|---|
| NVIDIA FP4 r2 ([recipe and pins](../../findings/2026-10-17-glm53-serving-performance.md#recipes-and-workload)) | selected primary; TP2/DCP2, FP8 KV, MTP3 | 327,680, C4 | `current`; human-approved |
| EXL3 r11 DCP1 ([retained control](../../findings/2026-10-17-glm53-serving-performance.md#recipes-and-workload)) | exact retained rollback; FP8 KV, APC, no speculation | 327,680, C4 | `rollback` |
| EXL3 r7 no-spec (public-safe configuration in [dated finding](../../findings/2026-09-13-intelligence-context-scout.md)) | historical text-only reference | 327,680 total / 65,536 output, C4 | historical selection; bounded evidence |
| [SGLang W4A16 adaptive MTP](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml) | historical 2026-09-02 text/image/OCR selection | 393,216 | `verified`, historical |
| [DFlash2 K5, corrected xgrammar](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-k5-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml) | historical rollback | 524,288 | `verified`, historical |
| [SGLang W4A16 adaptive MTP, conservative](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-240k-c1-adaptive-mtp-recipe.toml) | conservative fallback | 245,760 | `verified`, fallback |
| [Matched no-speculation control](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-nospec-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml) | reliability/performance control | 524,288 | `verified`, control |
| [Former DFlash2 K5, batch 2,048](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml) | first rollback | 1,048,576 | historical `verified`, rollback |
| [DFlash2 K3, batch 2,048](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-exl3-k3-dflash2-k3-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml) | former high-concurrency alternate | 1,048,576 | historical `verified` |
| K5, batch 4,096 | matched scheduler-chunk trial | 1,048,576 | `rejected`; no durable recipe |
| [TR3 vision fixed K5](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-vision-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) | prior interactive profile and image rollback | 262,144 | historical `verified` |
| [TR3 no spec](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-nospec-sm120-tp2-524k-wsl2-v2-no-owner-exchange-recipe.toml) | prior maximum-context/headroom lane | 524,288 | historical `verified` |

The historical EXL3 r7 no-spec lane admitted C4 at 327,680 total tokens with a
65,536-token output reserve. It completed 9/9 post-promotion context cases at
255,647–255,672 actual prompt tokens plus reserve; this does not establish
concurrent full-window capacity. See the [dated finding](../../findings/2026-09-13-intelligence-context-scout.md).

### Historical capacity profiles

The corrected 524K engine reported 19.18 GiB of available KV memory per rank
and 2,493,817 KV tokens, or 4.76 complete configured windows. Router c16 is a
scheduling ceiling for short requests, not proof of sixteen simultaneous 524K
prompts. The measured long-context concurrency gate is C2 at a nominal 250K
target. The locally qualified media contract is up to 16 images and zero
videos.

The historical SGLang profile uses C1, 4,096 maximum output tokens, adaptive
EAGLE, FP8 KV, and the multimodal visual tower. At 393,216 configured tokens
it retained 2,101 MiB free on each card after qualification and 2,543 MiB after
the promotion's routed/client work. The operator explicitly waived the
standing 3,072 MiB reserve for this model-only pair without waiving runtime
safety evidence. The 245,760 profile retained 3,487 MiB/card and remains the
conservative fallback. The 499,712/C4 profile is rejected and the C1 variant
is unverified.

For planning only, the historical 393,216-token shared pool divides to 196,608
total tokens/request at C2 and 98,304 at C4 before output, media, protocol, and
runtime headroom. A first C2 text gate at 180,000 prompt + 4,096 output per
request would leave 25,024 tokens in the shared pool. This is not qualified:
that historical recipe also fixes one running request and batch-size-one decode
graphs, so increasing concurrency requires a managed candidate load and the
full functional, capacity, quality, endurance, and post-workload VRAM gates.

## Evidence by measurement class

### Split-top-p patch diagnostic, 2026-10-25

- **Status:** partial functional kernel result; failed preweight gate, `no-promotion`.
- **Measured:** original/patched synthetic logits on one RTX PRO 6000 Blackwell Max-Q, CUDA device 0; exact case counts are in the [authoritative summary](../../findings/2026-10-25-sampling-fix-evidence/summary.json).
- **Limits:** the fourth ordinary-input mask comparison failed. No candidate model weights, inference, serving-worker boundary trace, peak-allocation pass, or timing comparison. Planned TP2/C4/context settings were not exercised.
- **Evidence:** [dated finding](../../findings/2026-10-25-sampling-fix.md), [raw excerpt and case ledger](../../findings/2026-10-25-sampling-fix-evidence/README.md), and [restoration record](../../findings/2026-10-25-sampling-fix-evidence/restoration.json). Preserve the exact-parity failure; obtain a reference probability/mask oracle before changing the gate.

### Go/Rust completion-cap diagnostics, 2026-10-25

- **Scope:** pinned Aider/Polyglot Go and Rust tasks at 16K/32K completion caps; diagnostic-only, no promotion.
- **Measurement boundary:** a retained raw task grade does not make a cancelled or incomplete job a completed measurement. Missing usage, finish reasons and compiler signals remain unknown; diagnostic absence does not establish a negative result.
- **Evidence:** [dated finding](../../findings/2026-10-25-glm-diagnostics.md) and [evidence index](../../findings/2026-10-25-glm-diagnostics-evidence/README.md). All six submitted jobs have verified closure; three additional request starts lack captured responses and retain unknown usage. Historical R12 accepted populations remain unchanged.

### R12 baseline and R13 admission refusal, 2026-10-25

- **Status:** bounded functional/quality baseline, `no-promotion`; no candidate comparison.
- **Measured:** 44 complete incumbent measurements/grades and four cancelled unknowns on the dual-card FP4 recipe, across fixed C1/C4 task cells. All released shared batch closures verified; active wall includes drain and telemetry finalization.
- **Limits:** native tool cases include synthetic results; contained Aider tests supply the executable coding oracle. Fixed order, uncontrolled cache, unknown request overlap and partial cancellation output prevent broader quality, concurrency or performance claims. Minimal-thinking Pi acceptance does not accept thinking-off/default behavior.
- **Evidence:** [Authoritative summary](../../findings/2026-10-25-evaluation-platform-r12-r13-evidence/summary.json), [index](../../findings/2026-10-25-evaluation-platform-r12-r13-evidence/README.md), [manifest](../../findings/2026-10-25-evaluation-platform-r12-r13-evidence/artifact-manifest.json), [finding](../../findings/2026-10-24-evaluation-platform-ignis.md#2026-10-25-successor-r12-baseline-and-r13-admission-refusal). Sanitized derivatives retain distinct original-private and public-byte hashes.

### Evaluation-platform diagnostic, 2026-10-24–25

- **Status:** partial incumbent evidence; pilot acceptance incomplete, `no-promotion`.
- **Measured:** R8 has 42 complete measurement/grade pairs (32 pass, ten fail) on the pinned NVIDIA FP4 TP2 recipe. The separate repaired mixed-C4 canary has four complete measurements, three task passes and one valid Go failure.
- **Limits:** six other submitted R8 rows have unknown grades or incomplete cancellation; blocked final interval and total active time remain unknown. No Ignis generation ran, so there is no paired deployment, speed or engine comparison. Real Pi tools/provider checks passed after restoration, but strict final text failed.
- **Evidence:** [population boundaries, configuration, failures and restoration](../../findings/2026-10-24-evaluation-platform-ignis.md); prior campaigns and four C++ canaries are separate denominators.

### Current NVIDIA FP4 r2, 2026-10-17

**Status:** `functional`, bounded `capacity`; human-approved `current`.
**Measured:** the complete 17-family comparison and fresh direct/routed/image,
near-limit, C1/C2/C4, and bounded native-client acceptance are retained.
**Limits:** mixed concurrent latency, narrow GPU headroom, failed controlled
output on both arms, and incomplete fleet/session propagation. Older SWE
scores below do not transfer to this new configuration.
**Evidence:** [promotion and KV accounting](../../findings/2026-10-17-glm53-nvidia-fp4-promotion.md),
[comparison and native sources](../../findings/2026-10-17-glm53-serving-performance.md).

### Selected EXL3 r7 no-spec, 2026-09-14

**Status:** `quality`, `functional`, bounded `capacity`; September 14 text-only reference.
**Measured:** MMLU-Pro 90/100 one pass, agentic 30/30, frozen SWE 4/5,
strict120 120/120 at C4, and context 9/9 at 255,647–255,672 prompt tokens plus
65,536 reserve. **Limits:** one 38.908 tok/s C4 median, no exhaustive
intelligence ranking, full-window soak, or fresh boot/reboot proof.
**Evidence:** [finding and sanitized evidence](../../findings/2026-09-13-intelligence-context-scout.md).

### Native Linux comparison, 2026-09-08

**Status:** `functional`, `capacity`, bounded `quality`; `no-promotion`.
**Measured:** warm direct C1/n3 shared-prefix, variable-short-output decode
149.02/125.03/124.46/120.29 tok/s at 4K/120K/262K/380K targets, versus WSL
112.07/96.17/102.42/99.79; long prefill approximately ±2.3%; endurance
60/60 at 142.64 versus 102.19 tok/s. Coding 15/15, image corpus 12/12,
agentic 30/30, official SWE smoke 1/1.
**Limits:** strict output 0/3 and unique canaries 1/10 excluded; no finalist
performance qualification. SWE agent time 115.69 versus 34.22 seconds,
22 versus 11 requests. Routed 380K admission and reasoning evidence failed;
direct image phrase sensitivity also failed. No OS-only causal claim.
The extended default-thinking context suite passed 128/150 through 376,484
actual prompt tokens. Nominal 8K/32K/131K/262K/380K buckets passed
27/18/24/30/29 of 30; failures were 9 empty length-terminated responses and
13 incorrect visible answers. The non-monotonic curve means the native
threshold-derived `effective_context=8192` is not a physical cap. No matched
Windows suite exists, so this is not a Linux-caused quality regression.
**Evidence:** [context summary](../../findings/2026-09-08-glm53-linux-wsl-comparison-evidence/context-summary.json);
[dated finding and native artifacts](../../findings/2026-09-08-glm53-linux-wsl-comparison.md).

### Retained EXL3 and SGLang qualification history


- `functional`: both matched arms passed 28/28 direct observations, including
  strict JSON, 206,296-actual-token retrieval, tools 20/20, a structured tool
  after the long prompt, streaming tools, tool-result continuation, and
  Responses. The selected arm also passed image understanding and OCR.
- `capacity`: C2 at a nominal 250K target / 206,630 actual prompt tokens per
  request completed 2/2 with an 8,192-token API completion allowance; the
  engine reported 2,493,817 KV tokens.
- bounded `quality`: intelligence 6/6, session 3/3, tools 3/3, and the 4K,
  131K, and 240K context cases passed with no retained failure.
- `performance`: the matched DFlash2 arm measured 83.08 tok/s at 4K and a
  pooled 69.99 tok/s across two five-request 240K runs, versus 42.61 and 43.63
  for no speculation. The two 240K run-level medians were 64.51 and 70.50.
- `concurrency`: the selected profile completed the measured 250K-class C2
  gate 2/2. Router c16 remains a short-request scheduling ceiling.
- multimodal: direct image understanding and verbatim OCR passed; up to 16
  images are admitted. Video remains disabled.
- client acceptance: real OpenClaw and Hermes shell-tool continuations passed
  with exact identity and no fallback. Pi 0.84.2's initial normal
  extension-loaded PTY path made exactly one `read` call, recovered an unseen
  marker, and emitted zero error events; the goal-closure recheck on Pi 0.84.4
  retained 524,288/8,192 and passed another real PTY tool nonce.

For the 2026-09-02 SGLang profile, thinking-disabled preflight passed all
ten observations, tools passed 20/20, and long needle/tool cases
passed, and image understanding/OCR passed. Thinking-enabled smoke, JSON,
tools 5/5, tool-result continuation, and Responses passed with dedicated
reasoning. Bounded coding passed 15/15 at a 2,048-token visible budget, the
media corpus passed 12/12, and endurance passed 60/60. The three-run capacity
sweep measured 112.07/96.17/102.42/99.79 tok/s decode and
16,729/5,749/5,579/5,457 tok/s effective prefill at nominal
4K/120K/262K/380K targets respectively. Managed direct and routed gates plus
real Pi/OpenClaw/Hermes acceptance passed after promotion.

### Matched local comparison

| Requested context | Matched no spec | Corrected DFlash2 K5 | Former 1M K5 | Current vs no spec | Current vs former 1M |
|---:|---:|---:|---:|---:|---:|
| 4K | 42.61 tok/s | **83.08 tok/s** | 82.1 tok/s | **+95.0%** | +1.2% |
| 240K | 43.63 tok/s | **69.99 tok/s pooled** | 67.9 tok/s | **+60.4%** | +3.1% |

The 240K selected value pools ten requests across two retained runs; their
run-level p50 values were 64.51 and 70.50 tok/s. This comparison is local to
the exact derived runtime and dual-Max-Q/WSL2 topology; it is not a general
model-intelligence ranking.

## Decision and promotion state

As of 2026-10-17, human-approved NVIDIA FP4 r2 is the selected primary and
EXL3 r11 is retained rollback. The operator considers it the best current fit
for this deployment, supported by the bounded comparison and acceptance above.
The existing native workflow remains `recovery_required`; full fleet catalog,
existing-session, and public-web closure are separate incomplete gates.
All selections in the following paragraphs are historical.

The 2026-09-14 EXL3 r7 no-spec lane was the selected September 14 text-only reference.
It does not establish an exhaustive intelligence winner, a general performance
ranking, or full-window concurrent capacity. The 2026-09-08 migrated-stack
measurement is `no-promotion`; all earlier selections below are retained
historical qualification and promotion decisions.


The 393K/C1 SGLang adaptive-MTP profile was the selected published text/image/OCR
default in its dated record. The corrected 524K K5 profile was its immediate
exact rollback; it retains measured C2 headroom at a 250K-class prompt and its
corrected structured-output behavior. Raising its batching to 4,096 remains rejected.

The historical SGLang contract advertises 393,216 context, 4,096 maximum output,
C1, and image/OCR without video. During its recorded promotion,
`llm.primary`, `llm.secondary`, `llm.auxiliary`, `llm.voice`,
`vision.general`, and `vision.ocr` select the same exact service during this
evaluation. Qwen3.8 Flash Next remains the retained video-capable rollback.
The 524K EXL3/DFlash2 profile is its historical same-model rollback, and the
245,760/C1 SGLang lane is its conservative same-engine fallback.

## Failures and gotchas

- **Current FP4 limits (2026-10-17):** strict controlled output failed on both
  comparison arms; natural/edge timing populations remain separate. Concurrent
  latency is mixed and sampled free VRAM reached 0.87 GiB. No MTP-off, new full
  SWE/endurance, full-window C4, or fresh reboot result is retained. The
  [promotion record](../../findings/2026-10-17-glm53-nvidia-fp4-promotion.md)
  retains client capture/policy failures and the native fleet recovery blocker.

- **Historical EXL3 r7 limits (2026-09-14):** no exhaustive intelligence comparison,
  full-window concurrency soak, or fresh boot/reboot test is retained. The 38.908 tok/s
  C4 no-spec median is one matched population, not a general speed claim.

- **Native comparison limits (2026-09-08):** strict controlled output passed
  0/3; unique natural canaries 1/10. Both populations are excluded. Routed
  380K text fails secondary media admission with HTTP 413; routed reasoning
  checks lack the required dedicated channel despite correct answers.
  Corresponding direct gates pass. One direct general-image literal phrase
  check fails on table formatting despite the separate 12/12 image corpus.
  These are retained failures, not relaxed validators or new qualifications.


- DFlash2's published license is noncommercial/no-derivatives. Obtain separate
  permission before commercial use.
- The target/draft/runtime are community artifacts, not stock-vLLM support.
- The original DFlash2 runtime failed structured generation after reasoning
  termination. Only the digest-pinned corrected xgrammar image is qualified.
- Video is unsupported; the DFlash2 drafter receives text-only inputs on image
  calls while the target processes the image.
- WSL2 peer IPC failed, requiring the qualified PyNCCL transport translation.
- The runtime suggested a larger fixed KV pool, but it was not A/B tested; the
  selected 0.95 utilization retains measured operating reserve.
- No missing MoE/GEMM tune warning appeared. First-request JIT warnings are
  warm-up observations, not evidence that a kernel tune would help.
- The high-control quality request was accepted but independent token-level
  reasoning telemetry was unavailable; the artifact says
  `requested_unverified`.
- The rollback profile needs its already-qualified visible/reasoning budget;
  an artificially small visible cap can end in reasoning without an answer.
- No exact Docker-image removal product surface exists. The previous GLM image
  remains the intended one-week rollback; no broad prune was used.
- The rc14 SGLang image required three narrow WSL2 fix-forwards:
  `NCCL_CUMEM_ENABLE=0`, disabling expandable-segment allocation, and a
  hash-gated fallback from the symmetric-memory logits gatherer to ordinary
  NCCL. The derived chat template is also hash-gated so thinking-disabled
  requests do not leak internal reasoning.
- The 393K/C1 profile's original reserve-policy failure was reclassified under
  the recorded model-only waiver; the physical 2,101 MiB/card measurement is
  unchanged. The 499K/C4 lane is rejected and the 499K/C1 lane is unverified.
- Sparse-MLA CPB calibration rejected implausible fitted results and retained
  the engine heuristic. Optional NCCL plugin warnings were benign. No kernel
  tune was stored because no exact default-versus-tuned end-to-end A/B showed
  an improvement.

## Dated run history

- 2026-10-25 — [Sampling-fix kernel trial](../../findings/2026-10-25-sampling-fix.md): candidate stopped before model weights after ordinary-mask parity failure; partial forced-token correction does not prove the end-to-end boundary fix.

- 2026-10-25 — [R12/R13 successor](../../findings/2026-10-24-evaluation-platform-ignis.md#2026-10-25-successor-r12-baseline-and-r13-admission-refusal): R12 retained complete graded incumbent results and clean shared closure; R13 supplied no candidate measurement. Exact restoration and minimal-thinking Pi accepted, historical thinking-off failure open.
- 2026-10-24–25: [Evaluation-platform diagnostic pilot](../../findings/2026-10-24-evaluation-platform-ignis.md) — partial incumbent grades, accepted bounded mixed-C4 closure, candidate unmeasured; service restored with strict Pi acceptance incomplete.
- **2026-10-17 — NVIDIA FP4 promotion and serving comparison:** selected primary
  with exact EXL3 r11 retained rollback; 12 matched pairs/84 scored requests per
  arm, fresh functional/capacity/native-client acceptance, KV accounting, and
  remaining fleet blocker. [Promotion](../../findings/2026-10-17-glm53-nvidia-fp4-promotion.md)
  · [comparison](../../findings/2026-10-17-glm53-serving-performance.md).

- **2026-10-13 — Full qualification closure:** The official-base GLM continuity baseline completed fixed-five at 2/5. C1/C4 controlled-output cells retained actual prompt means 25,280.5, 25,281.125, 106,520, and 216,194.67. Mixed TP2 no-spec/thinking-off diagnostics were incomplete at 1/5. The last-good non-abliterated official-base lineage is the restoration choice; `no-promotion`. [Finding](../../findings/2026-10-13-full-qualification.md)

| Date | Event | Result |
|---|---|---|
| 2026-10-06 | Separate abliterated TR3 4-bpw scout | Preflight 25/25 and agentic 18/18; all five SWE submissions graded, 3/5 resolved, below the 4/5 floor. Django/Pylint patch-quality failures; no speed/context/C4/modality qualification. `not-qualified`/`no-promotion`; exact r11 restored with direct 25/25 and routed 7/7. [Finding](../../findings/2026-10-06-mimo-qualification.md) |
| 2026-10-05 | CSF/QAD prospective qualification scout | Max functional 25/25, Oracle 18/18, initial SWE 4/5, then fresh-reload A1 SWE 2/5 attempts and 2/4 graded (Sphinx/Xarray resolved; Django/Pylint false; Sympy ungraded at 60 steps); exact eight-image/context/offered-C4 summaries 4/4. Strict C4 is 0/4 performance-eligible. A1 misses the 4/5 floor, so B1/B2/A2 did not run and no speed comparison exists. Candidate rejected; exact r11 restored with direct 25/25, routed 7/7, and fresh Pi read-tool acceptance. [Finding](../../findings/2026-10-05-xeon-model-qualification.md) |
| 2026-09-26 | Single-GPU challenger qualification | Exact r11 was the verified 4/5 frozen-SWE reference. Flash r3 and Swift27 r2 failed the floor; ThinkingCap's attainable upper bound was 3/5 with partial coding evidence only. Exact r11 was restored and readmitted; no alias changed. [Finding](../../findings/2026-09-26-single-gpu-qualification.md) |
| 2026-09-14 | Intelligence and context qualification, EXL3 r7 no-spec | Selected September 14 text-only lane: MMLU-Pro 90/100 one pass, agentic 30/30, frozen SWE 4/5, strict120 120/120, context 9/9 at 255,647–255,672 prompt tokens plus 65,536 reserve, and Pi, Hermes, and OpenClaw tool checks pass; one C4 no-spec population 38.908 tok/s median; no exhaustive intelligence, full-window soak, or fresh boot/reboot claim; [finding and evidence](../../findings/2026-09-13-intelligence-context-scout.md). |
| 2026-09-09 | ormandj v0.4.2 runtime qualification | Bounded direct gates and matched capacity improve, but strict turnover is 58/60 then 57/60; user selected `retain-baseline/no-promotion` and the exact baseline was restored; [finding](../../findings/2026-09-09-glm53-ormandj-v042.md) |
| 2026-09-09 | Native NCCL P2P transport A/B | User-authorized native default retains P2P enabled after direct/routed gates and bounded 4K n12/120K n3 latency evidence; both 380K strict-output cells and the raw PowerShell diagnostic marker failure remain explicit; [finding](../../findings/2026-09-09-glm53-native-nccl-p2p.md) |
| 2026-09-08 | Native Linux versus retained WSL on the same dual-card GLM image/model | C1/n3 historical-style decode +20.5–33.0%; bounded quality and SWE smoke pass, extended context 128/150 (9 empty, 13 incorrect), strict-output/canary and routed failures retained; whole-stack comparison, `no-promotion`; [finding](../../findings/2026-09-08-glm53-linux-wsl-comparison.md) |
| 2026-09-03 | Cross-run scheduler, KV-capacity, and measured-concurrency reconciliation | Current SGLang remains qualified at 393K/C1; corrected 524K rollback retains measured C2 at 206,630 prompt tokens/request; historical BrandonMusic C16 is short-request evidence, not full-window concurrency; [interpretation and artifact links](../../findings/2026-09-03-glm53-concurrency-capacity-interpretation.md) |
| 2026-09-02 | Isolated-worker SWE-bench Verified smoke and harness fix-forward | Fixed `django__django-11099` attempted 1/1, officially graded 1/1, and resolved 1/1 through 11 routed requests; one-instance smoke only; [finding and sanitized evidence](../../findings/2026-09-02-glm53-sglang-sm120-swe-smoke.md) |
| 2026-09-02 | Human-approved 393K/C1 reserve reclassification, managed promotion fix-forward, routed gates, and real-client acceptance | Published `current` text/tools/image/OCR profile; 304,491 actual prompt tokens, 112.07/96.17/102.42/99.79 tok/s at 4K/120K/262K/380K targets, direct and routed gates, Pi/OpenClaw/Hermes pass, 2,543 MiB/card after client work, exact 524K rollback retained; [promotion finding](../../findings/2026-09-02-glm53-sglang-sm120-393k-promotion.md) |
| 2026-09-02 | Pinned ormandj SGLang SM120 translation, WSL2 fix-forward, matched adaptive/no-spec A/B, 240K full qualification, larger-envelope feasibility, endurance, and exact incumbent restoration | 245,760/c1 adaptive MTP selected as a verified challenger with no promotion; 108.57/93.35/95.00 tok/s decode at 4K/120K/230K, quality 15/15, media 12/12, endurance 60/60, and 3,487 MiB/card post-workload reserve; [finding and raw artifacts](../../findings/2026-09-02-glm53-sglang-sm120-qualification.md) |
| 2026-08-31 | xgrammar fix-forward image, matched 524K no-spec/DFlash2 A/B, C2 250K-class gate, rollback drill, router, and real-client forward restore | corrected DFlash2 K5 selected at 524K; 83.08 tok/s at 4K and pooled 69.99 at 240K; C2 2/2; full [finding and raw artifacts](../../findings/2026-08-31-glm53-xgrammar-524k-qualification.md) |
| 2026-08-30 | Current-source refresh, K5/K3/batch4,096 A/B, 4K-500K performance, 950K retrieval, image/OCR, quality, router, and real-client promotion | K5/batch2,048 selected as `current` one-week default; K3 verified alternate; batch4,096 rejected; full [finding and raw artifacts](../../findings/2026-08-30-glm53-k3-dflash2-1m-optimization.md) |
| 2026-08-29 | Cardillo/Purtell translation, adaptive/fixed/no-spec A/B, vision/OCR, 250K and near-500K capacity | TR3 vision fixed K5 and no-spec 524K qualified as challengers; adaptive MTP rejected; 0xSero 3.0-bpw watch-only; [historical finding](../../findings/2026-08-29-glm53-cardillo-purtell-qualification.md) |

### 2026-10-16 comparison baseline

The frozen natural-output baseline is documented in [the dated finding](../../findings/2026-10-16-glm-natural-baseline.md). It does not establish coding repeatability and changes no recommendation or promotion state.
