# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.13.2] - 2026-07-14

### Changed

- **`anvil-serving init` now creates the complete canonical config family.** The full home
  scaffold includes a default `router.toml`, all local/container/flexibility/cloud router examples,
  the mode manifest, and the serve-recipe registry alongside the existing serve, Compose, voice,
  topology, environment, and edge files. The cloud config remains inert unless explicitly selected.
- **Local router image tag advanced to `anvil-serving:0.13.2`.** The Dockerfile, reference compose
  file, packaged scaffold, and guarded router-management default stay in lockstep with this patch
  release. Build the image locally from this checkout; no container registry artifact is published.

## [0.13.1] - 2026-07-14

### Added

- **Full model recipe CRUD and guarded loading.** `models recipes` can list, show, create, update,
  delete, and load recorded serve recipes. Mutations use atomic writes, backups, cross-platform
  registry locking, and drift detection; loads validate Docker arguments and keep published ports
  on loopback.
- **Guarded `anvil-serving upgrade` self-update command.** Resolves the newest stable PyPI
  release, preserves `uv tool` / `pipx` / `pip` package-manager ownership, supports a no-write
  dry run, requires confirmation before mutation, rejects downgrades, verifies the resulting CLI
  version, and refuses to detach editable source installs unless explicitly allowed.
- **Cross-platform `anvil-serving router endpoint` discovery.** Reports the deployed router's
  actual Docker listen address/port, a connectable local URL, running state, and the node's
  Tailscale MagicDNS name on Linux, macOS, and Windows, with explicit overrides and honest
  fallbacks when Docker or Tailscale is unavailable.
- **Dedicated OCR lifecycle group.** The shipped and packaged Fakoli Dark manifests now expose
  `--group ocr`, so operators can stop or start PaddleOCR independently through `serves down/up`
  without targeting the full `llm-stack` group.

### Changed

- **Local router image tag advanced to `anvil-serving:0.13.1`.** The Dockerfile, reference compose
  file, packaged scaffold, and guarded router-management default stay in lockstep with this patch
  release. Build the image locally from this checkout; no container registry artifact is published.

## [0.13.0] - 2026-07-13

### Changed

- **Local router image tag advanced to `anvil-serving:0.13.0`.** The reference compose file,
  shipped scaffold, and guarded router-management default stay in lockstep with this source
  release. Build the image locally from this checkout; no container registry artifact is published.
