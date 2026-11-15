# 2026-10-15 replacement evaluation

**Decision:** `no-promotion`. The retained GLM control, MiMo, and Mistral lanes
completed without producing a qualified replacement. DeepSeek V4.1 Flash has
bounded Stage 3 dummy-path and Stage 4 real-weight diagnostic evidence. Its
full-role r2 and r3 configurations failed the frozen reserve, and r4 failed the
first required Oracle8 reliability round despite passing its resource gate. None
is a qualification result. Exact GLM was restored and accepted through the
retained direct, routed, and actual-Pi checks. The permanent alias and backing-model
selection remain unchanged; restoration is separate from model selection.

**Evidence:** [public evidence index](2026-10-15-replacement-evaluation/README.md) · [artifact manifest](2026-10-15-replacement-evaluation/artifact-manifest.json) · [authoritative summary](2026-10-15-replacement-evaluation/summary.json) · [public configuration reconstruction](2026-10-15-replacement-evaluation/configuration-reconstruction.json).

<!-- benchmark-result-card/v1 -->
## Result card

> On the dual RTX PRO 6000 qualification configuration, GLM, MiMo, and Mistral
> did not clear their frozen replacement gates. DeepSeek has bounded dummy-path
> and real-weight diagnostics. Full-role r4 missed its first required Oracle8
> reliability round; no DeepSeek lane is qualified.

| Setup | Qualified value |
| --- | --- |
| Model | GLM r11 control; MiMo MOPD r4/r5; Mistral Medium 3.5 r2/r3; DeepSeek V4.1 Flash Stage 2/3/4 and full-role r2/r3/r4 |
| Hardware | two RTX PRO 6000 Blackwell Max-Q GPUs for TP2 DeepSeek diagnostics and the other replacement lanes; one GPU for DeepSeek Stage 2 |
| Runtime | direct online protocol, Oracle, and fixed-five coding gates; separately pinned numerical, dummy-path, and bounded real-weight diagnostics |
| Recipe | [public reconstruction](2026-10-15-replacement-evaluation/configuration-reconstruction.json) |
| Measurement path | direct online for completed GLM/MiMo/Mistral gates; one-GPU numerical runtime for DeepSeek Stage 2; bounded dummy and real-weight gates for DeepSeek |
| Contract | configured contexts, concurrency, and image limits do not establish full-role capacity, quality, throughput, or promotion |
| Evidence | retained terminal evidence through full-role r4 and GLM restoration |
| Decision | `no-promotion`; permanent alias/backing selection unchanged |

| Headline measurement | Local result | Conditions |
| --- | --- | --- |
| Replacement gate | no completed lane qualified a challenger | retained gate outcomes in the authoritative table below |
| DeepSeek Stage 3 r12 | bounded dummy-path integrity, health, configured capture, containment, and one-probe gate passed | no real main-weight, quality, throughput, 327K, C4, or image result |
| DeepSeek Stage 4 r7/r8 | r7 stopped at pre-engine checksum integrity; r8 passed bounded real-weight 64K/C1/image=0 basic and resource gates | no quality, long-context, image, C4, full-role, or promotion result |
| DeepSeek full-role r2/r3/r4 | r2 and r3 each failed their exact frozen resource reserve after bounded checks; r4 passed its resource gate but failed the first required Oracle8 round | each configuration is bounded to its exact result; no qualification outcome follows |
| Negative boundary | configured maxima and engine-reported cache values are not workload-capacity proof | no full-context or concurrent-window measurement |

**Why it matters:** no local challenger has evidence to replace the retained GLM
baseline.

**Important caveat:** the campaign used bounded protocol, Oracle, and fixed-five
SWE sampling with prerequisite pruning. A failed required gate stopped dependent
finalist work; omitted full finalist, capacity, performance, and quality tests
are not negative results.

Artifact manifest: [artifact-manifest.json](2026-10-15-replacement-evaluation/artifact-manifest.json) · Evidence index: [README.md](2026-10-15-replacement-evaluation/README.md)

## Authoritative gate summary

This is the sole table of campaign counts and measured gate values. Configured
context, concurrency, and image limits are not full-context or concurrent
capacity measurements.

