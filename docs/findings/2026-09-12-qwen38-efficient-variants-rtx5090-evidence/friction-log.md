# Campaign friction log

Record entries while the campaign is running. Preserve the earliest actionable
failure and distinguish product defects from model behavior.

| Time | Stage | Category | Earliest actionable evidence | Immediate disposition | Durable fix-forward artifact | Independent verification | Status |
|---|---|---|---|---|---|---|---|
| 2026-09-12T13:12:00Z | RESEARCH | missing-identity | Worktree launcher reports 1.0.0 while source release is v1.2.1. | Continue because module path and commit are exact; retain mismatch. | Ticket if still present after campaign. | Worktree module path and commit check. | open |
| 2026-09-12T13:14:00Z | RESEARCH | manual-workaround | Repo-scoped controller tools unavailable in this session. | Use verified worktree CLI fallback. | Environment attachment explicitly deferred. | Tool catalog inspection. | open |
| 2026-09-12T13:18:00Z | RESEARCH | missing-identity | Product and Windows GPU views succeeded; Docker Desktop lacks nvidia-smi and a stale WSL distribution could not attach. | Do not infer empty lane; use managed owner inventory. | Unrelated WSL repair deferred. | Recipe-container inventory plus Windows telemetry. | open |
| 2026-09-12T13:15:00Z | RESEARCH | ambiguous-output | Healthy incumbent remains loaded but current registry no longer resolves its selector; one older same-family container is exited unhealthy. | Preserve exact labels/image/revision and retained healthy container for restoration. | Added guarded `models recipes stop`/`start` support so a registry-independent exact container can be retained and restored. | 349 focused CLI/model lifecycle tests plus real incumbent stop/start dry-run. | resolved-before-mutation |
| 2026-09-12T13:41:04Z | BASELINE | model-behavior | Incumbent returned 107–126 of the requested 128 exact code words in all five strict capacity requests. | Preserve the 0/5 strict result; use the same observe-mode workload only for descriptive latency. | No product change: strict mode already failed closed and retained sanitized response timing. | Incumbent deterministic preflight and repeated quality suites passed independently. | open-model-defect |
| 2026-09-12T13:47:41Z | DOWNLOAD | operator-error | First live cache-inventory retry used unsupported `--json-out` and failed before inspection. | Read focused help and rerun with supported `--output`. | No code change needed; CLI usage error was explicit and non-mutating. | The corrected inventory showed one growing Signal partial and the download later verified exact completion. | resolved |

## Live fix-forward addenda (2026-09-12)

- The earlier lifecycle row was provisional, not a general-release approval.
  Independent review found missing-registry, fingerprint handoff, readiness,
  deadline, admission, and transport-contract gaps. Regression tests reproduced
  and fixed them through the managed CLI/MCP path. General merge/release remains
  held on the shared host-lifecycle lock and process-identity allowance contract;
  see the tracked retained-lifecycle/evidence-gaps ticket. Serialized campaign
  use was conditionally accepted. A live restart preview refused the active
  Qwopus peer with no mutation.
- Windows reports the pre-existing camera utility as a compute client. It is
  protected starting state, not a model process to terminate. The explicit
  baseline-PID allowance keeps the Docker-peer and 1024 MiB memory guards;
  immediate process identity/start-time verification is required for restoration.
- Signal and Swift each failed all five original strict 128-word requests.
  Swift still failed 5/5 with speculation disabled, ruling out speculation as
  a sufficient fix for that case. A separately declared 32-word diagnostic then
  passed 5/5. Preserve the original failure; never rank repetitive cap-hitting
  output as useful speed. Matched Signal no-spec also failed all five128-word
  requests, so removing speculation is not a sufficient fix for either model.
- Signal and Swift reasoning scouts each passed 9/10 and exhausted the same
  computer-science question's 5120-token total budget. A larger-budget retry
  fixture preserves the original question and independent answer key.
- Qwopus reasoning scout passed 1/10 under exact output syntax: eight visible
  answer-format failures and one reasoning-budget exhaustion. Disabled mode
  passed 24/30 repeated attempts, with two questions exhausting the1024-token
  visible budget. Reinforced syntax plus2048 visible tokens passed7/10; the
  mixed mitigation is diagnostic, not a matched performance cell.
- The first full test run stopped after5332 passes/340 skips on the intentionally
  changed MCP public-schema snapshot. Updated the snapshot after reviewing the
  typed allowance/stop/start contract; focused foundation tests passed. A fresh
  full run is in progress. No raw benchmark failure was overwritten.
- The documented old `eval benchmark run` spelling failed at parsing; focused
  help selected the supported `eval benchmark capacity` command. External-only
  quality normalization also emits warnings for unselected built-ins. Both are
  recorded in the retained-lifecycle/evidence-gaps ticket, not disguised as
  model failures.

