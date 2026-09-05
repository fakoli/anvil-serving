# Router and workload batch merge checkpoint

Status: final consolidated review and merge gates pending.

The operator narrowed the current stopping point to finishing the in-flight
workload source verification and bootstrap file-safety primitive, creating and
merging a PR, then stopping for a completed/remaining summary. Do not continue
with package publication, live deployment or further enrollment implementation
in this batch.

Current source scope is qualified replica sets, member capacity scheduling and
lifecycle controls, scoped node/fleet workload visibility, dashboard integration,
bounded controller diagnostics, and inert bootstrap contracts/primitives.
Receiver dispatch, trusted configuration composition, durable staging,
transactional activation/rollback, bootstrap transports/CLI and live enrollment
are unfinished. The trusted-context ticket is a design breadcrumb, not a built
feature. Managed controller recovery and live client acceptance remain separate.

Upstream PR #471 (b85b5d27) added portable host-supervised services during this
run. This merge retains its implementation and the workload navigation. The
generated manifest and CLI inventories are regenerated from the combined tree.
Removing only this batch's diagnostic tools and member-transition field from
the combined MCP catalog reproduces PR #471's exact catalog digest. Its literal
regression now pins that upstream baseline. The focused merge gate passed 126
tests across MCP catalog, command tree, serve management and native serve
binding. Full combined-tree and cross-platform CI gates remain pending.

No package, controller image, router image, host service, model, route, private
configuration, operator ACL or active client profile has been changed by this
source merge. Source merge will not be reported as deployment.
