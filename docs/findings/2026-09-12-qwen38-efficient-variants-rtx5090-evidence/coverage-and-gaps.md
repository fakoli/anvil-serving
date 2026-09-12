# Request-to-evidence coverage

| Requested outcome | Retained evidence | Final disposition |
|---|---|---|
| Pull latest changes | Exact source revision in `configuration.json` and `run-plan.json` | Completed before testing in an isolated campaign worktree; unrelated working changes preserved. |
| Better than current Qwen3.8 27B on this RTX 5090 | `summary.json`, native quality and capacity artifacts | No better replacement established. Signal is the strongest new efficiency research lead; incumbent retained. |
| Test four variations, including fine-tunes | Four original preflight and reasoning artifacts; `recipes.toml` | Signal, Swift, Qwopus Flash, and 20B Minitron loaded and passed six functional checks; two additional no-spec controls tested. |
| Explore shorter reasoning | Signal/Swift/incumbent reasoning artifacts | Signal/Swift used 16.5%/16.1% fewer total completion tokens on ten questions, with 9/10 versus incumbent 8/10 strict passes. Separate reasoning-token counts unavailable. Not broad quality proof. |
| Hardware-specific recipes | `feasibility.json`, `configuration.json`, both recipe registries | Four exact single-5090 recipes loaded. Candidates configured at 64K versus incumbent 262K. Actual preflight input was 47,349 tokens, not a validated 57K input plus 8K output envelope. |
| Stop at 50% Codex usage | `campaign-state.json` | Checks observed 19% initially and 28% at final verification; threshold not reached. |
| Unattended selection/promotion | `run-plan.json`, `summary.json`, `restoration.json` | Authority present; no challenger cleared replacement gates. Selected incumbent restored healthy, all candidate containers unloaded, protected process preserved. |
| Fix forward with bounded attempts | `friction-log.md`, independent review records, tracked lifecycle/evidence-gap ticket | Lifecycle and Windows regressions repaired with independent tests. Model failures investigated with no-spec, revised-output, syntax, and budget diagnostics; original failures preserved. General lifecycle release held on shared locking and immutable baseline process attestation. |

Completion means bounded research, four-profile measurement, selection and
restoration are complete. It does not mean a new model qualified, a production
SWE/client workflow passed, 262K challenger context was tested, or the lifecycle
patch is approved for general release. No test result hides these gaps. No new
promotion occurred.
