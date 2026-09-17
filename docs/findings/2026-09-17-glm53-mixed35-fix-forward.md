# GLM mixed 3.5-bpw EXL3: loader recovery and C4 results

The mixed 3.5-bpw candidate now runs. A narrow loader patch removed retained
CPU weight copies, and managed RAM/swap limits contained both successful
startups. The candidate passed C1 and C4 functional preflight, repeated bounded
coding/tool checks, and retrieval from a 216,307-token input.

Keep the existing baseline. Strict output-length checks were inconsistent on
both configurations, and the single matched chat sample showed no latency
improvement. These results establish recovery and bounded capability, not an
overall win or qualification for promotion.

<!-- benchmark-result-card/v1 -->

| Result card | Value |
| --- | --- |
| Local setup | Two RTX PRO 6000 Blackwell Max-Q GPUs; native Linux; TP2/DCP1; mixed K3/K4 EXL3; NVFP4 MLA KV; 327,680 configured tokens; C1 then C4 |
| Functional and quality gates | Smoke, JSON, and tools passed on both configurations; C4 bounded intelligence/tool checks passed three repetitions |
| Context | Exact retrieval from 216,307 actual input tokens in 59.034 s; one request on the C4 configuration |
| Capacity | First C4 cell passed 4/4 at 98.17 aggregate completion tok/s; second passed 3/4, invalidating its throughput result |
| Decision | Retain baseline; `no-promotion`; no demonstrated overall improvement |
| Evidence | [Bundle index](2026-09-17-glm53-mixed35-fix-forward-evidence/README.md) · [manifest](2026-09-17-glm53-mixed35-fix-forward-evidence/artifact-manifest.json) · [publication summary](2026-09-17-glm53-mixed35-fix-forward-evidence/publication-summary.md) |

## Exact configuration

| Field | Value |
| --- | --- |
| Candidate | `satgeze/GLM-5.3-Flash-EXL3-TR3-3.5bpw@62587d6015184a26773a4ed1b751a9c5fa469cd6` |
| Served names | C1: `glm53-mixed35-v84-stream-r7-v1`; C4: `glm53-mixed35-v84-stream-r7-c4-v2` |
| Image | `sha256:d1311adc96a37d235858aa397040ba5e550366961f1d4142f048e30476b93c47` |
| Runtime configuration | vLLM-derived runtime; TP2/DCP1, no EP; NVFP4 MLA KV; 2,048-token batches; no speculation |
| Containment | 60 GiB RAM, zero additional swap; 16 GiB admission headroom |
| Baseline | BrandonMusic 4-bpw EXL3; TP2/EP2/DCP2; FP8 MLA KV; C4; 327,680 configured tokens |
| Scope | Whole-configuration comparison; differences cannot be attributed to quantization alone |

The [complete C4 recipe](2026-09-17-glm53-mixed35-fix-forward-evidence/candidate-recipe.toml)
retains the flags and environment controls. C4 changes only the scheduler's
maximum sequences and served identity from the repaired C1 configuration.

## Root cause and repair

The original loader kept non-preallocated R7 TP slices on the CPU until later
processing. The opt-in patch transfers each slice synchronously to its
initialized CUDA parameter device, preserving mixed K3/K4 shapes. A CPU fixture
checks slicing, lifetime, transfer arguments, and rejection of a CPU target;
startup traces separately prove CUDA transfers on both ranks. The first retry
loaded all 120 shards in 48 seconds.

The shared recipe launcher now validates RAM, combined RAM-plus-swap, and
admission-reserve settings; checks the local Docker endpoint and cgroup-v2
support; and exposes actual limits, peak usage, OOM counters, and exit state.
A separate 64 MiB probe contained an intentional 256 MiB allocation while the
baseline stayed healthy. Both model retries recorded zero OOM events. The
16 GiB reserve is checked at admission; it is not a global RAM reservation.

Reproduction files include the [patch](2026-09-17-glm53-mixed35-fix-forward-evidence/loader-r7-stream.patch),
[fixture](2026-09-17-glm53-mixed35-fix-forward-evidence/test_stream_loader.py), and
[pinned build reconstruction](2026-09-17-glm53-mixed35-fix-forward-evidence/build-reconstruction.md).
The earlier unbounded failure remains in the [original finding](2026-09-17-glm53-mixed35-startup.md).

## What the tests establish

Both baseline and candidate C4 runs used default thinking, requested
`reasoning_effort=max`, and a 16,384-token completion cap: a 1,024 visible-token target plus
15,360 tokens of reasoning headroom. The effort control was requested but
not independently verified. The earlier disabled-thinking, 256-token
control failed before valid JSON; that diagnostic remains recorded.

| Check | Baseline C4 | Repaired candidate C4 | Interpretation |
| --- | --- | --- | --- |
| Bounded intelligence/tool checks | Three repetitions passed | Three repetitions passed | Deterministic checks; broader repository work remains untested |
| Chat, 25,558 actual prompt tokens | 15.86 s | 16.64 s | One sample each; descriptive, not a latency ranking |
| Strict capacity, first cell | 2/4 valid; two answers had 63 of 64 required words | 4/4 valid; 98.17 completion tok/s | Baseline throughput withheld; no valid throughput comparison |
| Strict capacity, second cell | Not run after failure | 3/4 valid; one answer had 65 of 64 required words | Candidate repeat is performance-ineligible |
| Long retrieval | Not part of this control | Exact answer, 216,307 input + 143 completion tokens, 59.034 s | One request; not four simultaneous long requests |

All capacity request canaries passed. Failed counts are output-contract
failures, not engine crashes or evidence of inferior coding ability. The
valid cell's completion throughput includes reasoning tokens. The context
probe targeted an estimated 262,144 tokens; neither that estimate nor the
327,680 configuration is the measured input count.

The initial C1 intelligence scout passed two checks once each. It is retained
separately and does not supply the C4 repetition evidence.

## Decision and remaining scope

The repaired model and configuration remain experimental. The user's
conditional promotion authorization requires a demonstrated improvement;
these results do not establish one. Representative repository tasks, repeated
session and real-client acceptance, consistent strict capacity, the full
configured window, and simultaneous long-context capacity remain unqualified.
This is not a rejection of the model or quantization family.

A detached service owns exact baseline restoration and direct/routed checks.
The [restoration records](2026-09-17-glm53-mixed35-fix-forward-evidence/README.md)
separate C1 and C4 recovery. No alias or production assignment was promoted.

Public artifacts redact operator paths, endpoint identities, container IDs,
and GPU UUIDs. Primary Node ran the tests; Mini was not involved.
