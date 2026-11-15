# Runtime investigation and recipe development

Use this alongside the qualification skill for three requests: reproduce a
runtime failure, improve a pinned configuration, or measure its hardware
envelope. A healthy restart is recovery; it does not resolve the incident.
Use the existing managed candidate lifecycle, evidence contract, and benchmark
publication workflow. Do not create another campaign framework.

## Freeze the question before testing

Record the incident or objective, model/image/source/driver identities, exact
recipe and request settings, protected workloads, starting state and rollback.
Retain a scenario with a time/request/context/output bound, abort conditions,
independent correctness checks, and the smallest useful comparison. Bind each
attempt to the scenario and recipe hashes. Keep runtime reliability, workload
coverage, semantic correctness, formatting, and performance eligibility as
separate results. Unknown is not passing.
Mechanically compare parent and candidate scenario fields before traffic and
retain the normalized diff, including both output caps and reasoning policy.
Retain accidental mismatches as separate
failed or non-comparable attempts; correct the scenario under a new identity.

Resolve the operation contract and resource owner through the existing
candidate-operations workflow. If structured wrappers are unavailable, retain
that limitation and use the documented CLI. Verify the executing checkout,
command host/runtime, and effective served identity before requests and after
every reload; a recipe file or label alone does not prove its settings loaded.

| Use | First experiment | Required conclusion |
| --- | --- | --- |
| Failure investigation | Recreate the observed request ordering and state; pair with a nearby non-triggering control. | Reproduced, not reproduced within the measured exposure, or trigger not exercised; root cause remains unknown until isolated. |
| Configuration improvement | Parent and one supported change on identical cases, followed by a reverse-order parent control. | Measured benefit and protected regressions, including quality and memory cost. |
| Hardware envelope | Increase one context, output, concurrency or batching axis from a passing point. | Last repeated passing point and first failure or untested bound, with input/output headroom and simultaneous demand. |

If the parent already causes a GPU fault or engine death, retain that control
instead of deliberately crashing it again for ordering symmetry. Record why
the reverse-order control was omitted and the resulting inference limit.

Do not require a new permission exchange for work already authorized. Confirm
operational previews using that authority; a new promotion, unrelated host
change, or interruption of an unapproved workload remains outside scope.

## Validate the skill and runner before live use

Freeze the old and new skill identities. Use the skill-improvement workflow
with an independent reviewer and isolated cases: the triggering incident, a
successful ordinary benchmark, and a publication-only task that must not
generate requests. Include fresh transfer cases after edits. Judge decisions
and artifacts, not matching wording. Packaging checks alone are insufficient.

The stability runner's offline tests must prove that a terminal-only or empty
stream cannot release the decode barrier, an early-finished anchor cannot
qualify overlap, interrupted streams retain partial evidence, and a failed
trial stops subsequent rounds. Preview must make no network requests. Test the
actual CLI and streaming transport with a local synthetic server before GPU
work. Keep prior benchmark behavior compatible.
Include malformed and duplicate-key scenarios, numeric/resource boundaries,
literal paths with whitespace/metacharacters, missing identity and occupied
output paths. Inputs are data; no scenario or path may become a shell command.

## Reproduce the runtime state

Start with synthetic, independently checkable data. Preserve sizes and ordering
from incident metadata without copying private user conversations. For a
mixed-load incident, start the long-context anchor, wait for a real generated
content or reasoning delta, then introduce the fresh prefill. Record whether
anchor output continues while the second request waits for first output.
Concurrent client connections alone do not prove a mixed scheduler batch;
correlate the owning engine's scheduler evidence when claiming that trigger.
If the intended overlap was missed, report incomplete coverage, not stability.

Run serial and overlapping controls, then fresh and repeated-prefix rounds.
Record actual prompt tokens and cache counters when available; repeated input
alone proves neither a cache hit nor eviction. Do not call a partial stress
test a long-duration soak. Bounded successful exposure is not a crash-rate
guarantee. Record warmup/compilation separately from steady-state requests.

For a cache-state hypothesis, add churn, request cancellation, slot reuse,
ragged lengths and page boundaries as implicated by the failure. Use runtime
unit tests for high page IDs, integer-overflow boundaries, scratch poisoning,
graph replay and allocation ownership; an HTTP test cannot prove those kernel
invariants. Never imply every campaign ran all of these probes.

Use the supported command with a reviewed non-secret scenario file:

