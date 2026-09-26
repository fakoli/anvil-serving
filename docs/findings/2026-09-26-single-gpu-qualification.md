# Single-GPU Swift and Qwen3.8 qualification

<!-- benchmark-result-card/v1 -->
## Result card

> On RTX PRO 6000 hardware, no isolated single-GPU challenger met the frozen
> coding floor; the existing dual-GPU GLM r11 remains selected.

| Setup | Qualified value |
|---|---|
| Model | GLM-5.3-Flash EXL3 r11 reference; Swift Flash IQ3, Swift27 NVFP4, and ThinkingCap AWQ challengers |
| Hardware | two RTX PRO 6000 Blackwell Max-Q GPUs for GLM; one per challenger |
| Runtime | pinned recipe reconstructions in the evidence bundle |
| Recipe | [sanitized Flash r3 configuration](2026-09-26-single-gpu-qualification-evidence/configurations/swift-iq3-r3.recipe.toml) |
| Measurement path | isolated direct candidate gates; retained GLM direct and authenticated routed restoration checks |
| Contract | frozen five-task SWE floor of at least 4 resolved; 327,680 configured candidate context |
| Evidence | `functional`, bounded `quality`, and `external-prior`; ThinkingCap coding raw evidence is missing |
| Decision | `current` GLM reference; challengers `rejected` or `no-promotion`; no live alias changed |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| GLM coding reference | 4/5 | verified retained exact-r11 SWE reference |
| Flash IQ3 r3 coding | 3/5 | all five graded |
| Swift27 r2 coding | 2/5 | four graded; fifth exhausted call budget |
| ThinkingCap r1 coding | at most 3/5 | two known unresolved; incomplete capture |

**Why it matters:** the incumbent stays in service without a route or client-contract change.

**Important caveat:** ThinkingCap cancellation deleted raw coding trajectories; its upper bound is not a completed score.

[Artifact manifest](2026-09-26-single-gpu-qualification-evidence/artifact-manifest.json) · [Evidence index](2026-09-26-single-gpu-qualification-evidence/README.md)

The exact GLM r11 deployment was restored after isolated trials. Its direct and
authenticated routed preflights passed and its alias and limits did not change.
No candidate was promoted, and no performance winner is claimed.

## Qualification record

The five frozen SWE tasks used the same agent/grader revisions, dependency
environment, 60-call limit, and 16,384-token completion cap. Sampling was
temperature 1.0 and top-p 0.95. Flash r3 used native thinking at `medium`
effort; Swift27 and ThinkingCap used their native default thinking, while GLM
used enabled thinking. These are whole-recipe comparisons with intentional
family-specific controls. The GLM 4/5 result reuses verified exact-r11 evidence;
no new baseline SWE run was needed. Candidate job timeouts were 21,600 seconds
versus the retained baseline's 3,600 seconds, with the per-task limits fixed.
Five tasks establish this campaign's acceptance boundary, not a broad model
ranking or statistically demonstrated superiority.

All three final candidates passed the retained deterministic suites: agentic
18/18, image 12/12 plus eight-image 4/4, and context 12/12. Flash IQ3 r2 is retained as a
failed configuration because its agentic suite was 17/18 with an invalid tool
enum. Flash IQ3 r3 repaired that suite and passed tools 20/20, but resolved
only 3/5 SWE tasks. Swift27 NVFP4 r2 resolved 2/5 with four tasks officially
graded; its APC-only r3 successor was prepared but not run.

ThinkingCap AWQ r1 passed the deterministic suites, then Pylint and Sphinx
each exhausted 60 calls without a submission. Two known unresolved tasks make
the best possible total 3/5, below the frozen 4/5 floor. The managed
cancellation removed the working trajectories. The public record retains only
the reviewer-observed exit status and test excerpts; it does not call this a
completed 3/5 result.

The one-GPU Flash recipe uses CPU-mapped PLE components. One GPU therefore does
not mean all weights were resident in VRAM. Retained isolated C1-unique and
C4-shared correctness cells are bounded diagnostics; the earlier C4-unique
seed-0 timing had cross-cell cache reuse and is invalid. Fresh matched speed
cells were omitted once no candidate cleared coding, so no performance or
equivalence claim follows.

## Research leads

Four alternatives were researched as dated external priors: base Qwen3.8
Flash Next, base Qwen3.8 27B, Swift 1.5 Qwen3.8 27B, and ThinkingCap Qwen3.8
27B. Model-card parameters, checkpoint sizes, and community reports did not
qualify any model. The source registry records only the dated sources and their
bounded decision impact.

## Evidence and restoration

The [evidence index](2026-09-26-single-gpu-qualification-evidence/README.md)
links retained native suite files, sanitized recipe reconstructions, the exact
GLM SWE reuse receipt, restoration proof, and the cancellation gap. Jev was
advisory research triage only; it did not grade or promote a candidate.