- **BREAKING (pre-1.0, operator-requested): `anvil-serving init` now defaults to the full
  operational home scaffold.** Bare `init` scaffolds the whole config set (all `serves*.toml`,
  compose files, `operator-topology.toml`, voice manifest, `.env.example`, and the ADR-0019
  `edge.toml`) into the config home (`~/.anvil-serving`, honoring `ANVIL_SERVING_HOME`; override
  with `--out-dir`) so a fresh machine runs `serves up --group NAME` with zero hand-assembly. The
  single-model quick bring-up into the CWD moved behind **`--single-model`**. The old `--home`
  flag is a hidden, deprecated alias for the new default for one release (prints a deprecation
  note), then is removed. No-overwrite-without-backup and placeholder-only (no secrets/real UUIDs)
  behavior are unchanged. See [ADR-0020](https://github.com/fakoli/anvil-serving/blob/main/docs/adr/0020-init-defaults-to-home-scaffold-shipped-as-package-data.md).

### Fixed

- **`init` works as an installed tool, not just from a source checkout (fixes #252).** The home
  scaffold resolved its reference files via `__file__/../examples`, a path that only exists in a
  git checkout — the `examples/` tree is not shipped in the wheel — so `uv tool install` /
  `pip install`ed `anvil-serving init` failed with *"the shipped reference examples are not
  available next to this install."* The reference set now ships as **package data** under
  `anvil_serving/_scaffold_templates/` and resolves via `importlib.resources`, working identically
  from a wheel install and a source checkout. The mirror is kept byte-identical to the canonical
  `examples/` copies by `scripts/sync_scaffold_templates.py` and a drift-guard test, and a
  packaged-path test resolves the set the way an installed tool does so the regression cannot return.

### Added

- **Router transition safety for slow single-workstation model swaps.** Promotion,
  rollback, and resume now quiesce affected tiers, drain counted generations before
  container mutation, require exact `/v1/models` identity readiness, and leave
  unrelated Fast serves resident. Authenticated CLI/MCP/controller operations expose
  transition status, quiesce, drain, and guarded readmission; `serves promote` is now
  remotely dispatchable as one human-gated transaction.
- **Health-aware runtime tier eligibility.** Local tiers can declare a
  `health_path`; bounded cached probes exclude stopped or starting model serves
  before inference, record `skipped-unavailable` without tripping the circuit
  breaker, and automatically readmit recovered upstreams without rewriting
  router config or restarting the front door.
- **Lower-noise observability dashboard for desktop and tailnet phones.** The
  read-only dashboard now separates grouped Windows, Fast/Heavy GPU, shared
  graphics memory, WSL, and Docker graphs from a bounded searchable probe
  explorer; pairs current readings with known capacities; exposes observed
  minima/maxima; and serves an unauthenticated shell that can accept a bearer
  token while keeping telemetry APIs authenticated when configured.
- **Qwen3.5-122B-A10B-MXFP4 RTX PRO 6000 recipe and evidence.** Adds a pinned
  131K-context vLLM/Marlin candidate serve plus dated standard-throughput and
  deterministic planning-eval artifacts. The measured candidate remains
  experimental and explicitly unpromoted because it was slower than the prior
  NVFP4 result and passed only one of five planning evals.
- **GPU-residency-aware model lifecycle.** Serve manifests can now declare named GPU roles,
  reservations, and resident or evictable workloads. `serves up` validates admission before
  starting a container, reports the derived reservation ledger, supports grouped lifecycle
  operations, and drains an evictable router tier before admitting an on-demand replacement.
  The reference topology includes the promoted Gemma 4 E4B Fast tier plus dedicated embedding,
  reranker, OCR, vision, and ComfyUI service definitions.
- **Tailnet edge and purpose-model routing.** The authenticated router can front bounded
  `/v1/embeddings` and `/v1/rerank` purpose models, while the managed tailnet edge owns the
  private `/v1` and optional ComfyUI entrypoints without exposing raw model serves.
- **Q36 RTX PRO 6000 experiment recipe and evidence.** Adds an opt-in, separately managed Q36
  experiment for the PRO 6000; it remains mutually exclusive with the selected ThinkingCap Heavy
  serve and is not a production routing tier.

## [0.12.0] - 2026-07-11

The first packaged release since v0.7.3 completes the operator CLI v2 transition,
adds the full read-only system observability dashboard and benchmark telemetry
pipeline, and makes externally authored deterministic eval suites a supported
benchmark input. It also includes the v0.10.0 tagged source checkpoint and the
v0.11.0 untagged source checkpoint.

### Added

- **Read-only system observability dashboard and benchmark context.** Adds
  capability-aware Windows, WSL/Docker, NVIDIA, container, service-health,
  remote-controller, and macOS collectors; a low-overhead local web dashboard
  with bounded tiered retention, pressure/loading/freshness indicators, and
  explicit sampling gaps; and programmatic benchmark capture sessions with
  compressed raw evidence outside Git, sanitized findings, retained-session
  comparison, and strict CPU/RSS/disk/GPU overhead gates. Target validation
  passed at 0.3051% average host CPU / 38,699,008-byte peak RSS in normal mode
  and 0.1513% / 39,501,824 bytes in benchmark mode, with zero dashboard-process
  capture writes, zero GPU allocation, and a 0.35% controlled benchmark effect.
- **Qwen3.5-122B-A10B-NVFP4 heavy-tier candidate evidence.** Publishes the
  131K-context RTX PRO 6000 evaluation and keeps the candidate experimental
  pending the documented tool-calling and quality gates; it is not
  auto-promoted by this release.
- **`eval benchmark run --suite-file`** — runs externally-authored eval specs (e.g. the
  fakoli-plugins session-evals `suite.json`) through the existing deterministic bakeoff
  check engine (text checks + tool-call validation) against the target endpoint. Per-eval
  checks and failures land in the standard evidence JSON under `suites.<suite name>`;
  `--suite-file` alone runs only the external suite (built-in suites opt in via `--suite`).
  Malformed specs are rejected before any request is sent — including vacuous checks
  (typo'd assertion keys, empty needles) that would otherwise pass on any output, per
  the no-self-verification rule. Requires `--bakeoff`.
- **Operator CLI v2 production closure (M4)** - adds a manifest-generated
  complete command index and tombstone table, a deterministic active-reference
  audit with checked-in numeric inventories, and aligned operator skills across
  Codex, Claude Code, OpenClaw, and voice operations. Active docs, examples,
  configs, parser program names, and agent guidance now use canonical nested
  commands; compatibility forms remain only in explicit migration/tombstone
  evidence. Parent command groups now reject action-specific flags when the
  required child action is missing instead of printing help and returning
  success.
- **Hermetic Markdown link guard** - checks relative targets in every
  Git-tracked Markdown file using the same Python-Markdown/Pymdown parser
  family as MkDocs, ignores external URLs and rendered code examples, and now
  runs beside strict MkDocs in documentation CI. Parser packages stay confined
  to docs/test extras; untracked worktrees cannot change the scan scope.
- **Operator CLI v2 voice lifecycle (M3)** — adds canonical
  `voice audio up|down|status|logs` and
  `voice proxy run|up|down|restart|status|logs|bridge` surfaces. Audio remains
  Dark-owned, the persistent Realtime proxy and loopback forwarding bridge
  remain Mini-owned, and all operational paths resolve topology before local
  work. MCP/controller parity includes bounded reads, preview/confirm mutation
  gates, persistent PID/log ownership, per-host command identity checks, and
  bounded subprocess/process/connection behavior. Legacy module-level voice
  paths remain removed tombstones.
- **`host memory` + `host reclaim` — the WSL page-cache watchdog** — promotes the ad-hoc
  remediation from the 2026-07-10/11 Blackwell bakeoff (repeated 60–90 GB weight streams
  ballooned the WSL2 VM's page cache to 50–54 GB of 64 GB, starving Windows;
  `autoMemoryReclaim=gradual` lags load bursts). `host memory` shows host RAM, the WSL VM's
  used/page-cache/available (`/proc/meminfo` via `wsl`), and GPU VRAM. `host reclaim` runs
  `sync && echo 3 > /proc/sys/vm/drop_caches` as root inside the distro — confirm-gated
  per the CLI safety policy (`--confirm`), refusing while a checkpoint is actively streaming
  (page cache growing > 0.25 GB/s) unless `--force`; `--watch --threshold-gb N [--interval S]` is the
  foreground watchdog form. Windows/WSL2-guarded with a clear message elsewhere.
- **Production-polish reconciliation inventory** — records the 49 pre-existing CLI
  polish hunks, their retain/adapt disposition, their v2 task ownership, and the
  planned callable-alias-to-tombstone conversion. This preserves the working
  implementation while keeping its deferred removal work auditable.
- **Production CLI discovery contract** — root help now documents global `--help`/`--version`
  flags and the canonical nested workflows, `serves --help` explains every action, and tests lock
  the help/version surface. Removed module-level voice lifecycle forms fail with a canonical
  replacement instead of silently dispatching. The CLI and voice references document
  exit behavior, stdout/stderr conventions, safety gates, and the complete canonical taxonomy.
- **Bakeoff notebook** — the persistent, comparable record the fast-tier
  bakeoff report was assembled by hand from. `anvil-serving eval benchmark run
  --bakeoff … --notebook DB --notebook-task T --notebook-hardware H` appends
  each run into `bakeoff_runs` (schema: two additive tables `bakeoff_runs`
  + `bakeoff_verdicts`); `anvil-serving eval benchmark external notebook
  add|list|render` records/lists/renders. `render` emits the repeatable form
  of the #181 report — the candidate matrix, a 100-point rubric (encoded as
  data in `external_benchmarks/notebook.py`), and a per-candidate
  win/lose/hold **verdict with a recorded reason** (hard gates: tool/session
  pass + no failures). Append-only history; the notebook view is
  latest-per-(candidate, config, task, hardware).
- **Shared host-mutation guard (`anvil_serving/guard.py`)** — the
  compute → gate → apply → verify → rollback pattern proven separately in
  `host` (confirm + numbered backups + refusal floors), `cache-prune`
  (plan/gate/apply), `router promote` (crash-loop verify + rollback), and the
  MCP triple gate, consolidated into one importable module: `confirm` ([y/N],
  EOF→No, `--yes`/`--force` short-circuit), numbered `.anvil.bak.N`
  backup/restore, `await_stable` (settle + N consecutive good samples), and
  `terminate_then_kill` (the canonical one-attempt destructive escalation —
  never a retry loop; a `wsl --shutdown` retry loop is what wedged the host).
  `host.py` and `multiplexer.py` now delegate to it.
- **`serves rm` / `serves adopt` confirmation gate** — both are irreversible
  (`docker rm -f`); they now prompt `[y/N]` with the full container list, and
  `--yes` skips the prompt for automation. `--dry-run` previews without
  prompting; a declined or EOF (no-TTY) answer removes nothing and exits 1.
- **`serves down` stop verification** — after a successful `docker stop`, the
  container state is re-checked; a container revived by a `restart: always`
  policy (GPU silently NOT freed) is now a loud warning and rc 1 instead of a
  false "stopped".
- **`router restart`/`reload` stay-up verification** — the same crash-loop
  check `router promote` uses (settle + consecutive running samples +
  RestartCount delta): a router that fail-fasts on a bad config no longer
  reports a successful restart while it crash-loops.
- **`init` config backups** — regenerating `docker-compose.yml`/`router.toml`
  over operator-edited files now writes numbered `.anvil.bak.N` siblings
  first (the same convention as `host wsl-config`).

### Changed (BREAKING for non-interactive callers)

- **`serves rm` / `serves adopt` now require consent**: interactive `[y/N]`
  on a TTY, `--yes` everywhere else. A non-interactive call without `--yes`
  (cron, CI, pipelines) now aborts with rc 1 and removes NOTHING — fail-closed
  by design. Update automation to pass `--yes`. The MCP `serves_manage` tool
  passes it automatically once its own confirm gate is satisfied.
- **`router restart` / `reload` block ~11s longer** verifying the router
  stays up (crash-loop detection); `--no-verify` restores the old fire-and-
  forget behavior.

### Fixed

- **Serve-manifest upgrade and environment isolation** - manifests generated
  before the `engine` field was introduced load through deterministic legacy
  inference while malformed or contradictory engine declarations still fail.
  Each loaded serve now owns its manifest directory directly, eliminating the
  unbounded object-ID map and preventing another manifest's `.env` values from
  leaking into long-lived lifecycle/controller processes.
- **Container startup follows the canonical CLI.** The Docker image entrypoint
  now runs `anvil-serving router run`; the removed root `serve` tombstone could
  not start a container built from current `main`.
- **Operator CLI v2 adversarial hardening** — non-local topology plans now fail closed before a
  local handler can run; JSON preserves resolved context, warnings, and classified errors; real
  leaf parsers provide focused help; `--` boundaries and dry-run confirmation behave correctly;
  token values require `--reveal --confirm`; and every visible canonical leaf either reaches a real
  parser or is withheld until implemented. Live documentation now uses canonical command paths.


## [0.11.0] - 2026-07-06

> Source checkpoint on `main`; not tagged or published as a package. Its changes
> are included in v0.12.0.

### Added

- **OpenClaw MCP control plane and split-host controller transport.** Added `anvil-serving mcp`
  as the structured operational surface for router status, serve status, doctor summaries, route
  probes, OpenClaw config sync/restart, preflight probes, and benchmark probes. Added
  `anvil-serving controller serve` as a stdlib-only HTTP controller for the anvil-serving host, plus
  gateway-side proxy mode (`anvil-serving mcp --controller-url ... --auth-env ANVIL_CONTROLLER_TOKEN`)
  so `fakoli-mini` can operate a GPU/router host over a private tailnet without raw SSH/shell as the
  product contract. The controller reuses the MCP tool registry, requires env-token auth by default,
  rejects unsafe public/wildcard binds unless explicitly gated, exposes
  `/health`, redacts controller-token values, and writes structured audit records.
- **ADR-0013 / ADR-0014 and operator playbooks.** Documented the clean OpenClaw layers
  (hook adapter for per-turn intent, router data plane for quality, MCP/controller for operations)
  and the tailnet controller transport for split-host deployments. Added operator playbooks for
  model inventory, preflight, benchmark, OpenClaw sync, promotion evidence, and controller failure
  handling.
- **Operator workbench skills and sub-agent workflows.** Added the cross-harness
  `anvil-serving-workbench` skill for Codex, Claude Code, and OpenClaw, plus
  Codex/Claude sub-agent role profiles for orchestration, inventory, route analysis,
  serve operation, preflight, benchmark, evidence reporting, quality criticism, and
  adversarial review. `harness sync openclaw --skills` now renders the workbench
  skill and Anvil-owned role config while preserving operator-owned OpenClaw settings.
- **Structured operator MCP tools.** Added MCP/controller coverage for model
  inventory, guarded serve and router lifecycle operations, bounded logs, decision
  summaries, route probes, benchmark artifact capture, advisory external benchmark
  reports/comparisons, read-only host summaries, and cache-prune planning.
- **Workflow result packet validation.** Added `operator-workflow/v1` packet
  validation, fixture-backed model-swap evidence workflow tests, promotion proof
  checks, artifact path bounding, advisory-prior rules, and voice-pipeline artifact
  scoping so voice benchmark results cannot count as router work-class promotion
  evidence.
- **Voice operations skill.** Added `skills/anvil-serving-voice-ops` to validate
  sidecar manifests, render sidecar commands/compose snippets, operate existing
  `voice` verbs, and collect bounded voice benchmarks as voice-pipeline evidence.

### Fixed

- **OpenClaw authoritative route probes are now auth-aware and truthfully logged.** The
  OpenClaw intent plugin can resolve a `/v1/route` token by env-var name
  (`ANVIL_ROUTE_AUTH_ENV` or `routeAuthEnv`) and sends both bearer and `x-api-key` headers. Decision
  logs mark `authoritative:true` only when `/v1/route` returns a valid tier; route-endpoint 503s
  route explicitly to the configured native provider/model, while other route failures fall back to
  the deterministic client classifier with `routingSource:"client-side-fallback"`.
- **Benchmark probes now fail closed on incomplete runs.** `anvil-serving benchmark` exits non-zero
  when completed requests are fewer than requested, so MCP/controller benchmark probes surface partial
  runs as tool errors instead of successful evidence. Recipe emission now happens only after a complete
  run, preventing partial benchmarks from appending default `verified` serve recipes.
- **Controller JSON-RPC notifications are side-effect safe.** A no-id JSON-RPC notification no
  longer executes `tools/call`; the controller returns `204 No Content` for such notifications.
- **MCP/controller operational-safety hardening.** Strict boolean parsing prevents string values such
  as `"false"` from satisfying `confirm:true`; stdio MCP no longer executes no-id `tools/call`
  notifications; `id:null` returns a protocol error without side effects; probe URLs must resolve to
  loopback/RFC1918/IPv6-ULA/tailnet addresses; probe auth env vars are limited to
  `ANVIL_ROUTER_TOKEN`; confirmed probe
  subprocesses are timeout-bounded and non-zero exits surface as tool errors; numeric wildcard bind
  aliases such as `--host 0` are refused without the public-bind gate. Controller auth is required by
  default even on loopback; unauthenticated loopback now requires the explicit
  `--allow-unauthenticated-loopback` development flag. Auth-bearing MCP HTTP calls disable environment
  proxies and redirects, duplicate `Content-Length` headers are rejected, and controller binds now
  allow only loopback/RFC1918/IPv6-ULA/tailnet or explicitly gated public addresses.
- **OpenClaw harness/plugin safety fixes.** Harness sync preserves existing plugin `config`
  (`cloudClasses`, `routeEndpoint`, `routeTimeoutMs`, `routeAuthEnv`, native route overrides) while reasserting
  Anvil-owned hooks; SSH/SCP gateway targets reject option-injection strings; remote SSH/SCP
  operations are timeout-bounded; remote restart uses a fixed login-shell command after the validated
  SSH target so user-level OpenClaw installs are found without shelling user input; authoritative
  `/v1/route` probes send the classified work class instead of hard-coding `chat`; fixture generation
  ignores operator `ANVIL_CLOUD_CLASSES`; arbitrary `--out --restart` syncs are rejected unless the
  output is the real local OpenClaw config path.

## [0.10.0] - 2026-07-06

> Tagged source checkpoint, but not published as a GitHub release or package.
> Its changes are included in v0.12.0.

### Added

- **`anvil-serving host` verb — own the WSL / Docker Desktop host config, with safety rails.** Closes
  the "reach for raw `wsl` / hand-edit `.wslconfig` / restart Docker Desktop" gap so anvil is the
  one-stop shop. `host doctor` inspects host RAM / GPUs / the WSL-VM cap and RECOMMENDS a SAFE WSL
  memory (host − a Windows reserve). `host wsl-config --memory/--swap` edits `.wslconfig` — BACKS UP
  first, changes only those lines (preserves a custom kernel/networking), and REFUSES a value that
  starves Windows (< 10 GB floor) unless `--force`; `--revert` restores the newest backup. `host
  restart-docker` applies a WSL-backend change the RIGHT way (a Docker Desktop restart, NOT
  `wsl --shutdown`), confirming unless `--force`. Encodes the backup-on-change / revert / confirm +
  `--force` pattern a live incident taught (a hand-set `memory=84GB` on a 93.7 GB host starved Windows
  and a `wsl --shutdown` loop wedged WSL — `host wsl-config` now refuses exactly that).
- **`anvil-serving host reset-wsl` — un-wedge a hung WSL subsystem.** When `wsl` commands time out and
  Docker Desktop can't start (hundreds of stuck `wsl.exe` pile up), this force-kills the WSL VM
  (`vmmemWSL`) + the hung `wsl.exe` front-ends and restarts Docker Desktop to rebuild the backend —
  codifying the manual Task-Manager "End task on `vmmemWSL`" recovery (confirmed from the Windows System
  log for the same 2026-07-04 incident). Deliberately does NOT use `wsl --shutdown` (the wedged CLI
  front-end blocks — that loop is what wedged it). Confirms unless `--force`; if the kill is denied it
  surfaces the elevated `Restart-Service WSLService -Force` fallback. +4 tests (DI'd; no WSL needed).
- **`host` verb hardening (adversarial review + Greptile/Copilot).** Process control moved from
  `taskkill`/`cmd start` to PowerShell `Stop-Process`/`Start-Process` — outcomes come from PowerShell's
  `ErrorCategory` **enum**, so denial detection is **locale-independent** (`taskkill`'s "Access is denied"
  text would silently miss on non-English Windows). `wsl-config` now **fails CLOSED** when host RAM is
  unreadable (`_host_total_gb` → None): it REFUSES rather than silently skipping the Windows-floor check
  (the fail-open that would have reproduced the starvation incident), and `_host_total_gb` checks the exit
  code + has a timeout. `reset-wsl` **propagates failure** (non-zero exit when the VM kill is denied/errors
  or Docker Desktop can't relaunch — automation can detect an incomplete reset). Backups number from
  `max(suffix)+1` (not the count) and write with exclusive mode, so a pruned/gapped backup can't collide
  with or overwrite an existing one; `recommend` clamps to the appliable ceiling (never suggests a value
  `wsl-config` would refuse, `None` on too-small hosts); `.wslconfig` section detection tolerates a trailing
  comment on the `[wsl2]` header (no duplicate section); and all non-ASCII was purged from the module's
  output (an em-dash/minus would mojibake/crash the cp1252 Windows console).

### Fixed

- **harness sync KEEPS OpenClaw's dropdown allowlist.** `agents.defaults.models["anvil/*"]` is
  OpenClaw's DROPDOWN ALLOWLIST — a preset appears only if listed there. The sync's "drop stale
  `anvil/*` overrides" step deleted the ENTRIES (not just the stale `enable_thinking` params), which
  removed the anvil presets from OpenClaw's picker entirely (hit live re-syncing Mini for the
  reasoning rollout). The render/merge now KEEP every preset's allowlist entry (empty params) and
  strip only the stale params; recipe + CLAUDE.md golden rule corrected to match.
- **`anvil-serving router up` now passes `--no-deps`** so it manages ONLY the router. Without it,
  `docker compose up router` re-runs `depends_on` and RECREATES the model serves whenever their
  resolved config drifts (e.g. a changed `--env-file`) — a gpt-oss-120b reload is minutes of 503s.
  (Hit live redeploying to 0.9.0.) The serves are `serves`' responsibility, not the router verb's.
- **harness sync preserves the gateway's LIVE credentials, and `--restart` uses a login shell.** The
  gateway-merge now KEEPS an existing anvil-provider `baseUrl`/`apiKey` (the rendered ones are just a
  default host + a `${ENV}` placeholder), so re-syncing a gateway that pins a LITERAL token no longer
  clobbers it into a 401 (hit live re-syncing Mini). And `harness … --restart` runs `openclaw gateway
  restart` via `$SHELL -lc` so the remote PATH resolves `openclaw` — a bare non-login ssh shell
  couldn't find it (installed under `~/.local/bin`/a brew prefix/etc.).

## [0.9.0] - 2026-07-04

### Added

- **Per-request reasoning selection (gpt-oss `reasoning_effort`).** New tier field
  **`extra_body_defaults`** — like `extra_body` but applied via `setdefault` (the request WINS), so a
  tier's `reasoning_effort` becomes a DEFAULT a caller can override instead of a hard pin. The router
  now also forwards a request's `reasoning_effort` to the upstream (OpenAI dialect), and the harness
  renders the OpenClaw models with `reasoning: true` — so OpenClaw's per-message reasoning selector
  actually takes effect. The flexibility heavy tier now defaults to `high` via `extra_body_defaults`
  (was a hard `extra_body`), so planning/etc. can be dialed low/medium per message; a hard `extra_body`
  key still always wins (contract preserved). Requires a router redeploy + a harness re-sync to pick up.

- **`anvil-serving router up --env-file` — persist the deploy secrets so a redeploy is reproducible.**
  The router fail-closes without `ANVIL_ROUTER_TOKEN` and reverts to loopback without `ROUTER_PUBLISH`;
  those lived only in the deploy shell env, so a bare `router up` / `docker compose up` would break the
  running router. `router up` now passes `--env-file` to compose (auto-detecting `~/.anvil_env` then
  `~/.env`, override with `--env-file`, disable with `--env-file ''`), so the token + tailnet publish
  come from a persisted file (which also carries `HF_TOKEN` for the serves).

- **`anvil-serving harness restart openclaw` + `sync --restart` — reload the gateway so settings
  apply.** OpenClaw reads its config at gateway STARTUP, so a synced config change is inert until a
  restart. `harness restart openclaw [--gateway-host <mini>]` runs `openclaw gateway restart` (locally
  or over ssh); `harness sync openclaw … --restart` restarts right after a successful push. It's a
  single command invocation (not a shell script), so it stays portable against a Windows/macOS/Linux
  gateway. `--config` is now optional (required only for `sync`).

- **`anvil-serving router logs` + `serves logs` — `docker logs` through the management verbs.**
  Diagnosing a router crash-loop or a serve no longer means reaching for raw `docker` (the same
  gap ADR-0012 closed for lifecycle). `router logs` and `serves logs <name>` take `--tail`/`--since`/
  `--follow`, check the container exists first (a clean message beats docker's raw error), and
  surface BOTH stdout and stderr (a router's fail-closed startup errors — e.g. a missing auth token —
  go to stderr). `serves logs` requires exactly one serve. Docker is dependency-injected, so tests
  run with no docker.

- **flexibility:T016 — Qwen3.5-122B-A10B (MXFP4) serves on sm_120 via a patched vLLM Marlin W4A16
  path**, proving the any-engine seam on the hardest case. Standard vLLM routes this W4A4 MXFP4
  checkpoint to FlashInfer's cute-dsl `mm_fp4`, which dies on sm_120 (`does not support backend
  'cute-dsl' with capability 120`); removing the (sm_120-broken) `flashinfer.cute_dsl` module at
  startup forces vLLM's *designed* Marlin W4A16 fallback. New reusable recipe
  `examples/fakoli-dark/docker-compose.flexibility.yml` + a `docs/findings/blackwell-sm120-lab-notebook.md`
  writeup. Correctness preflight = **ALL PASS** (smoke, structured JSON, 14k needle, 20/20 tool batch)
  with `--no-thinking`.
- **`anvil-serving harness` verb — own the harness-side config, not just the router.** `harness sync
  openclaw --config <router.toml>` RENDERS the OpenClaw provider config from the live router config —
  one selectable model per preset, each `contextWindow` = the LARGEST tier that preset can route to
  (the clamp gotcha), and NO per-preset thinking overrides (the router owns `reasoning_effort`/
  `enable_thinking` per tier now). Emits to stdout/`--out`, or PUSHES to the remote gateway with
  `--gateway-host` — transport is **`scp` (portable: runs on a Windows OR Linux host, against a
  Windows/macOS/Linux gateway — no remote shell)**, MERGING the anvil provider into the remote
  `~/.openclaw/openclaw.json` (preserving other providers/agents, dropping stale `anvil/*` overrides,
  backing it up first); `--overwrite` for a full write. Closes the "hand-edit the gateway out-of-band"
  gap named by the new
  CLAUDE.md golden rule (**anvil-serving owns the harness-side config too** — keep it in lockstep with
  the router's intent/tier config). Also ships the reconciled `examples/openclaw/openclaw-flexibility.json5`
  recipe. Skills/agent-config sync is the next scope. (The OpenClaw gateway runs on Fakoli Mini.)

### Changed

- **fakoli-dark router redeployed to the v0.8.0 release image** (from the transitional `0.7.1` pin
  in #125): the `router` compose service and `router_manage.DEFAULT_IMAGE` now pin
  `anvil-serving:0.8.0` — rebuilt from main, so the deployed router has flexibility mode + the v2
  profile loader (backward-compatible with the live v1 profile), and `router promote --image`
  validates against 0.8.0. Live routing verified after the swap (planning/chat/quick-edit → 200).

### Fixed

- **harness `--restart` guards (Greptile #130):** reject `--restart` on a stdout-only sync (the config
  isn't applied, so restarting would reload the OLD config and falsely report success) — require
  `--gateway-host` or `--out`; and reject sync-only flags (`--config`/`--out`/…) on the `restart`
  action instead of silently discarding them.

## [0.8.0] - 2026-07-04

### Fixed

- **Conservative per-request context gate: an over-context request is refused, not forwarded to a
  too-small tier.** A live incident routed a ~94k-token request to a 65k/32k-context local tier
  (heavy tier was down, so the preset fell back to fast), which 400'd at the model with "Input
  length exceeds maximum context length" plus an ASGI traceback. `policy.route()` has always had
  the hard-constraint filter (`needs.min_context > tier.context_limit` -> drop tier), but
  `serve.RoutingBackend` left `Needs.min_context` at 0, so it never fired.
  `serve._needs_for` now wires `min_context` from `internal.estimate_tokens` (a whitespace WORD
  count — a strict lower bound on real tokens: >= 1 token per word, English ~1.3x, dense code/JSON
  2-4x). The raw word count is used with **no** extra discount, so the filter drops a tier only when
  even this underestimate exceeds the tier's real-token `context_limit` (effectively real
  `tokens > ~1.3x limit`): a built-in cushion that catches the 1.4x incident while never
  false-rejecting a request merely near a tier's limit. When the gate drops EVERY candidate tier,
  `NoAvailableTierError(kind="over_context")` is raised and the front door renders a clean **413
  Payload Too Large** (distinct from the availability 503/`exhaustion_status`), instead of
  forwarding a doomed request or emitting a bare 500. `policy.route` records the specific tiers in a
  new additive `dropped_by_context` note bucket. stdlib-only, additive; normal-size requests route
  exactly as before.

### Added

- **Hugging Face `speech-to-speech` sidecar support.** Added a stdlib-only
  `anvil-serving voice-sidecar` helper that validates the sidecar manifest, renders the
  host `speech-to-speech` command, and emits a Docker Compose service skeleton for the
  v1 voice topology: OpenClaw Gateway remains phone-facing, Hugging Face
  `speech-to-speech` owns `/v1/realtime` / VAD / STT / TTS, and anvil-serving remains
  the OpenAI-compatible Chat Completions LLM backend. Added docs, a checked-in
  OpenClaw bridge sketch, 16 GB shared-memory validation guidance, and static tests that
  keep the example free of `localhost` and literal secrets.
- **External benchmark priors:** new `anvil-serving external-bench` CLI and
  `anvil_serving.external_benchmarks` package for ingesting raw external benchmark snapshots,
  normalizing Millstone AI rows, storing them in SQLite, exporting JSON, producing Markdown
  reports, and comparing local Anvil benchmark JSON against advisory external rows. These rows
  are performance priors only and do not change routing quality gates.
- **`rtx6kpro` external benchmark source:** added a JSON-only adapter for
  `local-inference-lab/rtx6kpro` RTX PRO 6000 Blackwell inference-throughput artifacts, including
  conservative Qwen/GLM metadata normalization, DCP and speculative-decoding methodology notes,
  and non-destructive failures for prose, CSV, or HTML imports.
- **Serve & router management verbs (ADR-0012):** every serve/router lifecycle op now flows through an
  `anvil-serving` verb instead of raw docker. `anvil-serving router {up|down|restart|reload|status|token}`
  manages the deployed (ADR-0004) containerized router; `anvil-serving router promote --profile [--config]`
  is the containerized profile write-back (the ADR-0009 moat) done safely — validate against the deployed
  image's OWN loader, back up, ATOMICALLY write into the read-only-mounted config volume via a root
  side-container, reload, and ROLL BACK on a crash-loop (settle + consecutive-`running` + `RestartCount`).
  New `serves rm` (retire any container incl. a non-manifest port squatter), `serves adopt` (recreate an
  externally-started serve under compose management), and `serves up --compose <file>` (bring up an
  experiment serve not in the manifest); `serves down` now honors `--dry-run` (was silently stopping
  serves). The fakoli-dark `docker-compose.yml`/`serves.toml` are reconciled to the live flexibility
  topology (heavy=gpt-oss-120b :30002, fast=Qwen3.6-27B-NVFP4 :30003, `vllm-hfcache` + HF repo-ids) so
  `anvil-serving serves` manages the real serves again.

## [0.7.3] - 2026-07-02

### Changed

- **fakoli-dark heavy tier enables NEXTN speculative decoding** (ADR-0008). Self-speculation via
  the model's own built-in MTP head (no separate draft model, no additional steady-state VRAM
  cost) — validated live with a two-step A/B on production hardware before merging: +30-43%
  decode throughput depending on concurrency, ~82% draft-token acceptance rate, and confirmed
  SGLang issue #19796 (an SM120-specific NaN-on-prefix-cache-hit crash) does not reproduce on
  this stack at cache-hit rates up to 96.2% under concurrent multi-turn traffic. Known tradeoff:
  TTFT regresses under concurrency (+37% at concurrency=4); net end-to-end latency still improved
  in every trial. No wire-level change — `served-model-name` and the router config are unaffected.

## [0.7.2] - 2026-07-02

**Weights on a volume + docs truth-up.** Two fixes from live operation, and a documentation
pass that brings every stated claim back in line with the shipped code.

### Fixed

- **Model weights mount from a named Docker volume, never a host bind mount** (#107). On
  Docker Desktop/WSL2, 9P/virtiofs bind mounts turned cold model loads into 20–90 minute
  stalls. All serve definitions — the fakoli-dark compose files, the legacy serve scripts,
  and the multiplexer's default registry (new `volume` registry key) — now read weights
  from an external named volume, with container paths unchanged so serve fingerprints are
  unaffected. This also removed the last machine-specific host paths from the shipped
  package.
- **Eval data default resolves to `tests/fixtures/eval-data`** (#106) — the previous
  default pointed at a directory relocated to the companion notes repo; the vLLM
  experiment entrypoint is pinned alongside it.

### Documentation

- **ADR-0007** (#105): a Claude-subscription cloud tier is feasible and permitted for
  self-hosted single-operator use — opt-in, subprocess-to-CLI, text-only classes, no tool
  broker, documented ToS-gray. Design-only; no implementation scheduled. Companion pi
  harness recipe added to the README.
- **Docs truth-up (positioning refresh):** README *Known limitations* rewritten to include
  the live-confirmed ADR-0005 keyless-failover caveat, the promotion-table evidence-erosion
  note (the reference heavy serve moved off the model the seeds were measured against;
  shadow-eval re-run recommended), and the Anthropic-dialect `NotTruncated` pass-through
  behavior introduced by the v0.7.1 caller-cap fix. AGENTS.md updated off v0.4.1/707-tests
  to v0.7.x/993; README/CLAUDE.md test counts corrected to 993 collected; mkdocs nav now
  publishes ADR-0002–0007 and the 2026-07-02 architecture review; docs version badge
  bumped; stale `relay.py` (non-streaming upstream) and `serves.py` (manifest default)
  docstrings corrected.

## [0.7.1] - 2026-07-02

**Live-incident hardening** — a LIVE end-to-end run (2026-07-02) found a harness that
computes `max_completion_tokens = declared contextWindow − prompt tokens`, floored at 1
(never rejects an oversized prompt). A misdeclared `contextWindow` made every real turn
arrive with `max_completion_tokens: 1`; the local model correctly honored the cap and
returned its one token with `finish_reason: "length"` — but anvil's `NotTruncated`
verifier had no way to tell a caller-requested cap from an unexpected truncation, so it
hard-failed every such response on every tier: 503 exhaustion on every turn, and the
repeated verify-failures tripped the circuit breaker, blacking out an otherwise-healthy
work-class for the cooldown window. The exhaustion 503 also printed a misleading message
("configure that tier's credentials/endpoint") for a case where the tiers were bound and
reachable the whole time.

### Fixed

- **Caller-capped `length`/`max_tokens` is compliance, not truncation** (the headline
  fix). `verify.ResponseView` gained a `caller_max_tokens` field, populated from the
  request's own `max_tokens` (parsed from `max_tokens`/`max_completion_tokens` by the
  dialects) at both response-view construction sites (`serve.py`'s
  `_structured_view_factory` and `commit_window.build_response_view`, the fallback used
  when a caller injects no factory). `NotTruncated` now passes a `length`-like stop when
  the caller set an explicit cap — it is exactly what was asked for. When the caller set
  **no** cap at all, a `length`-like stop is still treated as genuine unexpected
  truncation (unchanged). The critical interaction is preserved: an EMPTY,
  caller-capped `length` response (thinking-budget starvation, CLAUDE.md gotcha #9)
  still fails via `NonEmptyContent` — only a non-empty caller-capped response passes the
  full chain. With verify passing, no failure is recorded, so the breaker-poisoning stops
  too. Regression-pinned end to end: a real `max_tokens: 1` request through the front
  door + a local `allow` tier now returns 200 with the 1-token body, not a 503, and does
  not increment the circuit breaker across repeated 1-token-capped requests.
- **Exhaustion 503 message no longer blames credentials when the tiers were bound and
  reachable.** `internal.NoAvailableTierError` gained a `kind` parameter
  (`"unbound"` default / `"exhausted"`) distinguishing the two raise sites in
  `serve.py`'s `RoutingBackend.generate()`: `bound_tiers` empty (genuinely unbound — the
  "configure credentials/endpoint" message is correct and unchanged) vs. every bound
  candidate attempted and failed verify/relay (now says so — "all N bound candidate
  tiers were attempted and failed (verification or relay error); see the decision log" —
  instead of pointing at credentials/reachability). Same exception type throughout — the
  front door's `except NoAvailableTierError` contract is unchanged.
- **Docs:** `docs/OPENCLAW-INTEGRATION-SPEC.md` §2's provider-config recipe now declares
  `contextWindow: 131072` (the largest routed tier's window, `heavy-local`) for every
  preset instead of the previous `32000`-class values for `chat`/`quick-edit` that
  under-declared their real routed ceiling — the live-confirmed failure mode above is
  documented in full alongside the corrected recipe.

## [0.7.0] - 2026-07-01

**Wire fidelity + production hardening** — the relay now forwards what the harness actually sent
(tools, tool history, sampling parameters) and streams what the model actually produced (real SSE
deltas, real token counts), with a full-codebase hardening pass behind it.

### Fixed

- **Tools and tool history were silently dropped on relay** (#96) — the headline fix. The relay
  backends rebuilt the upstream body from the flattened `InternalRequest`, which dropped the
  request's `tools` / `tool_choice` and the `tool_use` / `tool_result` conversation history — a
  routed tier could never call a tool and lost its own tool history between turns. New
  `dialects/translate.py` (pure stdlib) translates tool definitions, `tool_choice`, and
  tool-carrying message history between the Anthropic and OpenAI wire shapes;
  `CloudBackend._build_body` forwards same-dialect requests verbatim and translates cross-dialect
  ones (e.g. Claude Code → local vLLM). Tool-free requests build a byte-identical body to before
  (regression-pinned). Verified live: a real 104-tool OpenClaw agent turn now reaches the local
  model and returns a real `tool_calls` response.
- **`relay()` now actually streams** (#98). `resp.read(65536)` on an `http.client` response blocks
  until 64 KB accumulate or EOF, so SSE token deltas were delivered all at once at end-of-stream —
  TTFT equaled full completion time. `read1()` returns per-chunk. The most user-visible fix in the
  hardening pass.
- **Classifier keyword haystack** (#97): only a short (≤150-word) system prompt joins the keyword
  scan — a harness's standing multi-thousand-word system prompt permanently contains
  "plan"/"review"/"edit"/"fix", which multi-matched every request into an ambiguous verdict and
  drowned the actual intent of the last user turn.
- **Public-bind warning is auth-aware** (#97): with `[server].auth_env` configured it notes the
  token gate instead of falsely claiming the endpoint has no authentication.
- **Production hardening bug bash** (#98) — router core: `DecisionLog` is a bounded ring buffer
  (default 10k records; was an unbounded per-request append — a slow leak on a long-running
  router); `RouterConfig.tier()` is O(1); an abandoned circuit-breaker half-open probe no longer
  wedges a tier OPEN forever (probes expire after one cooldown); the fence-scan verifier is linear
  (was O(spans × delimiters) — adversarial many-fence responses cost ~10⁹ comparisons in the
  hot path); front-door keep-alive desync and trailing-slash fixes. Support modules: multiplexer
  swap-path hardening (dead-child detection, checked `docker rm -f`, zombie reaping, OOM-guard
  eviction credit, clean 4xx/5xx) and **loopback bind by default** (was `0.0.0.0` — an
  unauthenticated model-swap endpoint on the LAN); calibrate bounded backpressure
  (`max_pending=64`, drops counted); secrets redaction is component-boundaried (`context_limit`
  no longer destroyed by a substring match on `text`); prices parse-before-cache, atomic writes,
  stale-cache fallback, per-process memo; case-insensitive inferred-preset resolution;
  `PYTHONHASHSEED`-independent fingerprints (set values canonicalized — set-valued serve flags
  re-fingerprint once on upgrade).
- **`policy.Needs.needs_tools` was never populated on the serve path.** `policy.route()` has always
  honored `needs.needs_tools` (excludes `tool_support=false` tiers), but `serve.RoutingBackend`
  never constructed a `Needs` — `route()` was always called with `needs=None`, so a tools-bearing
  request could route to a tier with no tool support (the model would then be unable to call any
  tool it needed). Wired via `dialects.translate.has_tool_artifacts` (#96): both `RoutingBackend.generate`
  and `RoutingBackend.decide` now build a `Needs(needs_tools=...)` from the raw wire body before
  calling `route()`. (`Needs.min_context` was wired conservatively later — see the Unreleased
  "Conservative per-request context gate" entry above.)
- **Verify: empty-content false-negative on tool-call-only local replies (regression coverage).**
  Live end-to-end testing with a real OpenClaw agent turn reported a local model reply with empty
  text `content` but a populated `tool_calls` being wrongly treated as thinking-budget starvation
  by `NonEmptyContent` and escalated/exhausted to a `503`. Investigation found the router logic was
  already correct on `main` — `NonEmptyContent` (`anvil_serving/router/verify.py`) already passes on
  a non-empty `tool_calls` list even with empty text, and `RoutingBackend._route_with_verify`
  (`anvil_serving/router/serve.py`) already threads a backend's `tool_calls`/`finish_reason` into the
  `ResponseView` via `get_last_structured()` — landed by the structured-field-passthrough work
  (#42/#52), which predates and is included in v0.6.0. A genuinely empty reply (no text AND no
  `tool_calls`) still correctly fails and escalates/defers, per the T004 safety net. Added end-to-end
  front-door regression tests (`tests/router/test_serve_fallback.py`,
  `tests/router/test_serve_verify_fallback.py`) and unit-level edge-case pins
  (`tests/router/test_verify.py`) locking in the tool-call-only-pass / truly-empty-fails contract at
  both the T004 minimal-verify local-"allow" path and the full allow-with-verify chain, since no
  end-to-end coverage previously existed for this shape. If this was observed against a *deployed*
  container, rebuild/redeploy from a commit that includes #42/#52 (any v0.6.0+ build already does).

### Added

- **Measured-profile loading** (#97): `[router].profile_path` loads a measured `profile.json`
  (written by `profile_bootstrap` / eval bootstrap) instead of always routing on the hand-authored
  seed profile. Configured-but-unloadable is a startup `ConfigError` — fail fast, never silently
  fall back to seeds the operator asked to replace.
- **Real usage passthrough** (#97): the relay backends extract the upstream's real `usage` block
  and both dialects render the real token counts when present (word-count estimate remains the
  fallback). Harnesses use these numbers for context management, so the estimated fiction was
  actively misleading.
- **Sampling-field wire fidelity (`top_p` / stop sequences).** `InternalRequest` now carries
  `top_p` and a normalized `stop` (list of strings — OpenAI's string-or-array `stop` form is
  collapsed to a list; Anthropic's `stop_sequences` is native). Both dialects parse them
  (`dialects/openai.py`: `top_p` / `stop`; `dialects/anthropic.py`: `top_p` / `stop_sequences`),
  and `CloudBackend._build_body` (`anvil_serving/router/backends/cloud.py`) forwards them with
  dialect-correct wire names, only when present, so an absent field builds the exact same body as
  before (extends the #96 byte-identical regression pin). Also forwards same-dialect-only
  `top_k` (Anthropic) and `presence_penalty` / `frequency_penalty` (OpenAI) — never invented for a
  translated cross-dialect request. Deliberately NOT forwarded: `logit_bias`, `seed`, `user`,
  `metadata` — provider-account/session-scoped fields (billing attribution, abuse tracking,
  deterministic-replay opt-in), not generation-quality knobs, so passthrough would leak
  caller-side state for little harness value. A tier's `extra_body` (applied last, #97) still
  overrides any of these — documented precedence, now test-pinned.
  Previously a harness sending `top_p` or a stop sequence had it silently dropped: the local/cloud
  model sampled with different parameters than requested.

## [0.6.0] - 2026-07-01

**Router as a service** — the front door is now a containerized, network-facing, **token-authed**
endpoint ([ADR-0004](https://github.com/fakoli/anvil-serving/blob/main/docs/adr/0004-router-as-a-service-containerized-and-authed.md)),
so the serves stay loopback-only behind one authenticated boundary and keep-alive comes from Docker.

### Added

- **Built-in front-door token auth (opt-in).** `[server].auth_env` names the env var (e.g.
  `ANVIL_ROUTER_TOKEN`) holding a shared token; the front door accepts `Authorization: Bearer <t>` or
  `x-api-key: <t>`, compares constant-time (`hmac`), and returns `401` on mismatch. **Off when unset**
  (loopback default unchanged); configured-but-env-unset fails fast. Unauthenticated `GET /healthz`.
- **Repo-root `Dockerfile`** (stdlib-only image, non-root, `HEALTHCHECK` on `/healthz`) and a
  router+serves compose topology: the `router` is the only published, authed service; the serves stay
  loopback-only and are reached by service name. Ships `configs/example-docker.toml`.

### Changed

- **`SECURITY.md`** documents the built-in bearer/`x-api-key` auth (supersedes the old "no built-in
  authentication" note); the raw serves stay loopback/internal behind the router.

## [0.5.0] - 2026-07-01

**Portable-by-default** — out-of-box router correctness and a generated bring-up
([ADR-0003](https://github.com/fakoli/anvil-serving/blob/main/docs/adr/0003-portable-defaults-and-generic-onboarding.md)),
so anvil-serving works generically, not just on the authors' setup.

### Added

- **`anvil-serving init` / `onboard`** — one command detects GPUs and emits a mutually-consistent
  compose + `serves.toml` + router config. **`anvil-serving doctor`** environment preflight. Shared
  `gpus.py` GPU-UUID pinning; `deploy` gains a vLLM engine, loopback-default publish, and serves.toml +
  router-tier emission. Per-tier **`extra_body`** (inject `chat_template_kwargs.enable_thinking=false`
  for thinking-by-default models); configurable **`[router].relay_timeout`**; `/v1/models` served-name
  auto-derive.

### Fixed

- **Shipped example configs 404'd out of the box** (a local tier without `model=` forwarded the preset
  token upstream) — `model=` is now required and warned. **verify-on-local-`allow`** catches an
  empty/truncated local `200` instead of delivering it. README states Python ≥3.11 + a pipx recipe;
  the OpenClaw plugin install uses `--link`.

## [0.4.1] - 2026-06-30

Serving-substrate hardening: model serves are now Docker-Compose-defined and `serves up`
is drift-safe, plus Blackwell sm_120 serving guidance. No router changes; no breaking
changes.

### Changed

- **Model serves are Docker-Compose-defined ([ADR-0002](https://github.com/fakoli/anvil-serving/blob/main/docs/adr/0002-serves-are-compose-defined.md)).**
  `anvil-serving serves up` delegates to `docker compose up -d <service>`, which recreates a
  container when its compose config has drifted and fast-restarts it when unchanged —
  replacing a blind `docker start` that could silently serve a stale model. Added a
  parametrized experiment-harness compose (`examples/fakoli-dark/docker-compose.experiment.yml`).
  **Docker Compose v2 is now a serving-substrate prerequisite** (the router itself stays stdlib-only).
- `serves up` gained a `--recreate` flag (force `docker rm -f` + up) and a served-vs-declared
  model drift warning for script-based serves.
- Serve ports bind `127.0.0.1` only; GPU pinning uses `CUDA_VISIBLE_DEVICES` (reliable on
  Docker-Desktop/WSL2) alongside Compose `device_ids`.

### Docs

- Blackwell **sm_120** serving gotchas (dense NVFP4 vs the MoE-NVFP4/block-FP8 kernel gaps,
  NVFP4≈1.8×FP8, the `VLLM_USE_V2_MODEL_RUNNER=0` UVA fix, the docker-volume vs 9P load path)
  in `CLAUDE.md`; ADR-0002.

## [0.4.0] - 2026-06-30

Advise-and-defer — the subscription-first routing pivot — plus the launch-hardening pass.
anvil is now **local-serve + routing brain**: the harness owns cloud on its subscription and
no cloud API key sits in the default path ($0 metered API by default). This release also closes
the six post-launch hardening issues (#42, #45, #46, #47, #52, #53).

### Changed

- **Cloud tier is now opt-in, OFF by default.** `configs/example.toml` ships as
  local-only; anvil holds no cloud API key and incurs **$0 metered API billing** in the
  default configuration. A cloud tier must be explicitly declared in
  `configs/example-with-cloud.toml` to unlock it.
- **Keyless exhaustion handoff replaces mid-request cloud escalation (default path).**
  When all local candidates are exhausted (verify-failure on an `allow-with-verify` class
  with no cloud tier configured), anvil returns an **`exhaustion_status`** (503 by
  default, configurable) with nothing streamed. A gateway like OpenClaw treats this as a
  transport failure and re-routes the request on its native subscription provider —
  flat-rate, not metered by anvil. The opt-in keyed `CloudBackend` path still works for
  single-endpoint harnesses that cannot route cloud themselves.
- **Contract C4 reshaped into two explicit modes** — *keyless* (exhaustion-503 → gateway
  transport failover) and *opt-in keyed* (router-internal escalation → 200). Documented
  in `docs/QUALITY-GATED-ROUTER.md` and `docs/PLAN-advise-and-defer.md`.
- **Docs and visual assets refreshed** to reflect advise-and-defer terminology (local-only
  default, opt-in metered cloud, keyless handoff, $0-metered framing). Internal
  design/planning/findings documents relocated to the private companion repo
  `fakoli/anvil-serving-notes`; public docs retain the product-facing surface.
- **Internal maintainability (#46).** `RelayBackend` decoupled into the backends package;
  dialect/privacy magic strings replaced with named constants; a dialect parity test pins both
  dialects' surface. Behavior-preserving — no wire change.

### Added

- **Per-intent `metered_cloud` gate.** When a cloud tier *is* configured, no work-class
  is eligible for it unless explicitly listed in `[router].metered_cloud`. No implicit
  global "use cloud" switch exists.
- **Cost dimension.** A configured cloud tier carries `cost_input_per_mtok` /
  `cost_output_per_mtok` fields (USD per million tokens). Estimated cost is surfaced in
  the decision log and a `cost_usd` metric on every metered cloud route; local tiers
  report `0`.
- **Optional off-by-default cost-sync.** A `[router] cost_sync = true` toggle fetches
  prices from the free, MIT-licensed LiteLLM pricing JSON (cached at
  `~/.cache/anvil-serving/prices.json`, 24 h TTL, stdlib `urllib` only). Static config
  is the default; sync is opt-in. Falls back to static config on any fetch failure.
- **Configurable `exhaustion_status`.** The HTTP status anvil returns when all local tiers
  are exhausted is configurable (default 503) so operators can tune the gateway-failover
  trigger to their gateway's classification.
- **`POST /v1/route` — the routing-brain endpoint.** Exposes the intent-resolve + routing
  decision without serving the request. Request: a `completions`-shaped body plus optional
  `signals` (`work_class`, `token_estimate`, `urgency`). Response:
  `{ tier, model, provider, work_class, reason, confidence, session_id }`. Status 200
  (decision, even if `cloud`), 400 (malformed), 503 (no suitable tier). Used by the
  OpenClaw plugin for upfront routing splits.
- **OpenClaw plugin upfront routing split.** The `before_model_resolve` hook in
  `plugins/openclaw-anvil-intent-router/` now routes `deny`-class and cloud-destined
  work directly to the gateway's native provider (bypassing anvil entirely), and routes
  `allow` / `allow-with-verify` classes through anvil. Uses the shared
  `tier0_keywords.json` classifier vocabulary; optionally calls `/v1/route` for the
  authoritative decision.
- **Tool-call passthrough + live structured verifiers (#42, #52).** `tool_calls` / `tool_use`
  and the real `finish_reason` / `stop_reason` now flow through the backends, dialects, and
  verifiers (streaming and non-streaming) — a coding harness's tool-calling turn is preserved
  end-to-end, and the `NotTruncated` / `ToolCallJSONValid` verifiers run live on the serve path
  (previously inert). The text path is byte-identical.

### Fixed

- **Fallback-path hardening (#45, #52).** Seam isolation (a hung verifier is bounded by a
  latency budget; a raising observer/log or response-view factory can no longer crash a served
  request), 32 MiB drain byte-caps (local + cloud) against runaway responses, and a
  **session-scoped, thread-safe circuit breaker with cooldown + half-open decay** so a transient
  blip can't permanently disable a tier.
- **Front-door HTTP polish (#53).** A `GET` to a POST-only route returns `405` + `Allow: POST`
  (not `404`); a bounded non-blocking drain after a `413` avoids a connection-reset race;
  `do_GET` body-handling keeps the socket in sync.
- **Concurrency + correctness hygiene (#47).** `DecisionLog` is guarded by a lock (it is written
  from `ThreadingHTTPServer` request threads); a structurally-malformed cloud response now
  surfaces a sanitized error instead of being masked as an empty completion.
- **`benchmark` context-clamp + `--no-thinking` (#78).** Right-sizes the replayed request
  distribution and avoids thinking-budget starvation during benchmarks.

## [0.3.0] - 2026-06-30

First public release. anvil-serving is now a **quality-gated local-model router for coding
harnesses**: point a harness (Claude Code via `ANTHROPIC_BASE_URL`, or any OpenAI/Anthropic
client) at one endpoint; per request it resolves an **intent** to a **tier** (fast-local /
heavy-local / cloud), cheaply **verifies** the output, and **falls back** up the tier chain on
failure — never silently shipping a local-quality miss. stdlib-only, Python >= 3.11.

The `harness-router` PRD (all 18 tasks, milestones M0–M3) landed in this release.

### Added

- **Protocol-standard front door** — accepts both the Anthropic Messages and OpenAI Chat
  Completions dialects on one endpoint, including SSE streaming, and normalizes them onto a
  single internal request shape.
- **Intent routing** — named-preset intents (`planning`, `quick-edit`, `review`, `chat`,
  `long-context`) carried in the `model` field, accepted bare or `anvil/`-namespaced, resolving
  to `(model, tier, params)`; a `model:`-pin escape hatch for repro/debugging.
- **Tier-0 work-class classifier** — the universal floor: infers a work-class from the raw
  payload (token count, `thinking` flag, tool types, image content, system-prompt fingerprint)
  for requests that arrive with no declared intent. Vocabulary ships as the `tier0_keywords.json`
  package-data.
- **`/v1/models` discovery** — advertises the preset vocabulary so intents surface in harness
  model pickers.
- **Tier-topology config schema** — TOML config declaring tiers, per-tier backends, presets, and
  a `mapping_version`; loaded with stdlib `tomllib`.
- **Quality profile + residency-aware routing policy** — a `(model, work-class) ->
  {quality_score, sample_n, last_measured, decision}` table (`allow` / `allow-with-verify` /
  `deny`) keyed on a serve fingerprint (model + quant + engine + serve flags); policy filters by
  hard constraints (including privacy / local-only residency) then ranks the survivors.
- **Cloud-tier credentials on the Backend seam** — Anthropic and OpenAI cloud backends with
  credentials referenced by env-var name, plus **secrets redaction** so keys never reach logs or
  the decision record.
- **Cheap structural verify** — near-zero-cost inline checks (empty/truncated content, tool-call
  JSON that does not validate, code that does not parse, a diff that does not apply).
- **Streaming commit-window + verify-gated fallback + decision log** — for fail-prone classes on
  the streaming path, a non-streamed commit window buffers and verifies before the first byte
  reaches the harness; on verify-fail / error / timeout / low-confidence the router retries up the
  tier chain (fast → heavy → cloud) with retry caps and a per-session cost budget; every decision
  is logged transparently (the response reports the *real* tier that served).
- **Typed extension seams** — Backend / verifier / policy extension points for adding tiers,
  engines, and checks without forking the core.
- **`anvil-serving serve --config ...` CLI** — starts the front door bound to the tiers declared
  in a router config; binds `127.0.0.1` by default.
- **Profile bootstrap + async calibration + traffic metrics + per-work-class promotion** —
  bootstrap the quality table from the generalized shadow-eval, opt-in async calibration with
  serve-fingerprint staleness, real-traffic metrics, and a per-work-class promotion decision
  (planning/critic stay cloud-default, failover-only).
- **OpenClaw tooling + reference adapter** — validate-first tooling (wire-form + firing-cadence
  validator, logging hook, fixture) and a thin, swappable `before_model_resolve` reference adapter
  plugin. The core stays zero-OpenClaw-coupling.

### Known limitations

- **OpenClaw live validation is manual.** Validating the integration against a real OpenClaw
  install (firing cadence and outbound wire `model` form) requires a human on the gateway box; see
  [`examples/openclaw/README.md`](https://github.com/fakoli/anvil-serving/blob/main/examples/openclaw/README.md). The committed `hook-fire-log.jsonl`
  is a representative fixture, not a live capture.
- **Most promotion verdicts are seed/expected.** Per-work-class promotion decisions in the
  shipped profile are hand-seeded and pending real-traffic calibration; only `planning` rests on
  hard eval data (in the companion notes repo `fakoli/anvil-serving-notes`).
- **The T017 traffic fixture is synthetic.** Traffic-metrics behavior is exercised against a
  synthetic fixture, not yet against real routed production traffic.

[Unreleased]: https://github.com/fakoli/anvil-serving/compare/v0.13.2...HEAD
[0.13.2]: https://github.com/fakoli/anvil-serving/compare/v0.13.1...v0.13.2
[0.13.1]: https://github.com/fakoli/anvil-serving/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/fakoli/anvil-serving/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/fakoli/anvil-serving/compare/v0.10.0...v0.12.0
[0.10.0]: https://github.com/fakoli/anvil-serving/compare/v0.7.3...v0.10.0
[0.7.3]: https://github.com/fakoli/anvil-serving/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/fakoli/anvil-serving/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/fakoli/anvil-serving/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/fakoli/anvil-serving/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/fakoli/anvil-serving/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/fakoli/anvil-serving/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/fakoli/anvil-serving/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/fakoli/anvil-serving/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/fakoli/anvil-serving/releases/tag/v0.3.0
