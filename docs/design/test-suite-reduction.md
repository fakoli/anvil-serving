# Test-suite reduction

Observed: 2026-10-31. Status: Python source validated; 80% case target unmet; browser expiry validation pending.
Merge base: `4ba1b56ee794d6666d1db2ab4c3d756c839d51aa`.
Locally tested source: `7c47af121759bac9f6214c0fbcae942f20d3876d`.

The source change removes **69.46% of collected Python cases** and **50.78% of
physical Python test/helper lines**. The requested **80% case reduction remains
unmet**: it would require at most 2,820 cases, another 1,487 below this candidate.
The accepted tradeoff permits fewer dedicated checks for noncritical behavior
while preserving identified critical contracts and the existing 70% combined
line-and-branch coverage floor.

## Current merge candidate

| Measurement | Baseline | Candidate |
| --- | ---: | ---: |
| Collected Python cases, including platform skips | 14,104 | 4,307 |
| Distinct collected Python test functions | 8,112 | 2,588 |
| Python test/helper files | 573 | 543 |
| Physical Python test/helper lines | 197,734 | 97,318 |
| Physical production Python lines | 208,744 | 208,693 |
| Complete local run with coverage | Not reprofiled | 132.18 seconds |
| Complete normal offline run | Not measured | 108.75 seconds |
| Combined coverage | Not reprofiled | 72.51% |

Both final runs passed **4,191 cases**, with **116 existing skips** and **20 passed
subtests**. The normal run meets the 120-second local target. Statement coverage
is 76.14%; branch coverage is 63.16%; the unchanged 70% gate applies to their
combined score. The current merge base was collected and inventoried, without
repeating its full runtime profile. One existing
multiprocessing fork deprecation warning remains.

The [results artifact](test-suite-reduction-results.json) records the source
fingerprint, environment, counts, coverage, and sanitized per-group timings.
Its `merge_delivery` record describes this combined tree; the root records
preserve the earlier campaign measurements. Eight new upstream behavior cases
remain, together with the upstream ownership, cache, and native-owner fixes.
Python source hashes remained unchanged across the accepted runs. The local
checks used Python 3.12; Python 3.11/3.13 CI and native platform execution remain
separate acceptance surfaces. These are source checks; they do not certify live deployment.

Physical lines include comments and blanks. The reduction includes removal of
8,457 vacant lines left between deleted definitions; executable assertions and
helpers were also removed. Cases are counted from ordinary pytest collection.
Existing platform/environment skips remain in the denominator. Counts do not
claim a reduction of Go, Node, Playwright, or live qualification cases.

## Earlier profiling

The initial campaign used baseline `7b5158b724ca715c85a3302091d2a2b40f253da0`.
Its reduced Python bytes match commit
`1c5229ef4cbc31ea3cb7aa1afef738425a189ac8`: 14,095 to 4,299 cases, 72.42%
coverage, 115.87 seconds without coverage, and 142.69 seconds with coverage.
Compared with its 752.59-second coverage baseline, that was an **81.04% runtime
reduction**, not an 80% case reduction. These historical timings are separate
from the current merge-base collection and candidate runs above.

The historical complete local baseline reported 13,977 passed, 116 skipped, and two
fixture failures caused by an unrelated free-space check in a timeout test.
The fixture now injects sufficient disk space so that it actually reaches the
timeout contract. The longest measured baseline case was 35.94 seconds; no
individual Python case or module exceeded two minutes. The verified ten-minute
problem was aggregate suite time. Historical CI took 13–14 minutes; local timing
samples are not a claim about the eventual CI runtime.

An intermediate 3,615-case selection experiment passed 3,499 cases with 116 skips
in 137.50 seconds under coverage, measuring 71.93%. Review restored additional
critical checks and retained full parameter matrices where rows protected
different mechanisms. The experimental selection plugin is not part of the
source change. Final acceptance used the entire physically reduced suite.

See the [baseline inventory](test-suite-reduction-baseline.json) for collection,
CI, other-language inventories, and profiling methods. The [deletion audit](test-suite-reduction-audit.json) records all 62 proposed whole-module removals and the critical representatives restored in 31 of them.

## What changed

- Removed ordinary positive, default, report-format, helper-branch, documentation
  prose, and repeated CLI-help checks. Their dedicated regression specificity is
  intentionally reduced; the supported product features remain.
- Removed unreferenced test helpers and imports. Selected parameter lists were shortened, preserving identified boundary classes
  and retained case identities. Larger
  ownership, path, recovery, protocol, and privacy matrices remain where rows
  exercise distinct mechanisms.
- Set local HTTP test servers to the existing 0.01-second polling pattern and
  joined handlers before reading results. A 124-case sample fell from 44.51 to
  2.34 seconds. No production server polling policy changed.
- Repaired gate/finalizer synchronization and a watchdog fixture composition
  race. The real detached worker/watchdog, cancellation, and process custody
  checks remain. The coordinator closure test substitutes its long drain wait;
  the direct fresh-zero drain and suite-window checks remain in the suite.
- Replaced drain deadline waits with a module-local clock after real ownership
  scans. Synthetic service recovery still performs both stable status scans,
  with no real-time idle between fixed runner responses.
- Reduced surplus concurrent-read repetitions and WAL setup commits while
  preserving actual overflow thresholds, simultaneous readers, a pinned WAL
  reader, and checkpoint assertions.
