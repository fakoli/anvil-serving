# ADR-0021 — CLI interaction contract

- **Status:** **Accepted** (2026-07-14)
- **Date:** 2026-07-14
- **Relates to:** [ADR-0012](0012-serve-and-router-management-verbs.md) (supported
  operational surface) · [ADR-0018](0018-router-transition-safety.md) (drained model swaps) ·
  `anvil_serving/cli.py`, `anvil_serving/command_tree.py`, `docs/CLI.md`

## Context

The operator CLI grew from independent tools into more than one hundred public command paths.
The v2 command tree made those paths discoverable, but the interaction details still vary by
handler: targets may be positional or named, preview and confirmation flags are inconsistent,
some parent help is richer than leaf help, and several mutations expose implementation concepts
instead of the operator's desired outcome. The Heavy-model workflow is the clearest example: a
recipe can be loaded and a hand-authored promotion plan can be applied, but there is no direct,
guarded way to say "switch the Heavy role to this recipe."

This project is pre-1.0, so improving the interface is worth deliberate breaking changes. The
implementation must remain stdlib-only unless a dependency creates substantial value that cannot
reasonably be achieved with `argparse` and the existing declarative command tree.

### Research basis

The contract follows established operator-CLI patterns reviewed on 2026-07-14:

- The [Command Line Interface Guidelines](https://clig.dev/) emphasize human-first discovery,
  contextual help, examples, actionable errors, stdout/stderr discipline, and stable machine
  output.
- [Docker CLI](https://docs.docker.com/reference/cli/docker/) groups verbs under resource nouns,
  exposes contextual `--help`, and defines explicit configuration precedence.
- [GitHub CLI](https://cli.github.com/manual/gh) provides a browsable hierarchy with a dedicated
  page and focused usage for each command.
- [`kubectl set image`](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_set/kubectl_set_image/)
  identifies the resource positionally and expresses the desired replacement explicitly;
  [`kubectl rollout status`](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_rollout/kubectl_rollout_status/)
  and [`kubectl rollout undo`](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_rollout/kubectl_rollout_undo/)
  make observation and recovery separate, discoverable operations.
- [`nomad job plan`](https://developer.hashicorp.com/nomad/docs/commands/job/plan) renders a
  structured diff and returns a modify index that prevents applying a stale plan.
- [`helm upgrade`](https://helm.sh/docs/helm/helm_upgrade/) exposes explicit rollback-on-failure and
  cleanup-on-failure controls for multi-step mutations.

## Considered options

1. **Keep each handler's existing interface and improve documentation only.** Rejected: clearer
   prose cannot make inconsistent safety, targeting, and output behavior predictable.
2. **Adopt Click, Typer, or another CLI framework and rewrite the dispatcher.** Rejected for now:
   the existing stdlib command tree already owns topology resolution, confirmation, remote
   dispatch, tombstones, and output policy. A framework migration would add a runtime dependency
   and large regression surface without solving the product-level grammar.
3. **Retain `argparse` and the declarative command tree, but make one interaction contract the
   acceptance gate for every public leaf.** Chosen.

## Decision

### 1. Resource-first grammar

Resource operations use `anvil-serving <resource> <verb> [target] [flags]`. Related verbs stay under
the same resource family. A positional argument identifies the primary target only when its meaning
is unambiguous; configuration, desired replacement, and secondary inputs use named flags. The
product-level workflows `init` and `doctor`, plus the CLI-self operation `upgrade`, are deliberate
singleton exceptions. `host doctor` remains a distinct topology-aware host diagnostic.

Examples:

```text
anvil-serving serves status heavy
anvil-serving serves switch heavy --recipe MODEL
anvil-serving models recipes show MODEL
```

Names describe operator intent (`switch`, `status`, `rollback`) rather than internal mechanisms
(`write-profile`, `docker-run`). Lifecycle verbs use the shared vocabulary `list`, `show`,
`status`, `logs`, `up`, `down`, `restart`, `switch`, and `rollback` where those meanings apply.

### 2. Predictable configuration precedence

The precedence is command-line flag, command-specific environment variable, documented project/CWD
file, operator config home, then packaged default. The project layer exists only for commands that
declare a project file such as `./serves.toml`; it is never a broad scan of the working tree.

`ANVIL_SERVING_HOME` selects the operator config home when it is non-empty. Otherwise every OS uses
`Path.home() / ".anvil-serving"`; anvil-serving does not silently switch to `%APPDATA%`, XDG, or
macOS Application Support. Empty environment values are treated as unset. Relative CLI paths are
resolved from the invocation CWD, paths declared inside a config resolve from that config's parent,
and packaged defaults resolve through `importlib.resources`. Help states every applicable layer and
the effective source. Human and JSON results identify the source for selected configuration,
topology, manifests, and registries. Secrets remain environment references and are never copied
into config or output. Cross-platform tests cover the precedence and relative-path rules.

### 3. Preview is a complete operation plan

Every mutation records whether preview is `required`, `not-applicable`, or `unsupported`;
`not-applicable` requires a reviewed reason, while `unsupported` maps to audit status `fail` until
preview is implemented. A required preview validates inputs and renders the same resolved targets,
preconditions, ordered actions, verification gates, and rollback action that apply would use.

Dry-run is offline by default. It may read local files and bounded local process state, but it never
starts/stops a process, writes config, pulls an image, invokes model inference, changes credentials,
or performs a paid request. A command may make an explicitly documented, bounded, read-only,
zero-cost health or identity probe. Any gate not executed during preview is listed as `deferred`,
with the exact point at which apply will run it.

Drift-sensitive previews support `--plan-out PATH`. The sanitized plan artifact contains a schema
version, command path, normalized inputs, CLI/handler version, source-file digests, topology and
overlay identity, resolved resource owner/endpoint, resolved transport identity/endpoint, remote
capability and schema versions, observed mutable-state fingerprints, creation time, and expiry. It
contains no credential value. Non-interactive apply uses
`--plan PATH --confirm`; immediately before the first mutation, the resource owner verifies the
artifact and every observed fingerprint. A changed input, file, owner, endpoint, handler version,
transport, or remote state refuses apply and requires a new preview. A controller failure never
replays a controller-authored plan over SSH.

Interactive commands may display and confirm a plan in one process without writing an artifact,
but must revalidate its fingerprints after the prompt. `--confirm` authorizes only that verified
plan. Non-interactive use without a required plan fails closed with an exact preview/apply sequence.
New interfaces do not introduce another consent synonym such as `--yes`; existing consent flags
receive a one-minor deprecation tombstone before removal.

### 4. Mutations are transactional where recovery is possible

Multi-step changes follow: resolve → validate → snapshot → quiesce/drain if needed → apply → wait
for bounded readiness → verify identity → readmit. Before the first mutation, the resource owner
creates an operation ID and an atomically replaced journal under
`<config-home>/operations/<operation-id>.json`. A same-resource lock prevents overlapping
transactions. The journal records the verified plan hash, phase, snapshot references, action
outcomes, and rollback state without secrets. Every phase and compensation is idempotent.

A failure automatically runs and verifies safe compensation. Status reads the journal; resume and
rollback require the operation ID and revalidate ownership. If compensation cannot restore the
snapshot or safe admission state, the command records `rollback-failed` and returns partial-result
exit `5` instead of claiming success. Process-local quiescence is not sufficient for this state:
before a restart-sensitive mutation, the transaction installs a persistent admission block in the
router-owned state. Router startup reads that block before accepting traffic. Only verified
readmission removes it; resume and recovery reassert it before touching containers or config.

When topology places participants on different hosts, the command host is a coordinator rather
than a fictional single resource owner. The parent plan contains one versioned participant plan per
owner (for example, Dark router/serve and Mini OpenClaw), plus dependency and reverse-compensation
order. Prepare resolves every participant, performs version/capability handshakes, acquires an
owner-local resource lock, and creates an owner-local journal before any commit. Commit uses stable
operation and participant idempotency keys. Each participant advances its journal atomically; the
coordinator advances the parent journal only after acknowledged participant phases. Failure runs
verified compensation in reverse dependency order. An unreachable or uncompensated participant is
reported as partial exit `5`, with the durable admission block retained where routing safety
depends on it. Apply rejects a participant whose plan, owner, transport, version, or lock changed
after prepare.

Quality promotion remains a separate human decision. Switching a deployed recipe may update
runtime wiring after preflight, but it does not manufacture or auto-promote quality evidence. If a
switch changes a router preset, reasoning/thinking setting, context limit, or other harness-visible
value, the same transaction renders, applies, restarts, and verifies harness configuration; rollback
restores both router and harness state. A runtime-only switch must prove those values are unchanged.

### 5. Help and errors teach the next action

Every public path supports contextual `-h` and `--help`. A leaf's help contains:

1. one-sentence outcome;
2. exact usage with required operands;
3. common examples before exhaustive options;
4. applicable defaults, environment sources, choices, and repeatability;
5. mutation, timeout, and rollback behavior when the leaf supports them;
6. a direct documentation page or anchor.

Missing actions print concise family help. Invalid input names the problem and one valid next
action without silently correcting a state-changing command. No recommended command may omit the
arguments needed to run.

### 6. Human and machine output are separate contracts

Primary results go to stdout; diagnostics and errors go to stderr. Human output is concise and
scan-friendly. Bounded commands support `--json` and emit one redacted envelope. The current D013
shape is version 1: `ok`, `command`, `context`, `data`, `warnings`, and `error`. Adding fields is
compatible; removing/changing fields requires an incremented top-level `schema_version` and a
migration note. The migration adds explicit `schema_version: 1` before any breaking JSON change.

`--json` is rejected for `follow`, `foreground`, and `protocol` output policies; this ADR does not
implicitly introduce JSONL. Read commands are bounded unless the operator explicitly selects one
of those policies. Exit codes are:

| Code | Class | Meaning |
| --- | --- | --- |
| `0` | success | Requested operation completed. |
| `1` | execution | Operation failed before partial completion. |
| `2` | usage | Arguments, configuration, or input are invalid. |
| `3` | safety | A confirmation, precondition, or safety gate refused the operation. |
| `4` | transport | The selected execution transport failed before a known mutation. |
| `5` | partial | State may have changed, or verified rollback/readmission did not complete. |

CLI, MCP, and controller schemas carry independent versions. A mutating remote request performs a
version/capability handshake before dispatch and fails closed on incompatible skew; it is never
translated optimistically after mutation begins.

### 7. Cross-platform behavior is part of the interface

The same canonical command works on Linux, macOS, and Windows unless help explicitly marks an
OS-specific capability. Paths use `pathlib`; subprocesses use argument arrays; examples do not
depend on shell-only syntax. Local URLs use `127.0.0.1`. Human output must survive standard Windows
console encodings, while JSON remains UTF-8-safe and ASCII-compatible where practical.

### 8. Every verb must prove the contract

The command manifest is the inventory. The versioned `docs/CLI-UX-AUDIT.json` contains exactly one
record per visible leaf. Grammar, help, configuration, output, errors, boundedness, docs, and parser
tests are always `pass` or `fail`. Preview, confirmation, drift protection, timeout, rollback,
journaling, remote parity, and each OS are `pass`, `fail`, or `not-applicable` with a non-empty
reason. Evidence fields name the tests and documentation anchor.

The audit lands as an explicit migration ratchet: CI rejects missing, duplicate, or stale records,
invalid evidence, command-manifest fingerprint drift, and any per-dimension failure count above its
checked-in maximum. Bootstrap conservatively marks every contract dimension as failing; only a
focused review may record a pass or a reviewed `not-applicable`. Each reviewed command-family change
binds the dimension, decision or `not-applicable` rationale, and dimension-specific test evidence to
the current command-manifest fingerprint, then lowers the corresponding failure maxima. Refreshing
derived metadata cannot renew that attestation. New leaves must either pass immediately or pay for every new failure by removing an
existing failure in the same dimension. When all maxima reach zero, the audit switches to `strict`;
strict mode rejects every failing record and any nonzero maximum. The ratchet is a migration record,
not a claim that every existing verb already complies.

A leaf is complete only when focused help reaches the real parser, examples parse, the docs anchor
exists, all applicable dimensions pass, every `not-applicable` has been reviewed, and behavior tests
cover declared operating systems. The audit must catch incorrect metadata such as a file-writing
command classified as `read`.

Breaking command paths and flags keep a tombstone with the exact replacement for at least one minor
release. JSON and remote schema changes follow their version rules. Because the product is pre-1.0,
consistency takes precedence over preserving accidental spellings after the compatibility window.

## Consequences

- The stdlib-only runtime rule remains intact; `argparse` and the command tree stay the core.
- Interface work is larger than renaming verbs: handlers, controller/MCP schemas, docs, examples,
  tests, and tombstones must change together.
- Some current commands will be consolidated or renamed, and duplicate confirmation mechanisms
  will be removed.
- Heavy recipe switching will become a first-class guarded workflow with preview, bounded rollout,
  identity verification, and rollback, while quality promotion stays independently gated.
- The versioned exhaustive audit becomes the merge gate when its schema and CI completeness check
  land; new commands then inherit the same requirements.
