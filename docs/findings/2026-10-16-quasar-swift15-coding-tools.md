# QUASAR versus Swift15: coding and tools

**Date:** 2026-10-16. **Decision:** `no-promotion`; keep Swift15 as default for now.

<!-- benchmark-result-card/v1 -->
## Result card

QUASAR is not a clear upgrade on this bounded functional comparison. It passed
fewer tool scenarios; its coding result depends on an ambiguous input representation.

| Setup | Measured configuration |
| --- | --- |
| Hardware/path | One RTX 5090 (32,607 MiB), direct local endpoint, one model at a time |
| Runtime | Same pinned NInfer image, text only, K8V4, DFlash2 K7 |
| Context/load | 32,768-token ceiling; configured C4, actual requests C1; shared KV 131,072 |
| Coding | Seven tasks, three repetitions, 123 executable cases per model |
| Budget | 4,096 visible allocation + 8,192 reasoning headroom; combined cap 12,288 |
| Sampling/cache | Medium reasoning, temperature 0.6, top-p 0.95; no fixed seed; cache warmth not separately controlled |
| Boundary | Functional evidence; no route, alias, default-model or promotion change; temporary state restored |

| Measurement | QUASAR | Swift15 |
| --- | ---: | ---: |
| Agentic scenarios | 16/18 | 18/18 |
| Frozen list-pair coding: whole solutions | 20/21 | 20/21 |
| Frozen list-pair coding: assertions | 119/123 | 120/123 |
| Post hoc tuple-pair sensitivity: whole solutions | 21/21 | 20/21 |

[Artifact manifest](2026-10-16-quasar-swift15-coding-tools-evidence/artifact-manifest.json),
[evidence index](2026-10-16-quasar-swift15-coding-tools-evidence/README.md), and
[publication summary](2026-10-16-quasar-swift15-coding-tools-evidence/publication-summary.md).

## What the results establish

Both matched profiles passed direct preflight, including 20 tool calls each.
In both QUASAR failures, the model called the correct lookup and update tools
with correct arguments but omitted the explicitly requested final status
`UPDATED-73`. These were result-incorporation failures, not tool-parser failures.

Swift15 made a real big-endian `decode_uvarint` error in repetition `1` as
labelled in the retained artifact. QUASAR's only frozen coding miss was
`stable_toposort` treating pairs as hashable tuples while JSON IPC supplied lists.
The prompt specified `(before, after)` pairs without explicitly requiring list
pairs. A separately retained post hoc analysis reconstructed only those pairs
as tuples, using identical cases, expected outputs and model responses. QUASAR
then passed all 21 solutions; Swift15's varint error remained. The frozen grades
were preserved. This ambiguity and the tiny fixture prevent a general coding ranking.

The descriptive coding request-time sums were 210.4 seconds for QUASAR and
218.0 seconds for Swift15. The 3.6% difference is not a performance win:
per-attempt variance was large, output lengths differed, and background activity
and cache warmth were not fully controlled. No performance chart or capacity
qualification is claimed. These were short pure-function tests, not repository-level SWE.

## Identity and method

QUASAR was `wallawalla47/Qwen3.8-27B-Quasar-NVFP4-NInferV3` at
`fd3d06cea9df58a54d5051aac9545a4a01a922db`; Swift15 was
`kaushikvira/Qwen3.8-27B-swift15-nvfp4full-dflash2-NInfer-v3` at
`ff891a1130adfcf46bf745e06f0fa3dcde2e8c82`. Both used image
`anvil-ninfer@sha256:59df48dd99f0d177b73de57ba88eb6457ed7e4b0c5771c45e8976adcd9f5ef67`
and NInfer source revision `bace20dc70249eed6402b66d4852c6c3f9612905`.
Artifact and binary hashes were checked before serving. The artifacts have
different precision layouts; the QUASAR artifact is not wholly NVFP4 or lossless.

The same seven frozen prompts, independent executable grader, budgets, and
18-scenario agentic scout were used for both finalists. Coding responses,
reasoning, stop reasons and per-attempt classifications are retained. The endpoint
accepts one combined completion cap; it does not enforce separate channel budgets.
The grader never called either model to judge an answer. Its source was manually
reviewed before bounded offline execution; it is not a hostile-code sandbox.

The live benchmark harness was package **1.5.0**, Python **3.14.8**, with source
fingerprint `8842145699f43e59e2649d27e662c09125e1c75f88dff27196cbc232d4677720`.
Its installed source revision was unavailable. The import-helper/publication
worktree declared **1.2.1**, based on `2b99e3353eb2e5a2813195af85050b1da0754332`
with uncommitted changes; that revision is not asserted as the source of the
1.5.0 collection. [Full configuration and recipe reconstructions](2026-10-16-quasar-swift15-coding-tools-evidence/configuration-and-identity.md).

## Earlier profiles and acquisition failures

The original Swift15 262K control was preliminary and is separate from the fresh
32K comparison above. QUASAR's vision-enabled 262K profile failed after weight
loading with `bf16 linear: unsupported shape`. Disabling vision cleared that error,
but the text-only 262K profile missed the GPU runtime-reservation floor. Neither
failed profile produced a quality result. The final matched text-only 32K profiles
were subsequently loaded and measured; these results do not qualify a 262K vision replacement.

Transport initially failed with TLS record errors and authenticated SSH integrity
errors. The approved checksum-offload test did not fix bulk transfers and all five
settings were restored. A cache-only managed pull on another host, followed by
verified transfer and managed local import, supplied the exact artifact. That
cache host performed no inference and is excluded from measured hardware. The
network root cause remains unresolved. [Failures and fix-forward dispositions](2026-10-16-quasar-swift15-coding-tools-evidence/friction-log.md).

## Closure and verification limits

The retained independent restoration record reports the original Swift15 262K
container healthy, sole recipe ownership restored, no OOM/shared-memory residue,
all checksum settings restored, and Steam closed. The public restored preflight
passed all checks, including 20 tool calls. Underlying operator identity/status
files remain private; the detailed restoration assertions are reported by that
verification record rather than fully public raw host state.

Native Windows SWE was unsupported; no SWE score is claimed. The staged-import
repair passed eight new focused tests and the author's broader 124-test set;
an independent reviewer reran the eight focused tests. The full suite was not
clean: a retry reached 7,932 passes and 416 skips before an unrelated Pi process
smoke failure; an earlier run hit an HTTP timeout. Documentation-skill tests
passed 19/19. Ruff and MkDocs were unavailable in the environment. No promotion
was requested or performed; broader real coding and tool workflows remain a
separate qualification step.