All telemetry and logs cited here are diagnostic; a passing code test does not
qualify a model. Final dispositions must be reconciled before publication.

## Windows Pi regression investigation

The second full run stopped after7961 passes/350 skips with Pi exit134.
A bounded isolated-process reproduction retained the earliest error:
`Assertion failed: ncrypto::CSPRNG(nullptr, 0)`. The test's minimal environment
omitted `SystemRoot`; supplying only that Windows prerequisite changed the
same startup probe from exit134 to a successful RPC response. The fixture
now preserves that one OS value without inheriting credentials.

The real smoke then exposed an independent transport defect: Windows
`select()` cannot poll subprocess pipes (Python's official select documentation).
A new real-child pipe regression failed before the fix. The Windows path now
uses stdlib `_winapi.PeekNamedPipe` and reads at most the reported available
bytes; POSIX behavior is unchanged. Six focused tests, including actual
Pi0.85.1 history/fork/resume/model controls, passed. Independent review and a
fresh full run remain required. Durable code and tests are in
`anvil_serving/workbench_app/pi_rpc.py` and `tests/workbench/test_pi*.py`.

## Segmented HTTP test-reader correction

The next full run stopped after 2,103 passes and 323 skips. Authoritative router
stderr showed the intended `504 startup_timeout`; the test retained only
headers because its unframed `0\r\n\r\n` search matched the last request-ID
header ending in hex zero. A deterministic segmented-socket regression
reproduced this before the fix. The test helper now searches only the body for
that final-chunk marker; other marker searches and production router code are
unchanged. All 19 request-runtime tests passed. A new full run is in progress.
Durable artifact: `tests/router/test_request_runtime.py`.

## Final operational dispositions

The time-ordered entries above retain what was known at each failure; this
section supersedes their open campaign statuses without deleting failures.

- Installed-version drift, unavailable session controller tools, and stale WSL
  attachment are deferred product/environment gaps in the tracked lifecycle
  ticket. Exact module/commit identity and the managed CLI provided a verified
  execution path; no raw Docker mutation or unrelated WSL repair was used.
- Managed retained-container restoration completed with exact camera process
  identity/start-time checks, no competing candidate, and a healthy incumbent.
  See `restoration.json`, `recovery-admission.json`, and `restored-state.json`.
  General lifecycle release remains held on shared locking and immutable
  process-identity attestation; bounded campaign acceptance is not release.
- Signal no-spec's larger 9,216-token retry still exhausted its reasoning
  budget without an answer. This also changed speculation versus the original
  scout, so it is not a one-variable budget comparison. Qwopus syntax/budget
  mitigation improved strict passes to 7/10 but left wrong answers and a cap.
  Minitron retained wrong-answer and output-adherence failures. These profiles
  are rejected as replacements under the tested contract, not silently fixed.
- The revised 32-word diagnostic passes for incumbent, Signal no-spec, Swift
  no-spec, and Qwopus do not erase the original 128-word failures. Swift's warm
  cache is separate from the other cold-cache populations. CPU contention was
  not controlled; no diagnostic is promoted to a finalist latency result.
- Independent Sol review accepted the bounded Pi pipe correction and HTTP
  test-reader correction with no blocking findings. Focused gates passed;
  final broad regression verification is recorded separately when complete.
- A publication regression caught this new dossier's missing canonical
  `Review narrative` label after 2,918 passes and 325 skips. Restoring the
  required dossier structure passed all 19 benchmark-documentation tests.
  Evidence-consistency tests also caught an unfinished summary and a
  terminal-newline mismatch in the recipe copy; both were reconciled against
  native artifacts and the exact registry, not by weakening tests.

Reusable operational lesson: restore a registry-missing incumbent by its
retained immutable container only after independent admission and identity
checks, then prove functional health. The engineering-learning skill is
unavailable in this session; this evidence-linked invariant and the tracked
ticket are the durable fallback.

## Final verification closure

The final full suite passed 8,117 tests with 391 skips in 381.30 seconds.
Ruff, 22 focused documentation/evidence checks, strict MkDocs, 669-file
Markdown link validation, five template JSON parses, skill validation, and
the full 1,164-file CLI reference audit also passed. The skipped tests do not
prove missing live client, full-context, or model-quality gates.

Independent evidence review caught a missing dedicated Signal license source
entry. The pinned publisher card declares Apache 2.0; the source registry now
retains its exact URL, date and limited interpretation. Windows checkout
conversion could also change the two canonical recipe bytes while evidence
copies are byte-preserved. Exact `.gitattributes` entries now preserve those
registry bytes. Native artifact line endings and intentional registry terminal
blank lines are retained for their hashes; whitespace validation explicitly
accepts CR-at-EOL and those terminal blanks. No evidence values were changed.
