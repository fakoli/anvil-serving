# OpenClaw Talk voice latency candidate A/B status report (2026-07-08)

> Status: synthesis of the `voice-latency-model-ab` evidence set. This is not
> completed candidate A/B evidence: the baseline was measured, but the candidate
> rows failed before reaching the LLM stage. This report does not promote a
> model, change router policy, change `[router].profile_path`, or alter OpenClaw
> production model selection.

## Executive Determination

Do not promote a new voice LLM candidate yet.

Candidate model A/B testing is incomplete. The successful measurement is the
current baseline only. I should not treat the retained candidate failure rows as
proof that the candidate models were tested.

The current OpenClaw Talk path is functionally healthy: spoken turns reach the
main chat session, session context is retained, tool calls work, hidden control
text is absent from visible user prompts, and the duplicate-message spam did
not recur in the checked session.

The latency evidence is not yet a valid candidate-model A/B. The only
successful timing row is the Mini-run baseline for `baseline-qwen36-27b` on the
`mini-audio` profile. The candidate rows were retained as evidence, but they
failed before STT because they were executed from a non-gateway checkout whose
`127.0.0.1` was not Fakoli Mini's loopback. Those failures prove the topology
guardrail, not candidate model speed or quality.

Final recommendation: keep the current production fast Talk model in place and
rerun the actual candidate tests from Fakoli Mini with `--candidate-overlay`.
Only reconsider promotion after comparable candidate timing and live Talk
validation both pass.

## What Was Tested

The evidence set covered these phases:

| Phase | Evidence | Result |
|---|---|---|
| Baseline | `mini-audio` using `baseline-qwen36-27b` through Fakoli Dark router | Successful timing row captured |
| Shortlist | Qwen3 dense NVFP4 and Gemma 4 dense candidates | Candidate set defined; no promotion implied |
| Matrix | Baseline plus candidate profiles and tool-relevant weather turn | Baseline measured; candidate rows failed before STT from wrong host context, so candidate models were not benchmarked |
| Live Talk validation | Fakoli Mini gateway and voice runtime with Dark router | Functional Talk path passed with warning-only COLO smoke |
| Final recommendation | Promotion readiness synthesis | Needs more comparable Mini-run data before model change |

## Current Reference Topology

The interpretation depends on where a command is executed:

| Host | Owns |
|---|---|
| Fakoli Mini | OpenClaw Gateway, Anvil Voice Realtime server, `mini-audio` STT/TTS loopback endpoints at `127.0.0.1:30010` and `127.0.0.1:30011` |
| Fakoli Dark | Anvil router at `http://100.87.34.66:8000/v1`, candidate LLM serves, optional Dark audio bridge ports |
| Mini proxy profile | `mini-dark-audio-proxy` loopback endpoints on Mini at `127.0.0.1:30110` and `127.0.0.1:30111`, only valid after Mini-side proxy listeners are up |

A Windows/operator checkout cannot validate Mini-local audio by calling its own
`127.0.0.1`. Candidate runs that fail on those loopback ports from a non-gateway
host are topology negative controls.

## Timing Results

The successful measured timing row is the rerun Mini baseline:

| Profile | Candidate | Status | TTFA ms | Turn ms | STT ms | LLM ms | TTS ms |
|---|---|---|---:|---:|---:|---:|---:|
| `mini-audio` | `baseline-qwen36-27b` | measured prior rerun | 611.29 | 789.06 | 106.28 | 356.82 | 325.95 |

Stage share of total turn latency:

| Stage | Time ms | Share |
|---|---:|---:|
| STT | 106.28 | 13.5% |
| LLM | 356.82 | 45.2% |
| TTS | 325.95 | 41.3% |

Determination from this row: the LLM and TTS stages are co-dominant. STT is not
the current bottleneck in the successful baseline measurement.

## Candidate Matrix Outcome

The candidate rows are retained in
`tests/fixtures/operator_workflows/voice_latency_model_ab_matrix.json`, but none
of them produced a valid latency comparison or a candidate model timing:

| Candidate row | Status | Determination |
|---|---|---|
| `candidate-qwen3-32b` generated-audio run | Failed before STT | Invalid latency comparison; wrong loopback context |
| `candidate-gemma4-12b` generated-audio run | Failed before STT | Invalid latency comparison; wrong loopback context |
| `candidate-gemma4-e4b` generated-audio run | Failed before STT | Invalid latency comparison; wrong loopback context |
| `candidate-qwen3-32b` weather/location tool turn | Blocked before live tool validation | Cannot evaluate tool-call regression yet |

