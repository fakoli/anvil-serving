# Voice pipeline independent verification gate

> **STATUS: PASSED.** This finding closes Anvil `voice-pipeline:T017` by
> independently checking the captured T010 local-loop proof, the captured T014
> official SDK Realtime proof, the delivery state of PR #151, and the follow-up
> verification PR #152.

## Inputs

| item | evidence |
|---|---|
| T010 local-loop proof | `docs/findings/2026-07-voice-local-loop-proof.md` row `2026-07-06T05:32:28Z`; session `C:\Users\sdoum\AppData\Local\Temp\anvil-voice-captures\local-loop-20260706T053128Z.session.json` |
| T014 Realtime SDK proof | `docs/findings/2026-07-voice-realtime-proof.md` row `2026-07-06T06:09:48Z`; session `C:\Users\sdoum\AppData\Local\Temp\anvil-voice-captures\realtime-sdk-20260706T060944Z.session.json` |
| T014 PR | `https://github.com/fakoli/anvil-serving/pull/151` |
| T014 commit | `bf4cac6f37c9186196e3ff5efb47d4231a62f791` |
| T014 merge commit | `59b3be3e496c835c298a3ade2116aea68aa31480` |
| T014 Anvil proof | `C:\Users\sdoum\.anvil\workspaces\anvil-serving-7a68b006\.anvil\proofs\voice-pipeline-T014-E006230.json` |
| T017 PR | `https://github.com/fakoli/anvil-serving/pull/152` |

## Independent Checks

McClintock verified the captured proof artifacts directly:

- T010 `local-loop-20260706T053128Z`: `turns_completed=1`,
  `barge_in_observed=true`, `barge_in=true`, `ttfa_ms=1517.49`,
  `turn_latency_ms=27379.24`, `output_bytes=832528`.
- T010 route proof: `ok=true`, HTTP `200`, provider `fast-local`, model
  `qwen36-27b`, tier `local`, `validation_errors=[]`.
- T010 event/audio evidence: barge-in interrupted generation 3, stale audio was
  dropped, input froze after barge-in, turn 4 completed, and both WAVs were
  valid 16 kHz mono PCM.
- T014 `realtime-sdk-20260706T060944Z`: `acceptance_errors=[]`,
  `connected=true`, `openai_version=2.44.0`, `barge_in_sent=true`,
  `cancelled_response_seen=true`, `completed_after_barge_in=true`,
  `output_after_cancel_events=0`, `output_after_cancel_request_events=0`,
  `input_audio_bytes=266228`, `output_audio_bytes=75800`, `event_count=50`.
- T014 raw events: `response.cancel` targeted `resp_1`, `resp_1` ended
  `cancelled`, no cancelled-response audio deltas arrived after cancel, and
  `resp_2` completed with 73,070 audio bytes.
- T014 official SDK path: harness imports `openai.AsyncOpenAI`, uses
  `client.realtime.connect`, and the installed package was `openai 2.44.0`.
- T014 signed Anvil proof verified with a temporary trust list:
  `verified=true`, task `voice-pipeline:T014`, signer `318e4f9745a5fb5e`.

Einstein verified delivery coherence:

- `git status --porcelain` was empty before T017 doc edits.
- PR #151 merged at `2026-07-06T06:21:54Z`.
- PR #151 head was `agent/voice-pipeline-t014-realtime-sdk-proof` at
  `bf4cac6f37c9186196e3ff5efb47d4231a62f791`.
- The merge commit `59b3be3e496c835c298a3ade2116aea68aa31480` has parents
  `a511535159cfadb47c7255fe93196db9be5be913` and
  `bf4cac6f37c9186196e3ff5efb47d4231a62f791`, so `main` contains the T014
  implementation.
- CI passed: docs strict, lint, build/wheel smoke, Ubuntu py3.11/3.12/3.13,
  and Windows py3.11/3.12/3.13. The GitHub Pages deploy job was skipped as
  expected.

## Local Verification

Commands run for this gate:

```bash
git status --porcelain
gh pr checks
gh pr checks 152 --json name,state,bucket,startedAt,completedAt,link,workflow
anvil proof verify C:\Users\sdoum\.anvil\workspaces\anvil-serving-7a68b006\.anvil\proofs\voice-pipeline-T014-E006230.json --trust %TEMP%\anvil-t014-trust.txt --json
```

The T017 branch was rebased onto `origin/main` after PR #151 merged, so its
diff contains only this independent-verification finding.

## Decision

T017 is satisfied. The local-loop and Realtime live proofs are coherent from
captured evidence, T014 is merged to `main`, PR #152 CI is green, and the
T017 delivery artifact is this finding.
