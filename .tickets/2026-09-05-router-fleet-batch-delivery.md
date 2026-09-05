# Batch implementation with consolidated final acceptance

Status: active delivery workflow; operator-directed on 2026-09-05.

The operator explicitly replaced per-task adversarial review and acceptance
with implementation batches followed by one consolidated review and acceptance
pass after all tasks are implemented. Keep isolated worktrees, scoped tickets,
focused regression tests and claim-bound evidence. Locally integrated candidates
remain pending review; dependency source may be used before formal acceptance
when its implementation is present and its focused gates pass.

Do not mislabel a local candidate merge, evidence submission or task graph as
accepted, publicly merged or deployed. Previously accepted tasks retain their
history. Newly reproduced defects are fixed forward with tickets during the
build; deliberate per-task adversarial passes are deferred to the final review.
The final pass includes complete source/docs/package gates, PR review/merge,
managed deployment, rollback readiness and real endpoint/client acceptance.

Documentation correction: the PRD index's relative link into .tickets was a
strict MkDocs warning because tickets are outside the published docs tree.
Keep the exact repository path as a code reference and synchronize the index's
older one-task wording with isolated executors and batch integration. This
does not relax focused testing, evidence capture or final acceptance.

PRD attribution correction: one workload-visibility structural review invocation
used the CLI's default reviewer label, human. That was an agent operation, not
a human decision. Revision21 explicitly records the actual agent reviewer and
notes the correction; implementation approval is separate from the deferred
batch acceptance. Future review commands must pass --reviewer explicitly;
ANVIL_ACTOR does not override that command's reviewer default.

The workload batch now includes explicit node source bindings (a5277f1e,
EV19A7DA9C, 45 tests), pure fleet composition (e66bfcc6, EV5E42BEBA,
89 tests), sealed controller node-workload operation (086d85b0,
EV3BAA58C6, 116 tests), controller startup options (fddbdd62,
EVA39F8E67, 101 tests), receipt-clock correction (a54c25c3,
EVCF77296C, 99 tests), linear composition (7986e1c1, EVCE315F7D,
102 tests), expected-node reader (5e98b040, EV9ED37714, 45 tests), and
bounded persistent fleet collector (b91618dc, EVAFF809D6, 34 tests).
Each count is its focused postcommit gate, not an additive test total;
each candidate also passed its scoped Ruff gate. All remain needs_review.

The integration baseline 34ad3208 passed 6,578 tests with 10 skips before
the later controller, CLI and receipt-clock/composition corrections landed.
The combined controller/startup/source gate at 72d0cd8f passed 140 tests.
These exact baselines are recorded so the older full-suite result is not
misrepresented as proof for later source. Generated CLI-reference inventories
remain stale and are owned by the final workload surface/docs tasks.

Further integrated candidates: explicit fleet bindings (`9f0a82ed`,
`EV50E9FE25`, 37 tests), canonical tool declarations (`6ccf10f2`,
`EV217C4A50`, 79 tests), canonical router client (`ffb42d90`,
`EV575BBDF1`, 62 tests), sealed fleet HTTP (`3a0840c0`, `EV40834EDE`,
130 tests), explicit fleet startup (`5002dce6`, `EV98A232A8`, 452 tests),
and explicit controller response cap (`759659fa`, `EVE1309262`, 123 tests).
All passed scoped Ruff and remain pending consolidated acceptance.

Full-suite baseline `b3cb658c` passed 6,640 tests with 10 skips in 269.90s.
It predates those further candidates; it is not proof for their integrated
behavior. The fleet client and MCP regressions continue in isolated worktrees.

Full-suite baseline `81b43ff6` passed 6,709 tests with 10 skips in 273.73s.
This includes the explicit fleet bindings, schema, router client, fleet HTTP,
startup and controller response-cap candidates. It predates the canonical fleet
client `30d054c1` (`EVE1C7C31B`, 95 focused tests) and dedicated MCP/chaining
candidate `0398c5a9` (105 focused tests). Those two source branches were integrated
only after the full baseline completed. CLI implementation continues separately;
dashboard slice details are published but not yet re-parsed while its claim is active.