```text
anvil-serving eval benchmark stability --scenario scenario.json --output trial.json
anvil-serving eval benchmark stability --scenario scenario.json --output trial.json --confirm
```

The unconfirmed form is an offline preview. A scenario's tokenizer contract
must match its endpoint; the initial runner supports vLLM chat tokenization.
Its native evidence describes client-observed overlap, not scheduler internals.

## Follow a failure to its owner

Stop new trial requests on engine death, an unexpected identity, host OOM,
GPU Xid, containment failure or uncontrolled memory growth. Preserve requests,
timestamps/IDs, terminal state, managed container logs and kernel evidence
before recovery. Distinguish deliberate client cancellation, timeout, HTTP
error, engine failure and hardware fault. A CUDA stack may report a later
synchronization point rather than the first invalid access.

For first-fault attribution after a reproducible CUDA illegal access, use an
isolated managed diagnostic recipe with CUDA core dumps and, when useful,
line information. A bounded configuration-mitigation trial may defer this
step, but must retain kernel root cause as unresolved. Keep dumps
private and bounded to available disk; they may contain model/user memory.
Record the faulting rank/kernel and reduce the failing operation before using
Compute Sanitizer. Do not enable intrusive diagnostics on a shared production
serve or treat a clean sanitizer run on different shapes as exoneration.

Profile only when it answers a specific question. Verify the runtime's actual
capture bounds and retain relevant ranks locally. Do not import an external
helper's workstation destination or process/environment discovery defaults.

## Test a remedy and then explore

Research the exact deployed runtime and supported controls. Keep speculation
out of a no-spec incident hypothesis unless introducing it as a separately
matched experiment. Test one causal delta, then replay the failing workload
and a nearby control. A lower concurrency may be a useful mitigation even
without proving the underlying defect; state the lost capacity explicitly.
No observed failure after a driver upgrade is not proof of a driver fix.

Only after incident coverage is settled or explicitly unresolved, run the
bounded envelope search. Stop advancing a failing axis; diagnose it and retain
the failed point. A configured maximum is not measured simultaneous capacity.
Use existing context, capacity, quality, tool and modality gates for finalists.
Natural long outputs need independent coherence/loop checks; strict word-count
failures alone do not establish a runtime crash or coding-quality regression.
Diagnostic fixed-length/forced-decode outputs do not qualify natural generation.

Use at least three matched repetitions for finalist performance claims,
alternating order where practical. Report TTFT and decode interruptions along
with throughput, actual output lengths and cache state. Keep kernel-only
timing supplementary; respect rank synchronization and the slowest rank.

## Keep a recipe notebook as evidence is produced

Append one entry per attempted configuration: question, parent/hash, exact
change, predicted result, command/scenario hash, timestamps, actual workload,
correctness, coverage, runtime result, performance eligibility, raw artifacts,
interpretation, counterevidence, cost and next step. Never erase failed arms.

At closure, publish a reconstructible recipe document: supported hardware and
software, immutable weights/image/source, launch recipe and rationale, tested
context/concurrency/output matrix, repeat counts and methodology, failures,
known limits, diagnostics, rollback, and raw artifact links. Keep private
topology and credentials outside public reconstruction files. State whether
the result is a mitigation, qualified candidate, unresolved investigation, or
unchanged baseline; retain the original incident until its closure criteria
are actually met. Complete the existing publication matrix and restoration
checks. Producing a shareable recipe does not promote it.

## Source leads, inspected 2026-09-23

- [vLLM illegal-access debugging](https://vllm.ai/blog/2025-08-11-cuda-debugging): core dumps locate the GPU fault; distributed shutdown errors can be secondary.
- [LIL B12X guidance](https://github.com/local-inference-lab/b12x/blob/10a553ef980571f23a073f930cb386fbc8a77e07/AGENTS.md): high recycled page IDs and graph ownership need explicit tests.
- [LIL mixed traffic](https://github.com/local-inference-lab/rtx6kpro/blob/2960b922466aa3167da0c7f6b3918b329bc36216/models/glm-5.3-flash/validation/scheduler-serving-r28.1.md): separate mixed-prefill/decode and cache/cancellation qualification from pure throughput.
- [LIL scoped cache qualification](https://github.com/local-inference-lab/rtx6kpro/blob/2960b922466aa3167da0c7f6b3918b329bc36216/models/glm-5.3-flash/validation/concurrent-checkpoints-r32.md): retain constrained-output failures without relabelling them as passing cache evidence.

These are external methods, not qualification of another recipe or hardware.
