---
name: anvil-serving-release-readiness
description: Gate an Anvil Serving package release and coordinated live deployment across Windows, Fakoli Dark, Fakoli Mini, the controller, router, Hermes, Pi, and OpenClaw. Use when a change will be merged, tagged, published, or deployed and exact manifest dependencies, version parity, recovery policy, and outage-free client smokes must be proven.
---

# Anvil Serving Release Readiness

Use this staged evidence contract when the requested scope includes any
combination of PR merge, package release, controller/router image rebuild, or
live Mini-to-Dark deployment. It complements model qualification; it does not
grant model-promotion authority.

## 1. Freeze scope and starting state

1. State which actions are authorized: PR, merge, tag, GitHub release, PyPI
   publish, image build, live deploy, or rollback. Do not infer the later
   stages from an earlier one.
2. Fetch `origin/main`, record the exact source revision, use an isolated clean
   `codex/` worktree, and preserve unrelated operator files.
3. Snapshot installed CLI versions, controller and router build identity,
   active routes, serve owners, operating mode, GPU reservations, shared
   memory, and OpenClaw MCP availability. Record which host executes each
   command.
4. Name the active model/profile, alias behavior, and recovery policy. When
   rollback is allowed, name the rollback serve/profile and which public alias
   and backing identity it restores. When the operator explicitly requires
   fix-forward, record the same-selected-model restart/rebuild or known-good
   selected-profile recovery path; never silently substitute the prior model.
   A package release must not silently change a route; a route change remains
   human-gated.
5. Declare build CPU and memory ceilings before compiling on an interactive
   workstation. Do not consume all host threads or RAM by default.

## 2. Prove the candidate before publishing

1. Verify the source version is consistent across package metadata, runtime
   constants, Compose image defaults, changelog, and user-facing version text.
2. Run changed-path gates, the full test suite, Ruff, strict documentation and
   link checks, CLI/reference audit, wheel build/smoke, and package metadata
   validation. Test the exact merged tree when the target branch moved.
3. Derive deployment file closure from operational manifests. For every
   referenced recipe, topology, router target profile, rollback router profile,
   Compose file, or mounted config:
   - resolve placeholders in the command host's container namespace;
   - require the host source file to exist;
   - require the exact read-only mount in each consumer container;
   - require a regression test that derives the dependency from the manifest,
     not a duplicated filename list.
4. Render/parse every changed manifest and profile with the candidate package.
   If a schema became stricter, identify and migrate deployed operator config
   before upgrading the controller that will parse it.
5. Install the built wheel into a clean environment and verify the CLI version
   and representative commands from that artifact, not only the checkout.
6. Require current-head PR review and CI. Resolve every outage-capable finding
   before merge; do not treat a stale review as approval of a new head.

## 3. Publish and deploy in recoverable steps

1. Merge only the reviewed head. Verify the merge revision, then create the tag
   and release from that revision. Confirm the package index serves the exact
   version and a clean install resolves it; distinguish propagation delay from
   authentication or authorization failure.
2. Preserve the active serve while upgrading the control plane. Use managed
   preview/apply verbs and rebuild only the controller/router components that
   need the candidate. Raw Docker is limited to bounded read-only diagnosis
   when the product surface cannot explain a failure.
3. Apply required config/file migrations before starting a stricter consumer.
   Re-run manifest dependency closure against the files visible inside the
   deployed controller and router.
4. Establish exact version parity across the Windows CLI, Mini CLI/bridge,
   Dark CLI/controller, and router. A mixed-version deployment is incomplete
   even when one endpoint is healthy.
5. Stop normal progression when the controller cannot parse the manifest, a
   required mount is absent, the router loses its target, route identity changes
   unexpectedly, or an in-scope client path becomes unavailable. Execute the
   declared recovery policy: rollback when authorized, or restore the exact
   selected model/profile through the recorded fix-forward path. Fix-forward
   authority does not permit silent fallback or a prior-model substitution.

## 4. Prove the live contract before closure

Run the checks through typed controller/MCP surfaces when available and use the
installed CLI only as a verified fallback.

1. Controller: `operation_contracts`, serve status, operating-mode status,
   reservations, bounded logs, and zero unresolved ownership.
2. Router: build/config identity, readiness, advertised aliases, exact
   `llm.primary` identity, and context/concurrency metadata. When rollback is
   allowed, validate that its router profile preserves the intended stable
   public alias and maps it to the expected rollback model identity. Treat an
   auxiliary alias such as `llm.rollback` as profile-specific, not a universal
   rollback contract. Unknown or unavailable routes must remain fail-closed.
3. Model/client catalog: reconcile Hermes, Pi, and OpenClaw from the same
   authenticated router snapshot. Verify each selected alias advertises the
   exact router `context_limit_tokens` and `max_output_tokens`, preserves its
   provider/auth references and compaction policy, and has no silent fallback.
   Use `converge-mini-vision-clients` for this gate when installed; otherwise
   enforce the same closure directly. Enumerate every retained Anvil-backed
   Hermes profile rather than assuming the default profile; verify
   its main model, auxiliary compression, Anvil provider, provider-model
   catalog, and custom-provider context/output fields. Verify every current
   Anvil alias in the Pi and OpenClaw catalogs. Preserve all unrelated client
   settings.
   Use dry-run, confirmed apply, and an idempotent second dry-run where the
   managed surface supports them. The final dry-run must report no changes.
   Verify both the tracked and installed scheduled reconciliation job cover
   `openclaw,hermes,pi`, include `--hermes-profiles all` and the required
   change-triggered restarts, and have a successful latest run. Then run the
   real Hermes, Pi, and OpenClaw request shapes, including tools and tool-result
   continuation when affected. A long-context needle or protocol preflight
   does not replace these client-shaped smokes. Any catalog/profile drift or
   missing scheduled coverage blocks deployment closure.
4. Reasoning/output: use the deployed default reasoning effort and enough
   visible-answer headroom to observe a real answer. For an output cap, prove
   both the applied value and the explicit warning; an exhausted tiny probe is
   not a model failure.
5. Exclusive mode: prove both GPU roles have the intended owner, every competing
   managed GPU workload is blocked, and shared-memory ownership is clean.
6. OpenClaw: verify the Mini gateway path, selected model alias/context/reasoning
   settings, no silent fallback, and the expected MCP tool inventory.
7. Re-run the post-deploy smoke after any hotfix. Record exact versions,
   revisions, checks, failures, rollback state, and live result in the release
   evidence.

## Closure rules

Report the release as complete only when all authorized publish/deploy stages
pass and no in-scope outage remains. Report `published_not_deployed`,
`deployed_degraded`, or `rolled_back` precisely when that is the evidence.
Never conceal a superseded bad release; name the hotfix version and the defect
that required it.

Return a compact readiness matrix with these rows:

| Gate | Required evidence |
|---|---|
| Source | clean exact revision and merged-tree gates |
| Package | clean-install exact version and published artifact |
| Files | manifest-derived host files and read-only container mounts |
| Parity | Windows, Mini, Dark, controller, and router exact versions |
| Route | active/recovery profile alias parity, identities, readiness, context/concurrency |
| Client | Hermes/Pi/OpenClaw catalog parity and real-shape smokes with reasoning and tools |
| Safety | exclusive ownership, blocked competitors, clean shared memory |
| Closure | no outage, or explicit recovered/degraded state under the declared policy |
