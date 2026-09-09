# Campaign friction log

| Stage | Evidence | Disposition | State |
|---|---|---|---|
| Identity | independent-review-ledger.json | Added managed immutable-image identity CLI; NUL validation, selected labels and documentation independently reviewed. Subprocess capture memory bound remains explicit follow-up. | Implemented |
| Baseline | baseline/preflight.json, baseline/needle-repeat.json | Preserved two benign needle refusals; continued comparative characterization before candidate. | Retained failure |
| Baseline turnover | baseline/turnover.json | Marker-at-start failed; before candidate, froze same strict32-word prompt in both arms. Raw run excluded. | Retained excluded attempt |
| Startup | candidate/identity-pre.json | Exact v81 build and both-rank IPC/P2P proved. TileLang warnings independently reviewed; untuned IPC seed warning retained. | Bounded acceptance with caveats |
| Quality | candidate/quality.json, independent-review-ledger.json | Literal marker omission is not proof of overall semantic failure; semantic caveats retained. No executed coding claim. | Adjudicated diagnostic |
| Turnover | candidate/turnover-controlled.json, candidate/turnover-controlled-repeat.json | 58/60 then57/60; no further reruns; qualification failed and exact baseline restored through managed operations. | Baseline retained; no promotion |
| Repository tests | private-only:full-tests.txt, private-only:router-cache-failure-recheck.txt | Unchanged router-pressure timing test failed once and passed isolated rerun; retain both outcomes. | Timing-sensitive test |
| Repository tests | private-only:full-tests-recheck.txt, private-only:bootstrap-secure-worktree-recheck.txt | Owned isolated checkout was group-writable775; narrowed only this checkout to755. Bootstrap tests then16pass5skip; no global permission change. | Environment fixed |
| Repository tests | private-only:full-tests-secure-worktree.txt | 5323pass29skip then missing python executable in subprocess PATH; rerun with existing venv bin prepended, no package changes. | Environment corrected |
| Restoration | private-only:restoration/router-status.txt, restoration/router-transition-status.txt | Generic status rejects router-url before any request; corrected to typed transition-status and verified admitting/ready. | CLI invocation corrected |

The complete source suite subsequently passed 7,437 tests with 30 skipped. Focused publication tests passed 146 with 8 skipped. Initial environment failures remain retained privately; no failed run was rewritten.
