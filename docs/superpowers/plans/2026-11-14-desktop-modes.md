# Desktop Modes Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Install three explicit Stream Deck mode buttons and finish in media mode.

**Architecture:** Extend the existing local desktop CLI with a mode command. Reuse its Docker pin, operation lock and cooldown, and the existing managed serve start/stop functions. Keep assignments and media worker names in private operator configuration.

**Tech Stack:** Python standard library, existing pytest checks, native Stream Deck Open Application actions.

**Spec:** Private operator evidence/desktop-three-modes-20261114/design.md, approved by the user.

## Global Constraints

- Preserve on-demand job-owned workers, lighting and dials, and the original toggle for rollback.
- Refuse busy or unverifiable workers. The approved idle-release follow-up uses existing job ownership and checks durable jobs plus native running/pending queues.
- No model promotion, new dependencies, weights or images. Keep deployed identities private.
- Select explicit modes after gaming without restoring the competing mode first.

## Review Focus

- Busy worker before selection must leave all containers alone.
- Worker appearing during control-plane stop must restore controls and refuse without stopping the worker.
- Invalid/remote configuration and missing target containers must fail before stops.
- Repeated/overlapping presses must not alternate modes or queue work.
- Failed activation must remain retryable; unrelated containers and legacy restore evidence must be preserved.

## Task 1: Implement mode selection

Files: anvil_serving/desktop.py, anvil_serving/desktop_modes.py, anvil_serving/commands/host.py, tests/test_desktop_modes.py, docs/cli/host.md.

- [x] Write failing checks for the three modes, previews, shared lock/cooldown, busy worker and control-plane race refusal, missing target, and explicit selection after gaming.
- [x] Run the new checks and observe failure.
- [x] Extract the existing operation lock/cooldown without changing its behavior. Add select_mode(mode, confirm=False, dry_run=False, timeout=600, run=desktop._run), using a validated private desktop-modes.toml.
- [x] Freeze media gateway/lifecycle through managed keep-container stops before crossing modes. Recheck workers and restore controls on refusal. Running/starting workers are never stopped by this command; job-owned teardown remains authoritative.
- [x] Add the host desktop mode CLI registration and generic docs. Run new and existing desktop tests and CLI contract checks.

## Task 2: Install assignments and buttons

Files: private desktop-modes.toml, isolated desktop switch installation, three desktop shortcuts, existing Stream Deck profile.

- [x] Back up installed module bytes, shortcut and profile; preserve unrelated dirty source work.
- [x] Install tested modules only into the isolated desktop switch environment. Write private assignment references to existing secondary/media manifests.
- [x] Create Gaming, LLM.secondary and Media shortcuts; use the existing upper-left key and two spare right-column keys. Keep lighting/dials unchanged.
- [x] Check actual Stream Deck UI labels/actions and compare all untouched profile actions.

## Task 3: Review and live acceptance

- [x] Obtain one fresh independent code review; resolve actionable findings and rerun affected checks.
- [x] Exercise installed Gaming -> LLM.secondary -> Media commands, repeated selections and rapid-press protection.
- [x] Verify the promoted local endpoint identity and one arithmetic request; finish with the model stopped, media gateway healthy and safe host-memory signal.
- [x] Save private receipts and retain only model-neutral durable facts in shared memory.

## Execution ledger

Approved implementation proceeds inline; no second design approval is needed. Existing desktop work is already isolated on codex/desktop-gaming-switch and has pre-existing dirty files; do not commit or overwrite unrelated changes.

Ruling: The installed media gateway does not configure durable global admission. Use its existing managed gateway/lifecycle stop operations and refuse every present active/transitional worker before and after that freeze; never weaken its admission checks or terminate job-owned workers. An idle retained worker still requires its normal job-owned teardown before a different mode can be selected.

Task 1: complete. Red-green checks cover mode selection, shared button guards, worker races, ownership overlap, missing controls, exact secondary identity, and read-only previews. Independent review findings were fixed and re-reviewed.

Task 2: complete. Installed into the isolated desktop-switch environment after backing up original bytes. Added three shortcuts and the two spare Stream Deck keys through the native editor. Lighting and dial actions compare identical to the original backup. Verified each saved command, disabled bring-to-front and long-press termination.

Task 3: complete. 206 focused tests passed, 9 skipped; CLI audit and scoped ruff passed. Live exact-shortcut Gaming -> Secondary -> Media retry passed; all three repeated selections were idempotent and rapid extra presses were ignored. Verified declared secondary identity and a fresh independently checked arithmetic response. Container IDs and initial states were preserved; ended with media controls healthy, local models stopped, on-demand worker absent, and fresh safe host-memory signal. No physical key press or new render was performed.

Live finding and correction: media readiness must release the LLM before waiting for host memory to become safe. The guard now polls its existing strict signal for ten seconds and preserves persistent refusal reasons. Media startup also waits for normal Docker health-probe lag. Neither change lowers safety thresholds. The initial failed receipt is preserved privately alongside the passing retry.

## Approved idle-release follow-up

The user authorized automatic teardown of idle media workers while preserving
running renders. This supersedes the original manual-teardown ruling above.

- [x] Use the existing controller's durable job-owned lifecycle; never directly stop a worker.
- [x] Refuse unfinished jobs (including warm requests), nonempty native queues, unowned workers and unknown status.
- [x] Freeze gateway submissions, recheck idle state, release through managed teardown, then finish the selected mode. Restore controls on refusal.
- [x] Keep preview read-only, including lifecycle schema initialization; require the same running predicate as managed teardown.
- [x] Cover idle release, busy and queued work, preview/apply races, ownership mismatch and unavailable status. Resolve independent review findings.
- [x] Verify installed idle-worker cleanup with actual shortcut commands and finish in media mode.

Live validation identified a stale deployed private worker profile incompatible
with its newer guard policy. Restore canonical private control/worker references
and their matching original publisher registration together; retain protected
volumes and existing job ownership. No image build or model promotion is needed.

Follow-up acceptance: 195 focused tests passed, 9 skipped; full CLI reference
audit and scoped lint passed. Both saved Gaming and Secondary shortcut commands
refused an unfinished test job without any container changes. After canceling
only the test jobs, both commands automatically released their idle owned worker.
Gaming stopped Docker; Secondary served a fresh arithmetic request. Returned to
Media with healthy controls, safe memory signal, no active worker or local model,
zero unfinished jobs or open ownership phases, and unrelated containers preserved.
Native running/pending render refusal was checked in automated tests; no render
was submitted and no physical key press was performed. Private receipts preserve
the initial stale-profile failure and the final passing cycles.
