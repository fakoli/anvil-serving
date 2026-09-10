# Workbench implementation checkpoint

The approved scope is the complete integrated Workbench, purposeful actions mapped
to twelve stories in `IMPLEMENTATION.md`, independent review, live acceptance,
push, CI and merge. The goal remains active. Production implementation is pushed in Serving PR 482.

## Architecture and completed gates

- `workbench_app` extends the existing authenticated Observatory facade. Connect
  signed raw targets, scopes, CSRF and Origin checks remain the identity boundary.
- Official Pi RPC owns native conversations. The portal supplies folders, threads,
  model/thinking selection, streaming tool events, steer/stop, resume and fork.
  No separate Pi Web identity/session server is introduced.
- Pinned unprivileged Pi runners use a per-session reverse provider gateway,
  exact model/path/body bounds, pinned DNS/TLS, no agent-visible credential and
  exact inspected Docker identity. Independent security review approved the final
  runtime; real model, native history, recovery and cross-session denial smokes passed.
- A bounded ext4 loop pool separates runner writes from server journals and
  canonical claims. Managed repeat setup passed. Independent storage review passed.
- Task work uses separate agent and verifier clones, a frozen canonical packet,
  explicit scope/mode/digest checks and transfer only after passing verification.
  Failed-check and passing-transfer real Docker smokes passed. Durable interrupted
  evidence jobs do not replay. A terminal-store failure capacity leak was fixed.
- Playground uses exact declared models, private conversations, real streaming,
  explicit cancellation and retained request tombstones. Independent review passed.
- Compute uses owner logs and reviewed typed diagnostics. Registered MCP wire tests
  now cover initial digest-free dry-run, exact confirmed digests and native results.
  Managed recipe load/unload passed independent admission and recovery review.
  Serve diagnostics now reuse the existing model owner and reject mismatched
  immutable container previews; focused controller/MCP wire gates pass.
- Browser checks exercised authentication, Workbench, settings, model editing,
  compute diagnostics/evidence, telemetry charts and persisted PRD reading. A real
  encoded task-ID navigation defect was fixed with raw-target auth preserved and
  HTTP regression coverage. Continue browser checks on the current root-owned fixture.

## Dependencies and coordination

Anvil PRs 223, 226 and 227 merged. Supported projection repair retained a backup
and restored replay integrity. Deterministic planning exposed a separate historical
identity gap: the canonical log contains PRD lifecycle records but no project
registration event, so replay cannot supply the required project row. Investigation
must use the event-proven identity and preserve concurrent claims; neither a fresh
project initialization nor a direct database edit is an acceptable repair.
The dedicated installed Anvil CLI resolves the canonical State workspace.

The concurrent Connect checkout and dirty private operator repository must remain
intact. No inference, router or Connect restart has occurred. A native Workbench canary now runs separately with explicit resource limits.
Its managed installation passed twice, with zero changes on repeat. Existing
Observatory still serves live traffic.

## Current work and remaining gates

- Root: finish real browser journeys, exact-source gates, native cutover,
  controller-only deployment, product PR/CI/merge and reflection.
- Compute worker: scoped private policy/owner wiring and deployment evidence.
- Sol reviewer: Pi focus preservation and native event rendering, then final UI review.
- Anvil repair worker: fresh repair PR CI, merge and supported project recovery.

Installed canary acceptance: valid Connect assertions pass; unsigned reads fail;
fleet, controls, docs and canonical Anvil project reads return authenticated data.
A real Playground request completed through the selected primary alias, returned
its exact marker and retained usage. Installed wheel smoke and strict docs pass.
The clean committed source suite passed 7,857 tests with 34 skips. Linux 3.11 and
3.13 CI and both installed-wheel build jobs passed. The first CI pass found an
import-time POSIX-only default, stale CLI inventory and packaged relative document
links. Those are corrected in the follow-up, with platform-specific tests limited
to the actual Linux storage boundary. The public scanner now recognizes only exact
public metadata/container constants in the files that own them; independent review
accepted its negative boundary tests.

Pi's two UI reviews fixed separate drafts, explicit new-thread targets, ordered
native ID-less messages, durable extension resolution and polling focus retention.
The isolated browser fixture now supplies a retained running thread and native
events for actual browser verification. Shell filters use the HUD once, and hiding
an interactive page no longer aborts its in-flight conversation request.

The repeatable private infrastructure role is merged. Private owner controls are
in a separate scoped PR. Native primary cutover, the final controller artifact,
Pi browser acceptance and the production merge remain delivery gates.

Keep private runtime paths, current digests, host identities, credentials and raw
receipts out of this tracked checkpoint. Private deployment evidence stays in the
operator workspace. Preserve unrelated untracked backups and other worktrees.
