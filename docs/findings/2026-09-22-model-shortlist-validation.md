# Model shortlist validation

**Date:** 2026-09-22. **Decision:** retain GLM; Qwen3.8 Flash Next is an
interesting but unqualified candidate. No promotion or route change is
authorized.

<!-- benchmark-result-card/v1 -->
## Result card

> The initial Qwen3.8 Flash Next NVFP4 TP2 trial loaded and passed serial C1 direct
> preflight on two RTX PRO 6000 Blackwell Max-Q cards, but did not pass the
> campaign's strict deterministic agentic gate.

| Setup | Retained value |
|---|---|
| Candidate | `RadixArk/Qwen3.8-Flash-Next-NVFP4@7b719225242aacd3dbd3f9407468c2ee9a9d2594` |
| Runtime | SGLang `4ccff141dbe992794f9da6c3aa23535b4f72000d`; image `sha256:8ac9dedd8c3c98bec7b91f4c2c7b697e4f429e336428dd8167140075e295bae7` |
| Hardware | 2x RTX PRO 6000 Blackwell Max-Q, TP2; C4 configured server admission |
| Configuration | 262,144 configured tokens, no MTP, FP8 PLE GPU-resident, automatic BF16 KV; checks were serial C1 |
| Managed recipe / reproduction | Operator-private recipe; public reconstruction in [exact configuration](#exact-configuration) and the [run plan](2026-09-22-model-shortlist-validation-evidence/run-plan.md#qwen-initial-recipe) |
| Measurement path | Direct, online cold-start and bounded functional/agentic checks |
| Decision | Retain GLM; `no-promotion` |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Startup memory | 65.55 GiB/rank | TP2 cold start; no OOM |
| Direct preflight groups | 6/6 passed | coding, JSON, tools, streaming, tool result, and nominal ~8K needle |
| Agentic greedy scout | 16/18 | Native suite floor 0.75; campaign deterministic gate is 18/18 |
| Agentic native-sampling scout | 15/18 | temperature 1.0, top_p 0.95; same 18/18 campaign gate |

**Why it matters:** the candidate is feasible enough to investigate, but
fixture/scorer validity blocks a trustworthy strict agentic comparison.

**Important caveat:** both greedy misses violate the exact tool-call
count/arguments; positional fixture drift then limits interpretation. The three
native-sampling misses likewise do not establish broad coding inferiority or
executed-code behavior.

[Evidence index](2026-09-22-model-shortlist-validation-evidence/README.md) ·
[Artifact manifest](2026-09-22-model-shortlist-validation-evidence/artifact-manifest.json) ·
[Publication summary](2026-09-22-model-shortlist-validation-evidence/publication-summary.md) ·
[Decision summary](2026-09-22-model-shortlist-validation-evidence/summary.json) ·
[Source registry](2026-09-22-model-shortlist-validation-evidence/source-registry.json).

## Outcome and decision

The candidate's six direct preflight groups passed and it remained stable
through the scout. Its native agentic artifacts state `passed: true` because
their own required pass rate is 0.75. The campaign plan independently requires
100% deterministic success. The greedy control was 16/18 and the official
native-sampling replay was 15/18, so downstream capacity, performance, vision,
large-context, deep-coding, and SWE work did not run.

The paired GLM scout also recorded 16/18. Its two planning false negatives
expose a second scorer defect: ordered-term matching finds an incidental
earlier substring rather than enforcing the required heading order. The raw
16/18 tie is retained, but it cannot rank broad coding or show Qwen is
strictly worse. Retain GLM; Qwen remains interesting and unqualified. Heavy
qualification is blocked by harness validity, not rejection of the model.

The exact GLM baseline was restored after the trial: the four public
configuration identities retained in [restoration evidence](2026-09-22-model-shortlist-validation-evidence/restoration.json)
matched, the full-mode snapshot matched, direct and routed smoke/JSON
preflight passed, the router readmitted the exact baseline identity, no OOM was
recorded, and shared memory was 2% used. This is bounded campaign closure, not
new GLM qualification.

## Exact configuration

The trial used the pinned RadixArk revision above with SGLang revision
`4ccff141dbe992794f9da6c3aa23535b4f72000d` and image digest
`8ac9dedd8c3c98bec7b91f4c2c7b697e4f429e336428dd8167140075e295bae7`. It ran TP2 with C4 configured admission and serial C1 checks, with 262,144 configured tokens, no MTP, ModelOpt
FP4 quantization, GPU-resident FP8 PLE, automatic BF16 KV, and
`flashinfer_cutlass` MoE execution. Startup reported 65.55 GiB per rank and
928,576 allocated shared-pool KV tokens. This is not a measured long-context result. The sanitized settings and status records are
retained in the [evidence bundle](2026-09-22-model-shortlist-validation-evidence/README.md).

## Method

The plan screened external priors before a managed local trial. Direct checks
covered short coding, structured JSON, a shared-prefix tool batch, streaming
tools, tool-result continuation, and a nominal ~8K needle. The agentic scout
used 18 serial C1 native deterministic observations with thinking enabled: a
temperature-zero control and a temperature-1/top_p-0.95 replay. The retained
controls do not establish that every generation default, including top_k, was explicitly sent.
The predeclared advancement threshold was 18/18 for each deterministic gate.

## Results

The Qwen startup completed without OOM. Basic preflight passed coding and JSON;
the tool preflight passed all four groups, including two clean shared-prefix
tool requests. The raw native agentic suite reports 16/18 (0.889) for greedy
and 15/18 (0.833) for native sampling. Those values clear the suite's 0.75
minimum but fail the stricter campaign rule.

The external screen uses Artificial Analysis Intelligence Index v4.3.2,
observed September 22. These are hosted-model priors, not measurements of
the local quantizations; small score differences do not establish superiority.

| Model | AA index | Local priority |
|---|---:|---|
| GLM-5.3-Flash | 42 | Retain the deployed baseline. |
| Qwen3.8-Flash-Next | 40 | First candidate; basic local checks passed, agentic comparison unresolved. |
| DeepSeek-V4.1-Flash | 39 | Defer the expensive two-bit/offload experiment behind Qwen. |
| MiniMax M3 | 30 | Lower priority for the primary coding role. |
| Ling3.0-Flash | 25 | Lower priority for the primary coding role. |
| MiMo-V2.6-Flash | Not verified | Watch for a supported remedy to the retained correctness failure; Pro results do not apply to Flash. |

Code Arena listed preliminary Qwen coding 1635 and GLM 1607. Its preference
results nominate Qwen for testing; they do not establish repository-task
correctness. Exact source URLs and observation dates are in the
[source registry](2026-09-22-model-shortlist-validation-evidence/source-registry.json).

## Failures and caveats

The two greedy misses were debug-loop traces with extra calls and a semantic
edit-format mismatch, so the exact protocol scorer correctly rejected them.
The sampling replay had two debug-loop protocol failures and one
dependent-result failure: its lookup/update calls were correct, but the final
answer omitted the required `UPDATED-73` marker. The fixture is indexed by global
call position, so an extra read can receive a later scripted lifecycle
response. Separately, the paired GLM planning response visibly used the
required Inspect, Patch, and Test headings but failed because the scorer's
first substring search matched incidental “tests” under Inspect before Patch.
See the [fixture-drift ticket](https://github.com/fakoli/anvil-serving/blob/codex/mimo-v26-qualification/.tickets/2026-09-22-agentic-debug-fixture-drift.md).

These harness defects must be repaired before a repeat strict gate can
distinguish broad planning/coding behavior. They do not excuse the valid narrow
protocol misses or turn the current candidate into a qualified result.

## What to test next

Repair both deterministic oracles before rerunning: the positional fixture
must reject extra calls without consuming later lifecycle responses, and
ordered planning-term matching must accept the captured correct heading order
while rejecting truly out-of-order headings. Only then can a fresh exact
agentic gate advance the current Qwen recipe to context, image, capacity,
performance, deep coding, and SWE stages. Watch
Qwen4 pending a verified artifact; keep MiMo Flash for a source-supported
recipe and independent result; defer DeepSeek conditionally. Ling and MiniMax
are deprioritized for this primary coding role, not universally rejected.

## Evidence boundary

This finding records local startup and functional evidence plus failed
campaign advancement. It makes no throughput, quality, context, vision,
capacity, or coding-quality claim. External scores are priors. No configuration
or lifecycle change is authorized by this publication.

The startup log also retains a nonfatal custom-allreduce UUID-to-index parse
warning and fallback. The candidate still reached readiness and direct
preflight; the warning must not be described as absent or as a clean startup.