The candidate failures should not be interpreted as model failures, and they
should not be counted as completed model tests. They show that LLM-only
candidate overlays must be run from Mini, or through a verified Mini/Dark bridge
profile, so that the STT/TTS path is valid before the candidate LLM is measured.

## Functional Talk Result

Live OpenClaw Talk validation on the Mini-to-Dark path passed the behaviors that
were causing concern earlier:

| Behavior | Result |
|---|---|
| Session transcript delivery | Visible spoken turns reached the active main session |
| Session memory | Conversation context persisted across turns |
| Tool calls | Weather request produced an `exec` tool call and weather result |
| Hidden forced-consult text | Not present in the checked visible session history |
| Duplicate message spam | No repeated dense burst recurred after cleanup in the checked window |
| COLO smoke | Exited `0`; verdict was `warn` because the command did not include `--run-generations` |

The live COLO interaction benchmark recorded 10 interaction benchmark requests,
10 HTTP `200` statuses, and finish reason `stop` for all 10. Latency p50/p95
was `568.6 ms` / `1259.9 ms`, and exact-generation throughput p50/p95 was
`82.77` / `171.82` tokens/sec.

## Pass / Fail

| Gate | Verdict | Reason |
|---|---|---|
| OpenClaw Talk functional path | Pass | Memory, tools, transcript delivery, and cleanup checks passed |
| Hidden prompt pollution fix | Pass | Checked session contained zero visible forced-consult/control-text markers |
| Duplicate spam fix | Pass with monitoring | Historical burst remains in old logs, but no new sustained repeat sequence was observed |
| Baseline latency capture | Pass | Mini-run baseline captured with TTFA `611.29 ms` and turn latency `789.06 ms` |
| Candidate latency A/B | Incomplete / fail for promotion evidence | Candidate rows did not reach the LLM stage, so candidate models were not actually tested |
| Model promotion readiness | Fail / not ready | No successful candidate timing plus live Talk regression check exists |
| Cost-control gate | Pass | No cloud model path or metered promotion was introduced |

## Final Decision

Keep the current `baseline-qwen36-27b` fast Talk path in production.

Do not promote Qwen3-32B, Gemma 4 12B, or Gemma 4 E4B from the current evidence.
They remain untested candidates, not rejected models. The evidence is
insufficient because the candidate runs did not exercise the full voice path
from the correct host/topology and did not reach candidate LLM timing.

Latency work should continue on two tracks:

1. Re-run model candidates from Fakoli Mini using the same `mini-audio` profile
   and only changing the LLM via `--candidate-overlay`.
2. Investigate TTS latency/chunking alongside LLM latency, because the baseline
   shows TTS is nearly as large as the LLM stage.

## Required Next Evidence Before Promotion

Before any production model or routing profile change, capture:

| Required evidence | Acceptance condition |
|---|---|
| Fresh Mini baseline JSON | Same host, profile, prompt shape, and evidence output path as candidate runs |
| At least one successful candidate overlay JSON | Candidate differs only by LLM overlay; STT/TTS profile remains comparable |
| Tool-relevant live Talk turn | Weather/location or equivalent tool call succeeds after candidate selection |
| Session transcript check | Spoken user and assistant turns land in the main session without hidden control text |
| Duplicate-message scan | No sustained repeat burst after candidate live turn |
| Human promotion gate | `router_promote` or `anvil-serving router promote` remains explicitly human-approved |

Recommended first candidate order remains:

1. `nvidia/Qwen3-32B-NVFP4` as the closest dense Qwen operational comparison.
2. `google/gemma-4-12B-it` as the main Gemma dense scout.
3. `google/gemma-4-E4B-it` only to measure the fastest plausible lower-quality
   bound for spoken responsiveness.

## Source Evidence

- `docs/findings/2026-07-07-voice-latency-baseline.md`
- `docs/findings/2026-07-07-voice-latency-model-shortlist.md`
- `docs/findings/2026-07-08-voice-latency-candidate-matrix.md`
- `docs/findings/2026-07-08-openclaw-talk-live-validation.md`
- `docs/findings/2026-07-08-voice-latency-final-recommendation.md`
- `tests/fixtures/operator_workflows/voice_latency_model_ab_matrix.json`