| Lane | Retained result | Decision boundary |
| --- | --- | --- |
| GLM r11 control | protocol 5/5; historical Oracle 7 54/54; SWE 4/5 with required Pylint sentinel failure; final restoration direct 5/5, routed 6/6, and actual-Pi read/settled receipt retained | control did not clear the frozen sentinel contract; it remains the retained baseline, without a new qualification or promotion claim |
| MiMo MOPD r4 | Oracle 7 15/18; SWE 3/5 | diagnostic historical-comparison lane; no promotion |
| MiMo MOPD r5 | Oracle 8 54/54; SWE 2/5 with required Pylint sentinel failure | did not clear the fixed-five coding gate; no promotion |
| Mistral Medium 3.5 r2/r3 | r2 3,826 MiB policy headroom; r3 Oracle 8 17/18 with 4,798 MiB headroom | r2 resource-policy rejection; r3 misses the frozen campaign reliability gate; no promotion |
| DeepSeek V4.1 Flash Stage 2 | independently reviewed one-GPU numerical runtime compatibility only | no weights, serving, quality, capacity, or promotion conclusion |
| DeepSeek V4.1 Flash Stage 3 r7–r12 | bounded startup, diagnostics, and r12 dummy-path evidence retained | r12 is not real-weight, quality, throughput, 327K, C4, or image evidence |
| DeepSeek V4.1 Flash Stage 4 r7/r8 | r7 pre-engine checksum failure; corrected r8 bounded real-weight 64K/C1/image=0 basic and resource pass | no long-context, multimodal, full-role, quality, throughput, or promotion result |
| DeepSeek V4.1 Flash full-role r2 | startup and basic non-thinking gate passed; the exact configuration failed the frozen GPU resource reserve and was unloaded | r2 is not a full-role qualification; unrun follow-on gates are unmeasured |
| DeepSeek V4.1 Flash full-role r3 | independently reviewed terminal: basic 2/2 and model-aware 5/5 (20-tool batch) passed; continuous monitor reached 3,961 MiB per GPU below the 4,096-MiB reserve; removed | exact r3 is empirically disqualified by the policy gate; no quality, capacity, throughput, or promotion conclusion |
| DeepSeek V4.1 Flash full-role r4 | resource gate passed with a 6,539-MiB per-GPU minimum; Oracle8 round 1 reached 17/18, with the debug-loop response using invalid scope `all` then `unit` and five calls against a four-call limit | the first mandatory reliability round failed; rounds 2/3 and all downstream quality, coding, context, capacity, throughput, vision, stability, and held-out gates are unrun |

## Scope and interpretation

GLM, MiMo, and Mistral were direct TP2 qualification lanes on two RTX PRO 6000
Blackwell Max-Q GPUs. DeepSeek Stage 2 was a one-GPU numerical compatibility
check; Stage 3 was a two-GPU dummy hardware-path investigation. Stage 4 adds a
bounded real-weight 64K/C1/image=0 basic gate, and full-role r2/r3 add separately versioned
327680/C4/image=8 resource-gated configurations. r4 retained that workload
shape and passed its resource gate, but its first mandatory Oracle8 round failed.
None is a quality, throughput, or promotion comparison.

The retained GLM, MiMo, and Mistral configurations used configured maxima up to
327,680 tokens/C4/eight images; Mistral r3 used 262,144 configured tokens/C4.
Those declarations do not establish actual full-context coverage, four
simultaneous full windows, or multimodal acceptance. Protocol and Oracle samples
were bounded prerequisites; the fixed-five SWE gate was a separate required
reliability check. Failed required gates pruned dependent finalist, capacity,
performance, and broader quality work rather than producing an implied failure.

## DeepSeek boundary

Stage 2 accepted runtime numerical compatibility under its pinned image and
sources only. Stage 3 r12 passed the bounded dummy-path integrity, health,
configured capture, resource, containment, and one-probe gate; dummy text has
no quality meaning. Stage 4 r7 stopped before engine initialization at its candidate-index
checksum, while corrected r8 passed the bounded real-weight 64K/C1/image=0
basic and resource gate. Full-role r2 reached health and basic non-thinking
correctness but failed its frozen GPU reserve after the basic gate; no OOM event
or follow-on qualification request changes that policy result. r3 is a separately
versioned configuration: its basic and model-aware bounded checks passed, but its
continuous resource monitor fell below the same frozen reserve, so it was removed.
r4 passed its resource gate but missed its first mandatory Oracle8 reliability
round; it cannot inherit either prior result.

The independent historical-source assessment also limits older GLM, MiMo, and
Mistral Oracle/SWE interpretation: their retained historical records do not
provide uniform evidence of the actual worker source imported at execution.
This is a provenance limitation, not a re-score, family ranking, or code-defect
claim.

## Decision and follow-up

The incumbent remains the retained GLM baseline described in the prior finding.
This completed gate evaluation did not qualify a challenger. Full-role r4's
first Oracle8 failure pruned rounds 2/3 and every downstream gate. The evidence
records the exact configuration and terminal boundary; it does not turn the
configuration into a full qualification or throughput result.
