# Campaign friction log

| Stage | Category | Evidence | Disposition | Status |
|---|---|---|---|---|
| R1 quality | sampling | [Oracle attempts](native/oracle-attempts.json) | R1 temperature-one strict Oracle failed 17/18. Temperature-zero was the frozen sampling remedy and passed 54/54; R1 vision/context also passed before the R1 reserve stop. | closed |
| R1 quality | reserve | [R1 cancellation](native/swe-evidence.json) | 3879 MiB reserve was below the 4096 MiB floor; the partial SWE run was retained and R2 used a lower utilization setting. | closed |
| R2 quality | quality gate | [Completed SWE](native/swe-evidence.json) | R2 resolved 3/5, below the four-case replacement floor; dependent capacity, stability, and DFlash cells stopped. | closed |
| source verification | test custody | private full-suite receipts | A pre-existing `/tmp/.git` invalidated the first temporary-root attempt; an AF_UNIX path then exceeded its limit. The final clean, short, non-Git temporary root passed the full suite; no source change was required. | closed |
| source verification | permissions | private offline-harness receipt | Group-writable permissions on the new worktree blocked the wrapper. The new worktree root and parent were tightened, then focused tests passed. | closed |
| campaign logs | CLI metadata | private bounded-log receipt; tracked gap `.tickets/2026-10-16-benchmark-logs-json-help-mismatch.md` | `logs --json` was rejected despite focused help listing the flag. Plain bounded logs emitted a JSON body and was used only as a campaign workaround; help/metadata alignment remains a deferred product gap. | open |
| publication | control availability | [restored native SWE](native/restored-baseline-swe-native.json), [closing status](native/restored-control-final-status.json) | The matched control completed 3/5 with closing restoration telemetry. Only the bounded per-instance/resolved-count comparison is recorded. | closed |

The R2 rejection is scoped to this exact profile and bounded workload. It does
not diagnose a model-family or runtime defect.