- Replaced seven identical telemetry timestamp normalizers with one shared
  implementation and retained its focused UTC/error checks: 50 fewer production
  lines with the same behavior.
- Added CI JUnit timing artifacts and contribution guidance against incidental
  assertions, unneeded matrices, real sleeps, and repeated fixture setup. The
  70% coverage gate remains. The existing Windows job now also selects the retained
  native custody checks; that platform execution is not verified by the local Linux run.

## Retention and review

Per-case coverage supplied overlap evidence, not a correctness oracle. The
retained core includes identified authentication/authorization, credential
privacy, exact model selection/no fallback, destructive-operation ownership,
recovery/idempotency, durable state, evidence binding, and real process/stream
contracts. Independent review restored additional guards for imports/backups,
model-free capacity, readiness, redirects, malformed state, protocol translation,
terminal media state, and failed-tool evidence. Parent review also restored the
real chat token checks and the direct drain check needed by the faster fixture.

Two process-local fault probes verified retained assertions: accepting a wrong
token caused the expected 401 check to fail with 200; substituting a default tier
for an unknown alias caused the expected 404 check to fail with 200. The probes
changed only their disposable test processes. They are bounded checks of those
mechanisms, not a full mutation score or a proof that every former assertion is
represented.

Review covered selected critical mechanisms and high-risk omissions; it did not
establish semantic equivalence for every removed case. The coverage floor is a
minimum, not a guarantee against regressions. Ordinary behavior has deliberately
less dedicated regression coverage under the accepted tradeoff.

Router usage samples were incomplete. They supported retaining chat, vision,
memory, and evaluation journeys but could not establish that another capability
was unused. No product family was retired from that evidence.

## Research on LLM-generated tests

The research supports reviewing assertion quality and maintenance cost. It does
not establish that 80% of this repository was redundant, or that LLMs authored
every questionable test.

| Primary source | Finding and application |
| --- | --- |
| [Quality Assessment of Python Tests Generated by Large Language Models](https://arxiv.org/abs/2506.14297), EASE 2025 | Most evaluated generated suites had an error or test smell. Prompt detail reduced some errors while increasing some smells. Require a distinct behavior and a credible oracle; adding instructions alone does not guarantee lean tests. |
| [Evaluating LLMs Effectiveness in Detecting and Correcting Test Smells](https://arxiv.org/abs/2506.07594), June 2025 study | Automated refactoring sometimes introduced new smells or reduced coverage. Use independent review and executable checks for deletion batches. Smell detection alone cannot authorize deletion. |
| [Do Coverage and Mutation Scores of LLM-Generated Test Suites Correlate with Their Effectiveness?](https://arxiv.org/abs/2607.22880), ISSTA 2026 | Coverage and mutation usefulness depends on whether tests characterize sound code or must expose existing bugs. Keep real regression cases and explicit expected behavior; neither percentage alone proves retained quality. |
| [Google: Change-Detector Tests Considered Harmful](https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html), 2015 | Tests that reproduce implementation structure can break on harmless changes without detecting defects. Review exact prose checks, reconstructed expected values and incidental mock-call assertions first. |
| [Software Engineering at Google, Testing Overview](https://abseil.io/resources/swe-book/html/ch11.html), 2020 | Choose small tests for narrow behavior and enforce resource boundaries. Keep a small number of real transport/process checks where isolation would erase the behavior under test. |

All sources observed 2026-10-31. Older guidance supplies principles; the studies
are evidence about their evaluated models and datasets, not today's agent or
this codebase. The local working hypothesis is that per-change test accretion,
repeated checking at multiple layers and expensive fixture lifecycles explain
part of the burden. Test authorship and causal contribution remain unmeasured.

## Remaining target and maintenance policy

Reaching 80% requires reviewing another 1,487 currently retained cases. Next
candidates are duplicated caller checks of shared validators and equivalent
parameter classes. Each further deletion needs a concrete retained mechanism or
an explicit noncritical coverage loss, followed by the coverage gate and focused
regression checks. Removing supported features requires evidence and a separate
product decision. The present change does not claim the numerical target is met. A final bounded
audit found only small additional ordinary candidates; the large surviving
matrices protect different authorization, custody, recovery, and protocol
mechanisms. Coverage overlap alone does not justify deleting them.

For new work, locate existing behavior coverage first. A bug fix gets the
smallest check that fails with the defect. Prefer observable outcomes and
representative equivalence classes; retain distinct trust-boundary, recovery,
protocol, and platform failures. Record case/line/runtime changes. Moving checks
into loops, skip markers, or a different lane earns no deletion credit.

Use the existing supported command:

```bash
python scripts/run_tests.py tests/ -q --durations=40
```

Use focused paths while editing. Coverage-on timing and the normal offline run
are separate measurements; the latter measured 108.75 seconds against the
120-second target.

The Connect expiry fixture now uses the supported 60-second session minimum,
down from 120 seconds. It retains the real pre-expiry stream checks, one-second
closure bound, replay refusal, and 210-second overall timeout. Its Go profile
check passes, but the end-to-end run stopped before readiness because the
disposable Caddy could not bind loopback HTTPS port 443 under the current
account. No browser speedup is claimed. Its remaining gate is the supported
`anvil-serving connect qualify --lane revocation` command with a prepared
container qualification config; no host privileges or live services were changed.
