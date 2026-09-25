# Campaign friction log

Record entries while the campaign is running. Preserve the earliest actionable
failure and distinguish configuration issues, product defects, and model
behavior. For each configuration trial, link its version and parent, hypothesis
and dated source, intended/effective setting delta, targeted and regression
results, final qualification evidence, and next action or stopping reason.
A stopped cell is not a rejected model; budget-limited investigation stays
unresolved. Use the qualification skill's configuration-search workflow.

| Time | Stage | Category | Earliest actionable evidence | Immediate disposition | Durable fix-forward artifact | Independent verification | Status |
|---|---|---|---|---|---|---|---|
| 2026-09-25T09:46:00Z | scout | failure | [Startup attempt 1](startup-attempt-1.txt): `unknown argument: --image-token-budget` after successful model SHA check | Removed unsupported card-sample flag from the managed recipe and retried | [Revised recipe](../../../configs/qwen38-swift15-ninfer-dflash2-rtx5090-post-profile.toml) | [Second startup](startup-attempt-2.txt) passed health, exact model SHA, and direct preflight | closed |
| 2026-09-25T09:36:00Z | scout | missing-identity | Router host SSH identity changed from the pinned known-hosts identity | Verified the trusted current router host identity and used its declared SSH alias | Private exact router preview and installed-config receipt | Routed preflight and full fleet no-change repeat passed | closed |
| 2026-09-25T09:37:00Z | feasibility | manual-workaround | No managed verb for the post's 450 W GPU cap | Applied exact cap through the host utility and verified readback | [Managed power-cap ticket](../../../.tickets/2026-09-25-managed-gpu-power-cap.md) and [cap evidence](power-cap.txt) | 450.00 W readback retained on final selected serve | open |


| 2026-09-25T10:48:00Z | finalist | model-output | Default-thinking 512-token strict cell produced no visible output; no-thinking `observe` cells returned visible but 0 exact 256-word targets | Retained both failed controls and labeled throughput descriptive | [Failed default-thinking cell](capacity-4k-c1.json), [failed no-thinking strict probe](capacity-4k-c1-no-think-probe.json), and [successful descriptive population](capacity-4k-c1-visible-observe.json) | Request-level canaries and completion counts verified; strict output remains open | open |
| 2026-09-25T10:55:00Z | finalist | serving-capacity | [128K/C4 artifact](capacity-128k-c4-visible-observe.json) completed 5/10; managed engine logs showed five HTTP 503 request-queue timeouts | Withheld throughput and C4 long-window qualification; kept final serve profile fixed | Dated finding records the exact failure and admission limit | Container remained healthy; 128K/C1 completed 10/10 | open |
| 2026-09-25T10:55:00Z | finalist | product-defect | SSE error chunks were ignored when capacity requests had no observer, producing a misleading stream-without-visible-content failure | Corrected request parser to raise server error independently of observer | `anvil_serving/benchmarking/requests.py` and `tests/test_benchmark_stream_errors.py` | Targeted benchmark/request tests passed | closed |
| 2026-09-25T11:31:00Z | quality | worker-prerequisite | First SWE asset preparation returned `harness_image_failure`; read-only diagnosis found Docker Desktop stopped on the isolated worker | Started the existing worker Docker Desktop application and repeated managed preparation | Pinned managed SWE assets, worker preflight, and private failed-attempt artifact | Repeated preparation and Docker-capability preflight passed; no model request occurred in the failed attempt | closed |
| 2026-09-25T11:32:00Z | quality | harness-input | Durable SWE attempt 001 failed `explicit_swe_selection_required` before model execution because the `scout` profile requires exactly five explicit IDs | Reused the frozen five-case selection from the prior coding lane | Private specs and failed-attempt artifact; public [run plan](run-plan.md) now names the selection contract | Attempt 003 completed official grading 5/5 with the five IDs | closed |
| 2026-09-25T11:34:00Z | quality | worker-runtime | Durable SWE attempt 002 failed `unsafe_cache_path` before model execution: the installed CLI launcher used Homebrew Python while the pinned SWE venv was built with uv Python | Launched the managed CLI from the same Python base used for asset preparation | `anvil_serving/benchmarking/swe.py` now validates the pinned isolated venv against its `pyvenv.cfg` rather than requiring the worker launcher to be the same interpreter; [SWE tests](../../../tests/test_swe_benchmark.py) cover the path | Attempt 003 passed preparation, preflight, agent execution, and official grading 5/5; the revised function also passed against the real Mac venv with a different launcher | closed |

For skill/process lessons, also link the baseline and candidate instruction
identity, applicability limits, development and independent-case results,
reviewer, observed cost, accepted/rejected/unresolved decision, and expiry
trigger. A reflection note alone is not an independently validated lesson.

If there was no friction, retain the file with one sentence stating that no
manual workaround, ambiguity, missing identity, unsafe default, repeated
command, or actionable failure was observed. A retry closes the incident only
when a durable disposition and its independent verification are recorded.
