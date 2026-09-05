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
