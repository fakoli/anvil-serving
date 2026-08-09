# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.31.1] - 2026-08-08

### Fixed

- The `serves promote`/`serves mode enter` preflight gate (#387, feature 12)
  aborted on ANY `error`-severity `lint`/`rollback-check` finding across the
  *whole* manifest set, not just the transaction's own serves. Live REPRO on
  a fresh `anvil-serving init` home: `serves promote any-plan --dry-run`
  exited `3` over `missing-registry` findings on two untouched scaffold
  entries plus a `rollback-image-missing` finding on the scaffold's own plan
  — refusing every promotion over defects the transaction never touches,
  exactly what feature 5's revision in `docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md`
  rejects. The gate is now scoped: `_preflight_gate` takes an `involved` set
  of serve names (see `_finding_is_relevant`'s check → relevance table), and
  only a relevant `error` finding blocks. An irrelevant `error` finding
  prints to stderr with an `ADVISORY (outside this transaction):` marker
  instead of aborting. `warning`/`info` findings remain always-advisory.
- An unknown/ambiguous `promote` plan name previously still ran the gate over
  the whole manifest set and could abort with exit `3` on an unrelated
  finding before `promote` ever reported the "must match exactly one
  `[[promotion]]` plan" refusal for the typo itself. `promote`'s dispatch now
  resolves the plan first; on zero or multiple matches the gate is skipped
  entirely and falls through to that refusal (exit `1`).

### Changed

- A `promotion-topology` finding on the plan actually being promoted now
  aborts `promote` with exit `3` at the gate (it is inside the transaction's
  own blast radius) instead of only surfacing once `cmd_promote` re-validates
  topology itself.

## [0.31.0] - 2026-08-08

### Added

- `serves promote` and `serves mode enter` now run the existing `serves lint`
  + `serves rollback-check` reports before their first mutation — including
  `--dry-run` and `promote --rollback`/`--resume`, since both checks are
  read-only. Serve checks cover the whole manifest set for both; the promotion
  plans checked are the named manifest's for `promote` (the same plans the
  transaction executes) and the full set's for `mode enter`. Any
  `error`-severity finding aborts the transaction with exit `3` before a
  container is touched, printing findings to stderr in the same format the
  standalone commands use (stderr so a `--json` caller sees them in the error
  envelope). `mode enter` now also loads promotions itself (a first load
  previously absent from that dispatch path) and forwards its own
  `--restore-group` into the rollback-check; the gate applies to `mode enter`
  only, not `leave`/`preview`/`status`. The new `--skip-preflight-checks` flag
  overrides, printing an unmistakable warning to stderr; passing it to any
  `mode` action but `enter` is a usage error (exit `2`). The standalone
  `serves lint`/`serves rollback-check` commands are unchanged. Closes the gap
  where a `missing-registry` or `rollback-image-missing` defect a lint run
  would have caught was instead discovered mid-promotion or
  mid-mode-transition. Feature 12 of the divergence program
  (`docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md`), specified in issue #377.

## [0.30.0] - 2026-08-08

### Added

- `fleet drift` — compares each declared host's repository snapshot
  (`PATH/hosts/<host>/operator-home/`) against that host's *live* operator
  home, file by file, content-based (sha256, newline-normalized). The LOCAL
  host is read directly (`--home`, else `$ANVIL_SERVING_HOME`, else the
  platform default config home); every other host is probed with one
  `ssh -n -o BatchMode=yes <host> python3 -c "..."` call per host that hashes
  exactly the repo-tracked filenames it is given. Only repo-tracked files
  are ever compared or read — a live-only `.env`, backup, or lock file is
  never listed, opened, or sent over SSH. Reports `identical` / `differs` /
  `missing-live` per file; `--json` for tooling. Exits non-zero iff any
  compared file differs or is missing on a *reachable* host; an unreachable
  host reports that state, not drift (same availability-class reasoning as
  `fleet version`). `--repo` is required — the tool never guesses the
  private operator repository root. Feature 7 of
  `docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md`, closing the incident where a live
  operator home was found six commits behind its repo while serving
  production, and a second host's live home was a wholesale byte-copy of the
  wrong host's home. See ADR-0034 §9.

## [0.29.0] - 2026-08-08

### Added

- `fleet version` — cross-host `anvil-serving` version skew. Without `--host`,
  hosts are derived from the operator topology (every declared host except the
  local one); `ssh -n -o BatchMode=yes <host> anvil-serving --version` probes
  each remote host with a per-host timeout. Reports `ok` / `unreachable` /
  `not-installed` / `timeout` per host plus skew against the local version;
  `--json` for tooling. Exits non-zero on skew or a reachable host missing the
  CLI; an unreachable-only fleet exits `0` (a sleeping host is an availability
  gap, not proof of divergent code) and a fleet with no remote hosts declared
  never exits non-zero. Feature 8 of `docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md`,
  closing the incident where a fleet host two minors behind the operator host
  resolved transports differently and produced an error naming the wrong
  cause. First verb of the new `fleet` command family.

## [0.28.0] - 2026-08-08

### Added

- `serves up-for ALIAS` — resolves the alias -> tier -> serve chain
  (`[router.model_routes]` alias to tier id, then the `[[serve]]` whose
  `router_tier` matches it) and prints the resolution: tier, serve name,
  container, port, exact `up` argv, and manifest provenance. `--confirm`
  delegates to `serves up` for the resolved serve; `--dry-run` forwards.
  Unknown alias exits 2 listing configured aliases; a tier with no backing
  serve exits 1. A tier backed by more than one serve (a promoted primary
  sharing `router_tier` with its rollback) is refused rather than
  auto-selected — starting the wrong serve on a shared port is worse than
  asking the operator to pick with `serves up NAME`. `--json` for tooling.
  Feature 11 of `docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md`, derived from the
  solo-GPU-owner persona in `docs/PRODUCT-DISCOVERY-PERSONAS.md` §1/§2.

## [0.27.0] - 2026-08-08

### Added

- `serves rollback-check` — proves every declared rollback is actually usable,
  read-only. For each promotion plan, validates its rollback topology; for
  every routed exclusive serve, loads its `rollback_router_config` and reports
  a finding if it fails to parse; and, for the serves a rollback depends on
  (each plan's `rollback` serve, plus every serve in an optional
  `--restore-group`), confirms the compose image is present locally via
  `docker image inspect`. `--json` for tooling; exits non-zero on any error
  finding. Feature 4 of `docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md`, closing two
  live incidents: a promotion plan's `rollback_router_config` referencing a
  file that did not exist, and a restore-group serve's compose image pinned to
  a nightly tag evicted from Docker Hub.

## [0.26.0] - 2026-08-08

### Added

- `router fleet-status` — answers "is every configured capability actually
  served, and where". Reads the router configuration and probes every declared
  alias, purpose model, and audio route, exiting non-zero when a declared alias
  has no reachable backing serve. Read-only; `--json` for tooling. Feature 3 of
  `docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md`, closing the incident where the
  router advertised three routes whose backing serves had been off for hours
  with no signal anywhere.

  Two deliberate behaviours: an endpoint answering `401` counts as reachable
  (something is serving and asking for a token), and `host.docker.internal` is
  translated to the host-relative `127.0.0.1` when probing, because the router
  runs in a container and that alias does not resolve on the host — probing it
  verbatim reported a healthy primary as unreachable. The translation is
  reported in the detail column and the declared host stays visible, so it is
  never silent. `localhost` is never substituted.

## [0.25.0] - 2026-08-08

### Added

- `serves lint` — static analysis over the loaded manifest set, with no Docker
  or network access, exiting non-zero on errors so it can gate CI or a
  pre-promotion check. Three checks, each added because the defect it finds
  occurred live while every other command reported success:
  `duplicate-serve-name`, `missing-registry`, and
  `worktree-anchored-registry`. `--json` emits the same report structurally.
  Feature 1 of the program in `docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md`.

### Changed

- **BREAKING:** `load_manifest_set` now refuses two surviving entries that
  share a `name`, naming both files and their containers. De-dup remains BY
  CONTAINER, so the supported read-only mirror pattern is unaffected — only a
  duplicate name surviving that de-dup is rejected, because name selection
  matches both and one silently wins. That shadowing caused two live incidents
  in six days, the second leaving a promoted serve unmanageable because
  `serves down` resolved the wrong container. `serves lint` loads leniently and
  still reports the defect, so the command an operator reaches for when blocked
  is not the one that breaks. Feature 2 of the same program.

## [0.24.0] - 2026-08-08

### Changed

- **BREAKING:** every `[[serve]]` entry must now declare `runtime`, either
  `"docker"` or `"native"` (ADR-0034 §7). There is deliberately no default — an
  implicit container assumption is what made a heterogeneous fleet
  inexpressible, and defaulting would preserve that defect under a new name.
  `container` is required when `runtime = "docker"` and rejected when
  `runtime = "native"`. Existing manifests must add the field; all in-repo
  manifests, examples, scaffold templates, and the `deploy` generator were
  migrated.
- `runtime = "native"` is accepted by the schema but rejected at manifest load
  with `NativeRuntimeNotSupported` until the native lifecycle exists. Roughly 85
  code sites resolve `serve["container"]`, so loading a native entry today would
  surface as a `KeyError` inside an unrelated command. Failing at load keeps the
  schema honest and the failure legible; removing the guard is the last step of
  implementing native serving.

### Added

- ADR-0034 records the fleet control-plane architecture: the operator runs on
  the gateway host and is never in the inference path; the existing controller
  becomes the per-node agent with a role-scoped operation catalog; multi-host
  dispatch is typed controller RPC with no remote Docker socket and SSH as
  recovery only. Node capability is declared along three orthogonal axes —
  runtime class (`docker`/`native`), memory model (`discrete`/`unified`), and
  availability class (`continuous`/`opportunistic`) — from which promotion
  eligibility is *derived* rather than declared, so a non-reproducible host
  cannot back a published claim. Fires the fleet trigger deferred in ADR-0033
  §3.4 and amends its supervision row for the native runtime. Serve entries will
  require an explicit `runtime` field; this is a deliberate breaking change.

## [0.23.1] - 2026-08-08

### Fixed

- `load_promotions` now rejects a `[[promotion]]` entry whose `router_config`
  or `rollback_router_config` resolves to a file that does not exist, naming
  the promotion and the resolved absolute path. Previously only `[[serve]]`
  router profiles were existence-checked, so a promotion plan that named a
  missing rollback profile loaded cleanly and every read-only surface
  (`serves status`, `serves mode status`, `serves mode preview`) reported a
  healthy manifest. The absence surfaced only once a promotion or switch was
  already under way — the moment a working rollback matters most. See
  `.tickets/2026-08-08-promotion-router-profiles-not-existence-validated.md`.

## [0.23.0] - 2026-08-08

### Added

- The controller's host publish port is now overridable via a single `ANVIL_CONTROLLER_PUBLISH` spec (`<host-ip>:<host-port>:8765`, default `127.0.0.1:8765`), so an operator can move the loopback publish off a host port already claimed by another service while keeping the container-internal `8765` canonical. Documented in `examples/fakoli-dark/.env.example`, the operator playbook, and the hardened-controller test. Tailscale Serve must be pointed at the chosen host port, and any prior route on the old host port should be removed.

- `GET /v1/models/capabilities` extends the per-tier `compat` declarations with
  two more fields that use the exact OpenClaw keys: `supportsStrictMode` (a bool)
  and `supportedReasoningEfforts` (an ordered set of lowercase effort labels such
  as `["low", "high", "max"]`). Both are strictly allowlisted per tier and feed
  the canonical config hash. This lets an operator author an accurate OpenClaw
  `compat` block (strict-mode structured output and supported reasoning efforts)
  instead of guessing. OpenClaw treats the effort list as a membership set (order
  is cosmetic) and does not auto-read this endpoint.

- Read-only `host config inventory` and `host config export` commands, with
  matching MCP/controller tools, now provide metadata-only operator-home
  discovery and bounded export of allowlisted Anvil configuration plus a
  sanitized Anvil-owned OpenClaw fragment. Explicit path selection closes
  supported direct dependencies without requiring a whole-home export.

### Removed

- Production-cleanup dead-code sweep, verified against the command registry,
  MCP tool dispatch, entry points, and dynamic-dispatch sites before removal:
  the unused `cli._hidden_leaf_help_flags`, `config.load`,
  `external_benchmarks.store.record_verdict`,
  `observability.benchmark.overhead.process_cpu_percent`,
  `serves._compose_project_from_up`, and `voice.stages.llm._split_speakable_text`
  helpers; the never-constructed `SessionCreated`/`SessionUpdated`/
  `ResponseCreated` realtime event dataclasses (those wire events are emitted
  as raw dicts by the realtime service); and the never-reachable
  `commands.spec._future_handler`/`_deferred_handler` placeholder path
  (`_resource_node` now requires a concrete handler module).
- Top-level `assets/` duplicates of `docs/assets/` (byte-identical PNGs);
  README and the tailnet runbook now reference `docs/assets/`. Session-retro
  artifacts under `post-session-findings/` moved to the private operator
  repository per ADR-0032.
- Repo-wide over-engineering audit cuts, each verified caller-free before
  removal: the superseded `RealtimeProxyState`/`RealtimeProxyLogs`/
  `RealtimeProxyService` lifecycle family in `voice.realtime.service` (the
  live path is `RealtimeProxyProcessService`); the never-enabled background
  availability prober and its `availability_prober`/
  `availability_probe_backoff_max`/`availability_probe_staleness` router
  config keys (the inline single-flight prober is the only implementation);
  the `anvil_serving.command_tree` compatibility module (zero importers); the
  zero-caller re-exports in the `controller`, `mcp`, and `benchmark` facades,
  which now declare exactly their supported surfaces; unused command
  option/doc-path constants, `InternalRequest.last_user_text`,
  `AttemptRecord.detail`, `est_tokens`, the unimplemented `independent_judge`
  validator strength, the always-`None` voice `llm_latency_ms` metric, and
  `cmd_switch`'s dead `resume` parameter; and the dormant `profile`/
  `calibrate` legacy vocabulary in the CLI-reference audit.

### Changed

- Consolidated duplicated helpers onto single canonical homes: the
  no-proxy/no-redirect `urllib` opener now lives only in `transports`
  (collectors and the MCP controller client import it), `resolve_api_key`
  lives only in `preflight` (re-exported unchanged from
  `benchmarking.requests`), and the voice STT/LLM/TTS stages share one
  `bearer_headers` helper in `voice.stages.base`. Public facade names and
  signatures are unchanged.
- Controller idempotency-key expiry tracking replaced its probabilistic
  bloom-filter tombstone generations with an exact SQLite `tombstones` table
  (same replay-block semantics, one `SELECT`/`DELETE` instead of bit
  arithmetic; the store is already bounded by `max_records`).
- Second round of DRY consolidations onto single canonical homes: shared
  `docker rm`/`docker ps`/missing-binary wrappers in `serves`; catalog backups
  reuse `guard.next_backup`; router auth-env validation, safe availability
  checks, media block scanning, and dialect tool-argument decoding each have
  one helper; control-plane private/tailnet IP classification and MCP
  mutation-gate logic are single-sourced; `preflight` imports the benchmarking
  artifact helpers and is the canonical `response_observation` home;
  frequency counts use `collections.Counter`; the voice preflight/findings
  script helpers moved to shared `scripts/voice/_preflight_common.py` and
  `_findings.py`; duplicated test fakes consolidated into `tests/conftest.py`
  and a new `tests/voice/conftest.py`.
- CI now runs the CLI-reference audit once per trigger in a dedicated
  `docs-audit` job instead of eight times across the test/build matrices.
- `.tickets/` is reorganized: 37 resolved tickets moved to `.tickets/closed/`,
  21 open tickets remain at the top level, and all references (docs, configs,
  `targets.py`, `START_HERE.md`) now point at the new locations.

### Fixed

- The canonical CLI now forwards the consumed `--confirm` flag into the legacy
  `serves mode enter` / `serves mode leave` handler argv via an explicit
  `HandlerRef.forward_confirm_flag` declaration, making the leaf argparse gate
  authoritative instead of relying on the dispatcher's thread-local
  confirmation scope (the `confirmation_authorized()` fallback remains as
  defense in depth). A spec validator rejects the declaration on handlers that
  do not define a `--confirm` option, so it cannot spread to
  `confirmation_scope`-style handlers. JSON mode still never prompts and still
  requires explicit confirmation.

### Security

- Operator configuration export fails closed for YAML, unknown or secret
  files, malformed secret references, credential-bearing values, capability
  URLs, symlinks/junctions, concurrent filesystem mutation, and dependencies
  outside the captured operator-home snapshot unless they are declared portable
  product registries recorded without reading. POSIX capture is anchored to a
  directory descriptor; Windows holds a non-reparse, no-delete-share root
  handle; export reuses the exact bytes captured by inventory.

## [0.22.0] - 2026-08-04

### Added

- The OpenAI streaming dialect now emits a trailing `usage` chunk when the caller
  requests `stream_options.include_usage: true`, fixing the missing streaming
  usage that broke OpenClaw-style context-window metering (#345). Real upstream
  counts are used when the relay surfaces them; estimates fall back exactly like
  the non-streaming path. The default (non-usage) streaming path remains fully
  lazy, so time-to-first-token and backpressure are unaffected.
- `GET /v1/models/capabilities` now exposes a `compat` block per tier, currently
  carrying `supportsUsageInStreaming` (an allowlisted bool declared under
  `tier.params.capabilities.compat`). This mirrors OpenClaw's provider-model
  `compat` shape so a client can discover that a tier emits streaming usage.

## [0.21.1] - 2026-08-02

### Fixed

- The Fakoli Dark controller deployment now mounts the target and rollback
  router profiles required by routed exclusive-mode manifests. This restores
  managed serve and mode inspection after 0.21.0 made router activation part
  of the mode transaction; rebuilding the 0.21.0 controller without these
  mounts would reject the otherwise valid DeepSeek Primary manifest.
- A controller Compose regression test now derives every required routed
  exclusive profile from the serve manifest, verifies the source file exists,
  and requires an exact read-only mount into the controller.

## [0.21.0] - 2026-08-02

Anvil Serving 0.21.0 is the DeepSeek V4 Flash 0731 long-context Primary and
model-specific output-safety release. It promotes the locally stable 650K
profile for one Pi/OpenClaw coding user, retains the failed 1M client evidence,
and includes the post-0.20 WSL2 native KV-offload lifecycle work.

### Highlights

- **Human-gated 650K DeepSeek Primary.** The exact official 0731 revision on
  the pinned r16 B12X/DSpark K5 image now backs `llm.primary` in exclusive TP=2
  mode with 650,000 context tokens, 16 admitted sequences, 4,096-token
  batching, high reasoning by default in the verified clients, and an explicit
  Qwen rollback alias.
- **Real-client qualification, not a synthetic-only promotion.** Pi on Fakoli
  Dark, Pi on Fakoli Mini, and OpenClaw on Fakoli Mini passed live routed
  smokes. The published narrative follows the complete 128K-to-650K-to-1M
  experiment and explains why bounded 1M success did not survive actual coding
  agent request shapes.
- **Per-model output protection.** Router tiers may declare an optional
  positive `max_output_tokens`. Oversized OpenAI Chat, Responses, and Anthropic
  Messages requests are clamped before admission and relay, with standard and
  `X-Anvil-*` warning headers instead of a hidden mutation or hard rejection.
- **Managed WSL2 native KV offload.** The release includes the exact mmap-only
  pinning translation, 128K replay and 256K capacity evidence, counter-backed
  CPU-to-GPU reload, and ownership-aware shared-memory inspection and reclaim
  surfaces merged after 0.20.0.

### Changed

- Exclusive-mode entry now treats router activation as part of the transaction:
  routed targets declare complete current/rollback profiles, install the target
  profile only after serve readiness, and pass guarded tier readmission before
  success. Profile or readmission failure restores the prior profile and split
  stack; leave performs the reverse quiesce, drain, profile, and readmit flow.
- The reference dual-GPU manifest records separate 650K current and retained
  1M experimental DeepSeek recipes. Exclusive ownership blocks every other
  managed GPU workload while the Primary is running.
- Router metadata and effective-config fingerprints advertise the configured
  output ceiling. Tiers without the field retain their previous behavior.
- Package metadata now uses the SPDX `MIT` expression and explicit license-file
  declaration supported by the pinned setuptools build floor, removing the
  deprecated license table and classifier from release builds.
- Pi on both hosts and OpenClaw on Mini use `llm.primary` with 650K context,
  32,768 maximum output tokens, and high reasoning as the default.
- Benchmark publication now records the current/rollback chain, exact client
  smokes, output-clamp proof, and both fatal 1M workspace failures across the
  finding, run catalog, model dossier, hardware page, comparison, and archive.

### Evidence and caveats

- The 650K profile recovered a needle near 640K and measured 141.6 tok/s median
  decode in the matched 32K/c1 low-reasoning slice. High and max reasoning are
  functionally verified, but no high-reasoning throughput claim is made.
- The 1M/maxseq16 profile passed near-985K retrieval and bounded protocol gates,
  then fatally exceeded its 514.25 MiB locked B12X workspace on two real client
  shapes. The second failure used a 19,118-token prompt and only 5,120 requested
  output tokens, proving that output clamping alone is not a sufficient 1M fix.
- Moving display output to the AMD iGPU enabled the larger graph envelope, but
  the operator explicitly waived the normal 3 GiB per-device reserve for this
  single-user exclusive deployment. The final reservation ledger reported only
  94 MiB free after that reserve; this is not a co-residency claim.
- The pinned custom runtime and recipes remain specific to the recorded model
  revision, image digest, two sm_120 Max-Q cards, WSL2 transport, quantization,
  TP size, and KV format. Requalification is required after changing any of
  those dimensions.

## [0.20.0] - 2026-08-01

Anvil Serving 0.20.0 is the DeepSeek V4 Flash 0731 DSpark qualification and
managed-experiment reliability release. It preserves the exact pinned SM120
runtime and recipe, publishes matched speculative-decoding evidence through
128K context, and fixes lifecycle and benchmark-evidence gaps found during the
unattended campaign. Production aliases remain unchanged.

### Highlights

- **Reproducible DeepSeek 0731 TP=2 serving.** The official checkpoint at its
  exact revision now has pinned r16 B12X DSpark-K5 and no-spec control recipes,
  with named cache volumes, offline nested speculative assets, reasoning
  controls, WSL2 translation, and preserved image/source identity.
- **Matched DSpark evidence.** On two RTX PRO 6000 Blackwell Max-Q cards,
  DSpark raised median successful 4K/c1 decode from 64.9 to 130.7 tok/s and
  request throughput by 70.5% versus the same-image no-spec control. The
  cumulative draft acceptance rate was 55.1%.
- **Long-context and quality qualification.** Low, high, and max reasoning,
  tools, session behavior, and 27/27 coding/intelligence checks passed. Cold
  and warm 32K, 64K, and 128K ladders record memory, time to first output,
  visible TTFT, prefill, generation, and end-to-end timing.

### Added

- Managed recipe named-volume declarations and model-environment entrypoint
  support, including cache preparation for nested speculative checkpoints.
- A preserve-after-failure exclusive-mode option so failed targets retain the
  container and durable diagnostics without being confused with a successful
  transition.
- Dated DeepSeek 0731 findings, raw bounded evidence, exact source registry,
  model dossier, run catalog, measured hardware page, and community-facing
  deployment recipes.

### Changed

- Benchmark context ladders retain timing for every row instead of only the
  final request, and capacity failures preserve their actionable error text.
- Reasoning effort accepts the model's qualified `low`, `high`, and `max`
  levels across managed preflight, benchmark, CLI, and MCP surfaces.
- External benchmark ingestion no longer carries quantization state from one
  source row into an unrelated row.
- Foundation-only voice CLI tests now isolate their operator home so a live
  exclusive TP=2 owner cannot contaminate unit-test admission results.

### Evidence and caveats

- The 128K profile is functional but used roughly 95-96 GiB on each 96 GiB GPU
  and failed the required 3 GiB-per-device reserve. This release records a
  qualified experimental candidate, not a production promotion.
- The local 130.7 tok/s DSpark result does not reproduce externally reported
  230-250 tok/s figures from different 600 W hardware and software conditions.
- 256K was attempted but not qualified. The supported published ceiling for
  this exact recipe is 131,072 tokens, with 128K request evidence.
- The runtime is a pinned custom derivative. Reusing the recipe with another
  vLLM, CUDA, FlashInfer, GPU product, quantization, or TP size requires fresh
  qualification.

## [0.19.0] - 2026-08-01

Anvil Serving 0.19.0 is the symmetric dual-GPU and TP=2 qualification
release. It replaces the former mixed RTX PRO 6000/RTX 5090 topology with two
equal RTX PRO 6000 compute roles, adds an exclusive tensor-parallel operating
mode, and publishes reproducible five-model benchmark evidence without
changing production aliases.

### Highlights

- **Explicit dual-GPU operating modes.** Split mode and exclusive TP=2 mode
  now have declared ownership, admission, rollback, and status contracts. A
  TP=2 serve owns both GPU roles while every competing inference workload is
  offline; leaving the mode restores the saved split group transactionally.
- **Five fresh TP=2 qualifications.** Qwen3.5 122B, Nemotron 3 Super 120B,
  Laguna S 2.1, DeepSeek V4 Flash 0731, and Inkling Small were measured on the
  same two-card host with pinned revisions, engines, quantization, context,
  functional gates, capacity data, repeated quality checks, and retained
  failures. Every result remains `no-promotion`.
- **Portable managed recipes and public evidence.** Exact-revision cache
  verification, offline serving, portable container-relative GPU selection,
  reasoning-aware timing, model dossiers, hardware-first comparisons, and raw
  artifacts make the campaign reproducible without publishing host-private
  topology values.

### Added

- A machine-readable TP=2 recipe registry for all five candidates, including
  pinned derived SGLang images for the WSL2-safe DeepSeek and Inkling paths.
- `capacity-v4-reasoning`, which records time to first reasoning-or-visible
  output separately from first-visible TTFT and measures generation from the
  correct reasoning boundary.
- Exact cache-completeness preflight before GPU allocation, plus enforced
  Hugging Face and Transformers offline mode after verification.
- Hardware-first Inkling Small and DeepSeek V4 Flash dossiers, a current
  source registry, dated narrative findings, and bounded raw evidence for the
  full campaign.

### Changed

- Model recipe loading now fails fast when an owned container exits before
  readiness and supports recipe-specific startup timeouts.
- Quality context budgeting now preserves both visible-answer and reasoning
  headroom, while control-evidence references remain portable in published
  artifacts.
- Exclusive-mode release uses a bounded force-remove path so an unresponsive
  Docker Desktop stop cannot trap the transaction. Split restoration skips
  readmission only when the default router is intentionally stopped; live or
  explicitly addressed routers still require a successful transition.
- Inkling's pinned runtime adds its required ModelOpt dependency, forwards the
  exact checkpoint revision through cache/config lookup, and uses narrowly
  gated SM120 compatibility fallbacks for grouped GEMM and activation kernels.
  These are compatibility fixes, not claimed kernel speedups.

### Evidence and caveats

- DeepSeek completed 11/12 final capacity requests; one request exhausted its
  2,048-token allowance entirely in reasoning without a visible answer.
- Inkling's low-reasoning functional, capacity, and repeated quality gates
  passed, but the extended Responses subset still exposed internal reasoning
  with `reasoning_effort=none` and remains a published failure.
- No genuine NVFP8-labeled DeepSeek or Inkling artifact was found in the
  current catalogs. The release records the exact publisher quantizations
  tested instead of relabeling them.
- The campaign establishes compatibility and bounded performance for each
  exact TP=2 recipe. It is not a topology-only speed A/B and authorizes no
  model promotion.

## [0.18.0] - 2026-07-30

Anvil Serving 0.18.0 is the remote-controller and MCP interoperability
release. It lets a model-free Fakoli Mini operate Fakoli Dark through a
restricted controller, while a dual-era TypeScript bridge keeps current
OpenClaw clients compatible with the modern MCP contract on Dark.

### Highlights

- **Containerized remote control on Dark.** The controller runs as a dedicated
  non-root Linux container with explicit Docker-socket, GPU, configuration,
  state, and Tailscale Serve boundaries.
- **Dual-era MCP on Mini.** The bundled TypeScript bridge accepts both
  initialize-based MCP through `2025-11-25` and stateless MCP `2026-07-28`,
  translating both onto a modern-only authenticated Dark endpoint.
- **Repeatable OpenClaw operation.** Typed CLI and MCP operations, credential
  references, dry-run shutdown, GPU inventory, deployment documentation, and
  live validation boundaries make the Mini-to-Dark topology reproducible.

### Changed

- The Fakoli Mini stdio bridge now bundles the official TypeScript MCP SDK
  `2.0.0` and serves both the initialize-based MCP era through `2025-11-25`
  and stateless MCP `2026-07-28`. Its authenticated downstream connection is
  pinned to `2026-07-28`, so the Dark controller remains modern-only.
- Mini-to-Dark typed CLI operation now accepts declared credential environment
  references, supports `serves down --dry-run`, permits container-backed GPU
  inventory, and includes a deployment runbook plus complete live validation
  boundaries.
- Fakoli Dark can run the restricted controller as a dedicated non-root Linux
  image with Docker-socket and GPU visibility, explicit configuration mounts,
  container-to-host loopback rewriting, durable operation state, and a
  loopback-only Compose deployment for host-owned Tailscale Serve. Fakoli Mini
  can connect through the Anvil stdio bridge. OpenClaw `2026.7.1-2` and its
  bundled MCP SDK `1.29.0` are supported through the bridge's legacy-facing
  side without adding a legacy endpoint to Dark.
- Docs site theme switched from ReadTheDocs to Material for MkDocs (nav tabs,
  dark mode, native wide-table scrolling; `docs/assets/tables.css` removed).
- The Evidence nav section is now **Benchmarks**, restructured visitor-first:
  comparison table as the entry point, the GPT-OSS Puzzle operator recipe filed
  under its model dossier, and the mention audit, compatibility recipe index,
  and chronological result archive grouped under an Archive subsection.
- Published docs now use the generic tailnet placeholder `100.64.0.10`
  everywhere; the real tailnet address was redacted from all published pages
  and raw evidence files (see the redaction note in `docs/findings/README.md`
  and the new "Published-docs topology policy" in `AGENTS.md`).

## [0.17.0] - 2026-07-29

Anvil Serving 0.17.0 is the Agents-A1 multimodal qualification and Primary
promotion release. It adds reproducible image and video benchmarking, promotes
the pinned official FP8 checkpoint at a 262,144-token context window, and keeps
Qwen3.5 122B NVFP4 as the immediate managed rollback.

### Highlights

- **Agents-A1 FP8 Primary.** The reference Primary is
  `InternScience/Agents-A1-FP8` at its pinned revision, served with FP8 KV,
  multimodal image/video support, thinking hard-disabled by the router, and a
  262,144-token context window.
- **Measured Qwen head-to-head.** The production-shaped comparison records
  exact runtime identity, protocol-v3 quality, long-context retrieval, TTFT,
  effective prefill, decode, memory/KV allocation, image/video behavior, and
  routed promotion gates. Qwen3.5 remains a first-class rollback rather than
  being discarded.
- **Reusable qualification system.** Versioned multimodal corpora and evidence,
  cache inventory and guarded cleanup, managed recipe lifecycle, kernel-tuning
  contracts, and hardware-first benchmark publication are now reusable product
  surfaces instead of campaign-only procedures.

### Changed

- Promoted Agents-A1 official FP8 to the guarded `llm.primary` route after the
  262K protocol-v3 and routed preflight gates passed. The matched comparison
  measured 35.31 GiB model memory and 51.93 GiB KV allocation for Agents-A1,
  versus 73.22 GiB and 13.84 GiB for Qwen3.5; Agents-A1 also delivered the
  video corpus on the exact qualified runtime.
- Preserved `nvidia/Qwen3.5-122B-A10B-NVFP4` as the immediate managed rollback.
  Promotion remains explicit and reversible through the guarded recipe
  transaction.
- Kept the generated RTX PRO 6000 FP8 MoE tune rejected and inactive: its
  matched A/B regressed primary-lane throughput by 1.399%. Tune artifacts now
  carry exact engine, GPU, dtype, tensor-parallel, and model-geometry identity.
- **Documentation portal is organized by capability.** The mkdocs navigation now
  leads with what anvil-serving does today — serve models, qualify and benchmark,
  route through the gateway, voice and audio, operate the host, integrate a
  harness — with dated benchmarks and findings kept as a separate evidence layer.
  This adopts 15 pages that existed but were unreachable from the nav, including
  `docs/WORKBENCH.md` (the documented home of six `workbench` commands),
  `docs/TAILNET-ENDPOINT-RUNBOOK.md` (the only prose for the `edge` commands),
  and ADRs 0016–0026, 0028, and 0029. `docs/index.md` was rewritten as a
  capability map.
- **Evergreen docs no longer read as single-model documents.** `docs/VOICE.md`
  describes the `stt`/`tts` audio purposes and routed serves rather than naming
  specific STT/TTS models as architecture nouns, and `docs/TROUBLESHOOTING.md`
  states reasoning-control guidance as behavior classes (template-controlled
  versus budget-controlled) instead of a two-vendor taxonomy. Current model
  occupants remain documented in the benchmark portal and model dossiers.
- `docs/SERVES-AND-EVAL.md` merged into `docs/OPERATOR-PLAYBOOKS.md`; both
  described the same serve → preflight → publish walkthrough.
- Dated campaign snapshots in `docs/BENCHMARKS.md` are labelled as such and defer
  current routing state to the benchmark portal.

### Added

- `eval benchmark multimodal` and bounded image/video preflight support with
  versioned manifests, media hashes, deterministic assertions, latency and
  token telemetry, and raw evidence suitable for independent review.
- Video-safe router relay and admission behavior, including preservation of
  OpenAI `video_url` blocks, fail-closed unsupported translations, and bounded
  image/video capacity accounting.
- Read-only model-cache inventory plus guarded exact-revision cleanup evidence,
  including filesystem, Hugging Face snapshot, incomplete download, Docker
  image, container-reference, and last-observed metadata.
- Managed recipe status, bounded logs, and unload operations so model lifecycle
  and failure investigation remain within the Anvil command surface.
- Reusable LLM qualification, kernel-tuning, and benchmark-publication skills,
  with pinned evidence contracts and ticketed friction from the campaign.
- A deterministic image, mixed-media, and 10–120 second video corpus, including
  Creative Commons source provenance and normalized media hashes.
- **A master model comparison table** at `docs/benchmarks/comparison.md`: every
  measured configuration in one place, grouped by card, with quantization and KV
  dtype, served context and admission, reasoning mode, TTFT (carrying its depth
  and concurrency), output rate, and a link to the working recipe. Rate cells
  name their instrument — controlled long-generation decode, short-output
  aggregate capacity, or continuous-batching aggregate — because the repository's
  own methodology forbids comparing them to each other. Unmeasured metrics are
  marked rather than estimated, and configurations without a pinned revision are
  flagged as unable to ground a new equivalence claim.
- **Operator documentation for three capabilities that had none.**
  `docs/PURPOSE-MODELS.md` documents embeddings and reranking — the
  `[[router.purpose_models]]` field contract, why the surface routes by exact
  model name instead of by alias, and its qualification boundary.
  `docs/MODEL-PROMOTION.md` documents the guarded `serves promote` transaction,
  the distinct `router quiesce` / `drain` / `readmit` states, and what makes a
  rollback target real. `docs/MODEL-LIFECYCLE.md` documents the `models`
  family: catalog sync, artifact pull, the recipe registry, and cache
  reclamation.
- `scripts/audit_cli_references.py` now fails when a page referenced by a
  `docs_anchor` in `docs/CLI-COMMAND-MANIFEST.json` is not reachable from the
  mkdocs navigation. `mkdocs build --strict` reports an out-of-nav page only at
  INFO level, so documented command pages could previously fall out of the site
  while CI stayed green.

### Removed

- `docs/SYSTEM-OBSERVABILITY-DASHBOARD-MILESTONES.md`, a PRD execution plan
  pinned to the v0.11/v0.12 source line. The `dashboard` and `collectors`
  commands it described are documented in the CLI reference.

## [0.16.1] - 2026-07-28

### Fixed

- `router install-config --confirm` now succeeds when the restarted router
  exposes the exact configured tier set even if one or more model serves are
  currently unavailable. The command still reports per-tier readiness and
  lists unavailable tiers without conflating configuration installation with
  model readiness, preflight qualification, or promotion.

## [0.16.0] - 2026-07-28

Anvil Serving 0.16.0 is the multimodal primary and router observability
release. It promotes the pinned NVIDIA Qwen3.5 122B NVFP4 checkpoint on the
RTX PRO 6000, preserves explicit caller-controlled thinking, and adds
authenticated read-only operational endpoints for model and router inspection.

### Highlights

- **Multimodal Qwen3.5 primary.** The reference Primary serve now uses
  `nvidia/Qwen3.5-122B-A10B-NVFP4` at its pinned revision with a 262,144-token
  context window, one image per prompt, thinking enabled by default, and an
  explicit Laguna rollback configuration.
- **Authenticated observability API.** The router exposes model capacity,
  capabilities, fingerprints, router status, bounded decision-buffer
  statistics, request traces, and Prometheus metrics without adding runtime
  dependencies or inferred routing behavior.
- **Managed voice lifecycle.** Guarded `voice up` and `voice down` aggregate
  co-located STT, TTS, and realtime-proxy lifecycle operations while refusing
  split-host configurations.

### Changed

- Ruff linting now selects the repository's intended `E4`, `E7`, `E9`, and
  `F` rule families explicitly, so CI can track current Ruff releases without
  inheriting newly promoted default rules.
- Replaced the Laguna S 2.1 Primary recipe and direct route with the pinned
  NVIDIA Qwen3.5 122B NVFP4 recipe and published functional, multimodal,
  long-context, thinking, and quality evidence.

### Added

- Added `voice up` and `voice down` as guarded, co-located-only lifecycle
  aggregates. They operate managed STT/TTS and the managed realtime proxy in
  dependency order, return one combined result, and refuse split-host
  configurations.
- Added authenticated `GET /v1/models/capacity`,
  `/v1/models/capabilities`, `/v1/models/fingerprints`,
  `/v1/router/status`, `/v1/stats`, `/v1/requests/{request_id}`, and
  `/metrics` endpoints. Capacity responses combine declared model limits with
  bounded live vLLM gauges when available.

### Fixed

- Corrected the managed realtime-proxy reference manifest to reach STT/TTS
  through an audio-owned Docker network and service DNS on Windows and Linux.
- Made voice CLI unit coverage dry-run lifecycle plans instead of mutating
  real Docker containers during the test suite.
- Decoupled historical release-sweep evidence validation from the mutable
  current CLI manifest while preserving the recorded revision, digest, and
  internally consistent command counts.

## [0.15.0] - 2026-07-27

Anvil Serving 0.15.0 is the selectable Omni voice and lifecycle-cleanup
release. It consolidates the RTX 5090 auxiliary text and vision path around a
managed Nemotron 3 Nano Omni tier, adds a co-resident small-Omni voice option,
and makes stopped-container cleanup the safe default.

### Highlights

- **One managed auxiliary Omni tier.** Auxiliary LLM, general-vision, and OCR
  capabilities now share the pinned `omni-local` tier, with bounded image and
  OCR preflights and published qualification evidence.
- **Selectable Omni voice topology.** Operators can run the existing exclusive
  30B Omni recipe or a co-resident Qwen2.5-Omni-3B, Parakeet STT, and Kokoro TTS
  stack on the RTX 5090. The smaller model remains unpromoted.
- **Cleaner lifecycle boundaries.** Admission derives from the complete
  colocated manifest set, and `serves down` removes stopped containers by
  default while preserving an explicit post-mortem mode.

### Changed

- `serves down` now stops and removes selected containers by default so model
  experiments do not leave stale Docker configuration behind.
  `--keep-container` preserves the previous stop-only behavior and container
  logs when an operator needs post-mortem inspection or a cheap restart.
- Collapsed the RTX 5090 auxiliary LLM, general-vision, and OCR containers into
  one pinned, managed Nemotron 3 Nano Omni tier. `llm.voice`,
  `vision.general`, and `vision.ocr` now select `omni-local`; embeddings and
  reranking remain a separate mutually exclusive stack.
- Added bounded `image` and `ocr` preflight checks with independent expected
  text, input type/size validation, and content-addressed evidence for CLI and
  controller callers.
- Added an operator-selectable `omni-voice-stack`: pinned Qwen2.5-Omni-3B
  co-resident with the existing Parakeet STT and Kokoro TTS services on the RTX
  5090. The exclusive 30B Omni recipe remains available, and the smaller model
  is not router-promoted by this change.
- Serve, voice, controller, and ComfyUI lifecycle paths now derive admission
  from the complete colocated `serves*.toml` manifest set. Cross-stack GPU
  reservations can no longer be omitted simply by entering through a different
  lifecycle verb.

### Fixed

- The small-Omni vLLM image now includes pinned audio extras. The stock image
  accepted text/image/OCR but failed audio requests with the explicit
  `vllm[audio]` dependency error.
- The Gemma 3n E2B candidate now records its Hugging Face license-gate failure
  as an unverified recipe instead of treating the 403 as an authentication or
  container-start failure.
- `serves logs` no longer crashes on Windows when UTF-8 container output
  includes progress bars or box-drawing characters that the active console
  codec cannot represent. Unsupported glyphs are escaped while startup stderr
  remains visible.
- Authenticated model pulls that fail now preserve the exact container error
  and point operators to the matching Hugging Face repository when access
  denial requires browser-based terms acceptance.

### Breaking changes

- Removed the `auxiliary-local`, `ocr-local`, and `vision-local` reference-tier
  contracts and the `auxiliary`, `ocr`, and `vision` production serve names.
  Single-user deployments should replace them with `omni-local` and `omni`.

## [0.14.0] - 2026-07-27

Anvil Serving 0.14.0 is the production-readiness and modularization release.
Since 0.13.3, the supported operator path has been exercised end to end on the
reference two-GPU host, repaired where live testing exposed gaps, and organized
around explicit single-user lifecycle boundaries.

### Highlights

- **Production capability sweep completed.** Managed serving, router, recipe,
  benchmark, voice, OCR, reranker, embeddings, ComfyUI, and Workbench workflows
  were exercised through product commands. Agent-A1 and Laguna results, the
  voice round trip, purpose-model checks, and final runtime state are published
  in the dated release-readiness finding.
- **One stack per operational purpose.** Serving, auxiliary capabilities,
  voice audio, ComfyUI, and Workbench no longer share ambiguous Compose project
  ownership. Each stack can be operated independently, while the router remains
  owned by Anvil Serving.
- **A reproducible local Workbench path.** Operators can build the Workbench
  image locally, bring up its private stack, reuse the router authentication
  contract, and verify the complete Workbench-to-router-to-model path without
  publishing or pulling a project image from an external repository.
- **Stronger benchmark evidence.** Capacity runs now have deterministic context
  plans, authoritative token accounting, explicit `capacity-v2` measurement
  identity, bounded failure retention, and fail-closed validation. The benchmark
  implementation is split into focused modules behind its public facade.
- **Smaller, explicit control-plane modules.** Command registration, controller
  services, MCP foundations, and all MCP tool families are independently
  reviewable packages. Public compatibility facades and deterministic direct
  dispatch remain in place.

### Breaking changes

- **Generated hardware and live serving roles are now Primary/Auxiliary only.**
  `init` emits `PRIMARY_GPU_UUID`, `AUXILIARY_GPU_UUID`,
  `primary-local`, `auxiliary-local`, `primary`, and `auxiliary`; the removed
  Heavy/Fast CLI and environment spellings are not accepted as aliases.

- **The command manifest is now schema v4.** Command declarations moved from
  the monolithic `build_command_tree()` function into decorated modules under
  `anvil_serving.commands`. The manifest no longer duplicates documentation in
  `examples`, `configuration_notes`, or `behavior_notes`; leaf parsers own
  detailed argument help and the CLI reference owns workflows and guidance.

### Added

- **Explicit operator stacks.** Serving, auxiliary capabilities, voice audio,
  ComfyUI, and Workbench now have separate Compose projects and lifecycle
  boundaries. Router ownership is fixed to the `anvil-serving` project, Dark
  owns model-serving resources, and purpose stacks can be started and stopped
  independently without colliding on a shared Compose project.
- **Locally built Workbench lifecycle.** `workbench build` provides a
  confirmation-gated local image build, while Workbench startup uses private
  Compose DNS and the authenticated router contract. The release sweep
  validated the complete Workbench-to-router-to-Laguna path.
- **Published production-readiness evidence.** The dated release sweep records
  live Agent-A1 and Laguna capacity and quality runs plus managed voice, OCR,
  reranker, embeddings, ComfyUI, router, recipe, and Workbench lifecycle
  validation on the reference hardware.

### Changed

- **Benchmark internals are now a cohesive package.** The public
  `anvil_serving.benchmark` facade delegates artifact handling, suite validation,
  request transport, evaluation, recipe emission, workload execution, and CLI
  coordination to focused modules under `anvil_serving.benchmarking`.
- **Capacity measurements are deterministic and token-aware.** Sampled context
  plans use a recorded seed, the measured inverse-CDF includes its 16K bucket,
  shared prefixes honor their declared token budget, burst requests retain a
  unique suffix, and streamed throughput prefers authoritative
  `usage.completion_tokens`. Capacity artifacts declare measurement protocol
  `capacity-v2`; exact token throughput is suppressed when usage is unavailable.
- **Benchmark evidence fails closed more consistently.** Context windows without
  prompt headroom and non-finite JSON metrics are rejected, request failures are
  retained as bounded error classes, monotonic clocks measure durations, and
  percentile summaries use the documented nearest-rank method.
- **`init` discovers host identity without accumulating redundant backups.**
  It assigns the largest GPU to Primary, the smallest GPU to Auxiliary
  (runtime index breaks equal-capacity ties), discovers the Tailscale IPv4
  address, and rewrites only files whose generated content changed.

- **Bare `serves status` is bounded to the supported serving path.** Grouped
  manifest entries are polled by default; untagged candidates and experiments
  require an explicit name or group selection and are excluded from the
  default reservation probe.

- **Command registration is modular and deterministic.** Eight explicit
  command-family factories now assemble the same 133-path operational surface
  without filesystem discovery or eager handler imports. Shared lifecycle
  options are declared once, and the legacy `anvil_serving.command_tree`
  module is reduced to compatibility imports.
- **Controller and MCP internals are independently reviewable packages.**
  Persistence, security, transport, protocol, runtime, catalog, and tool-family
  implementations now live under `anvil_serving.control_plane`; the existing
  `anvil_serving.controller` and `anvil_serving.mcp` modules remain explicit
  compatibility facades with deterministic direct dispatch.

### Fixed

- **Live lifecycle commands now match their production contracts.** The sweep
  corrected Compose project attribution, router ownership, purpose-stack
  selection, readiness checks, model-cache and benchmark-evidence handling,
  voice routing, and cleanup of stopped test containers through product verbs.
- **Workbench bring-up is reproducible without an external image repository.**
  The CLI builds the local image, obtains the existing router authentication
  contract, starts the stack, and verifies the authenticated sandbox path.

## [0.13.3] - 2026-07-26

### Breaking changes

- **The router is now a thin, direct capability gateway.** The intent
  classifier, work classes, policy profiles, residency selection, verification
  and commit-window chain, fallback and cloud escalation, calibration, route
  fingerprinting, traffic-window quality metrics, and `/v1/route` endpoint
  have been removed. `[router].model_routes` is the complete chat alias
  vocabulary: every normalized alias maps to exactly one local tier, every
  chat tier must be addressable, and an unknown alias returns
  `model_not_found`. Legacy commands, options, configuration keys, presets,
  mode manifests, and compatibility tombstones are not accepted.

- **Reference hardware roles are explicit.** The RTX PRO 6000 is the primary
  LLM serving host. The RTX 5090 is reserved for the low-latency voice LLM,
  STT/TTS, embeddings, reranking, and optional ComfyUI workloads. This change
  does not promote a model or claim new live hardware qualification.

### Removed

- **Residual legacy and duplicate surfaces.** Removed the unreachable cloud
  backend and echo-module entry point, consolidated the local HTTP relay,
  moved deterministic backends into test support, deleted superseded one-off
  launch scripts and machine snapshots, and removed obsolete route-analysis
  and combined-probe agent roles.

### Added

- **Direct model aliases.** `[router].model_routes` maps normalized chat model
  aliases to one local tier. Direct routes retain authentication,
  Anthropic/OpenAI dialect handling, raw SSE relay, readiness, admission,
  context/tool constraints, metadata-only decision logs, purpose models, and
  the audio gateway. `/v1/models` advertises only configured aliases. See
  ADR-0028.

- **Bearer-authed per-tier/serve health snapshot.** `GET /v1/health/tiers` returns a live
  readiness snapshot for EVERY configured serve — chat `llm` tiers, purpose models, and audio
  routes — not only recently-routed ones, so a configured-but-idle tier is no longer
  indistinguishable from a down one. It reuses the router's already-tracked, cached availability
  probe (the same state that produces `skipped-unavailable` / `health_transport_*` in routing), so
  polling adds no heavy new probe path. Each row carries only `{id, role, status, last_check,
  latency_ms, reason}`: a serve host, URL, upstream token, or model id never appears, and a
  `reason` is a bounded content-free category. Authenticated with the router bearer like every
  route except `GET /healthz`; `/health` and `/v1/decisions` are unchanged. Resolves #292.

- **Realtime assistant transcript streaming.** Sessions that explicitly request both
  `audio` and `text` now receive TTS-authoritative
  `response.output_audio_transcript.delta` events followed by
  `response.output_audio_transcript.done`, correlated to the same response and content
  item as `response.created` and `response.done`. The terminal equals the streamed
  deltas, fallback-normalized text is reported exactly, failed synthesis never claims
  unspoken text, and per-response retention is bounded. Audio-only sessions retain
  their existing audio event stream. See ADR-0025.

- **Authenticated normalized one-shot audio gateway.** Opt-in private Dark STT/TTS routes now
  expose bearer-authenticated JSON `POST /v1/audio/transcriptions` and
  `POST /v1/audio/speech` on the router, keeping raw serve hosts, audio bytes, transcripts, and
  synthesis text out of callers and decision records. The gateway bounds inputs, outputs, and
  upstream wall-clock time; records only safe route/byte/latency metadata; requires resolved
  front-door auth; and currently exposes the live-qualified Kokoro PCM16 contract with its explicit
  sample rate. It is a future HTTP-client seam and does not replace the Workbench Realtime relay.

- **Lifecycle-aware WSL page-cache reclaim.** A strict, default-off machine policy in
  `$ANVIL_SERVING_HOME/host.toml` lets confirmed model pulls, recipe loads, and managed serve
  up/adopt/switch/promotion boundaries evaluate one synchronous, readiness-aware cache reclaim.
  The hook requires threshold, 1 GiB operation-growth, and settled-I/O evidence; uses
  page-cache-only `drop_caches=1`; reports a structured warning-only outcome; and is inherited by
  controller-dispatched serve operations without a new command, lifecycle flag, or MCP tool.
  `host status` exposes the resolved policy, `init` ships the disabled template, and ADR-0023
  records the VM-wide performance and consent tradeoffs.

- **Repo-local agent controller registration and voice discovery.** Trusted Codex and Claude Code
  checkouts now start the checkout's `anvil-serving mcp serve` entry point instead of depending on a
  potentially stale global registration, and thin harness-specific wrappers expose the canonical
  voice operations skill without copying its body.

- **Guarded recipe-based model switching.** `anvil-serving serves switch ROLE` lists validated
  activation-ready recipes without prompting; `serves switch ROLE MODEL --confirm` performs the reviewed
  Compose/container/router transition with exact loopback bindings, cross-platform locks,
  operation-owned router artifact snapshots, compare-and-swap drift protection, fresh gate
  evidence, bounded failure handling, and automatic rollback only while router state is known.

### Changed

- **Serving and benchmark evidence are now the product center.** The reference
  topology assigns primary LLM inference to the RTX PRO 6000 and uses the RTX
  5090 for a low-latency voice LLM, STT/TTS, embeddings, reranking, and
  on-demand ComfyUI. The former intelligent-routing path is removed rather than
  retained for compatibility.

- **Private-only evidence grounding has been removed.** The historical Anvil integration audit,
  planning-capability evaluation, complete bounded eval bundle, and harness-routing research are
  now public dated findings. Load-bearing docs link those public records; missing ADR-0008 raw logs
  are recorded as an evidence gap instead of being implied to exist privately.

- **Public dated findings now have a durable evidence policy.** Findings remain inspectable after
  their recommendations are superseded, while prospective raw artifacts are sanitized, bounded,
  indexed, correction-preserving, and externalized with stable URLs and hashes when too large for
  reviewable Git storage. Private notes may supplement but never solely ground a public claim. See
  ADR-0027.

- **OpenClaw integration documentation is now a current-state contract.** The canonical spec leads
  with the shipped hook, generated setup path, model-id and maximum-reachable-tier context-window
  rules, ownership boundaries, and upgrade proof. Historical research and incidents are condensed
  into a linked appendix instead of interrupting the operational contract.

- **Repo-scoped Workbench guidance now matches the current CLI and MCP control plane.** The Codex,
  Claude Code, and OpenClaw workbench skills catalog all structured tools, distinguish capacity
  probes from repeated quality evaluation, cover reservations/transitions/promotion/telemetry/host
  gates, and document the CLI-only role-based recipe switch. The specialized voice skill now uses
  the lifecycle MCP tools when available and keeps audio profiles separate from candidate LLM
  overlays. Contract tests prevent future command-tree, MCP, and checked-in skill drift.
  Adversarial hardening adds source-freshness and recipe CRUD/load playbooks, exact three-part
  promotion gates, LF-safe repo gate routing, and
  spec-independent reviewer probes. OpenClaw no longer auto-generates critic roles through the
  candidate-routed Anvil provider.

- **Model-serve commands now share one reviewed, task-oriented help system.** Every
  `serves` leaf presents exact usage, concrete examples, configuration precedence,
  behavior and safety notes, local arguments, global options, and its owning reference
  page in a width-bounded layout. Heavy model selection is the direct
  `serves switch heavy MODEL` preview/apply flow; `serves rm` and `serves adopt` now use
  only the canonical `--confirm` spelling.
  Command examples and guidance are versioned in the machine manifest, parser-tested,
  and ratcheted in the exhaustive per-leaf UX audit; deterministic local merge-gate
  routing now mirrors the repository's CI and strict documentation checks.

- **Model and recipe commands now use the same task-oriented reference layout.**
  Catalog, artifact, scoring, recipe CRUD/load, and cache-prune help pages show
  copyable examples, configuration precedence, behavior boundaries, global options,
  and direct documentation links. The model guide now leads with the safe Heavy swap:
  discover a compatible recipe, inspect it, preview `serves switch heavy MODEL`, then
  apply the reviewed command with `--confirm`.

- **Router commands now use grouped, task-oriented help and documentation.** Every
  router leaf presents exact examples, configuration precedence, behavior and safety
  boundaries, global target options, and its reference link. The router guide groups
  foreground/discovery, deployment lifecycle, tier transitions, and credential tasks;
  endpoint discovery now uses the same real-parser reviewed renderer as other leaves.

- **Evaluation is split into explicit capacity and quality workflows.**
  `eval benchmark capacity` measures endpoint performance, while `eval benchmark quality`
  produces repeated protocol-v3 correctness evidence. Model-family reasoning controls fail closed
  where incompatibility is known; artifacts retain visible/reasoning budgets, full visible output,
  finish reasons, provenance, and per-attempt failure classification; ranking suites must declare
  stronger validators; malformed or resource-exhausting suites fail before requests; failed gates
  still retain atomic evidence. Requested output paths and notebook eligibility are validated before
  live work, remote aggregate deadlines cannot exceed the transport cap, and `eval preflight`
  exposes the same bounds and controls locally and through the controller. Usage-log analysis now
  has bounded recursive input scanning, per-child deadlines, and rollback-safe paired output commits.
  The former `eval benchmark run` and `eval planning` forms return exact migration guidance.

- **Model operations now share one preview/apply grammar.** Catalog sync has a
  no-write preview, shared confirmation, output ownership checks, an output-specific
  lock, validated staged replacement, and numbered backup; worker or install failures
  leave the active catalog intact, setup/launch failures are bounded, failed rollback
  names its preserved recovery artifact, and child output remains valid under CLI JSON mode;
  installed builds resolve the packaged recipe registry after project/config-home
  locations; recipe leaf help and plans expose runnable preview/apply and recovery
  details and reject ignored `serve.args`; and cache deletion uses the canonical
  `--confirm` gate, refuses to infer
  current-host deletability from metadata alone, and returns partial status for failed
  removals. The former `--yes` spelling returns migration guidance.

- **A single CLI interaction contract is the adopted migration target.** ADR-0021 codifies resource-first
  grammar, contextual help, configuration precedence, complete dry-run plans, shared confirmation,
  transactional recovery, stable JSON, actionable errors, and cross-platform behavior. A versioned
  per-verb audit and CI completeness gate are required before the migration is considered complete.
- **CLI documentation is organized by operator workflow.** A concise reference landing page now
  maps every public verb into focused router, serve, model-and-recipe, evaluation, host,
  control-plane, and voice pages. Full recipe CRUD and loading are directly discoverable, focused
  command help links to its owning family page, and the generated exhaustive index remains the
  drift-checked lookup surface.
- **CLI examples are parser-checked against required operands and safety gates.** Router and serve
  promotion, tier transitions, topology resolution, voice ownership, GPU-sharing probes, grouped
  serve logs, and cache deletion now show complete copyable forms. Collector actions are first-class
  command-tree leaves, and writing collector configuration is confirmation-gated.

### Fixed

- **OpenClaw wire validation now tests the integration vocabulary it actually serves.** The
  validator loads the shipped plugin's runtime preset export, requires every plugin preset in the
  selected router config, treats optional router-global presets as explicitly out of scope, and
  validates only Anvil-bound overrides in mixed decision logs. Explicit captures now fail closed on
  empty, malformed, inconsistent, or non-string route evidence instead of producing false passes.

- **Router lifecycle controls now match their canonical CLI and MCP contracts.** Action-specific
  help exposes Compose, service, environment, container, verification, and guarded recreate
  options; previews and results name their exact selected target; MCP preserves confirmation; and
  router-only recreation keeps Compose `--no-deps` intact.

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
  default, opt-in metered cloud, keyless handoff, $0-metered framing). Selected internal planning,
  findings, and PRD records were copied to the private companion repo
  `fakoli/anvil-serving-notes`; the three later-removed planning/review documents remained
  recoverable in public Git history but were not present in the notes-repository audit recorded by
  the 2026-07-22 public-evidence publication finding.
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
  [published hard eval data](https://github.com/fakoli/anvil-serving/blob/main/docs/findings/2026-06-28-planning-capability-eval.md).
- **The T017 traffic fixture is synthetic.** Traffic-metrics behavior is exercised against a
  synthetic fixture, not yet against real routed production traffic.

[Unreleased]: https://github.com/fakoli/anvil-serving/compare/v0.21.0...HEAD
[0.21.0]: https://github.com/fakoli/anvil-serving/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/fakoli/anvil-serving/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/fakoli/anvil-serving/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/fakoli/anvil-serving/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/fakoli/anvil-serving/compare/v0.16.1...v0.17.0
[0.16.1]: https://github.com/fakoli/anvil-serving/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/fakoli/anvil-serving/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/fakoli/anvil-serving/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/fakoli/anvil-serving/compare/v0.13.3...v0.14.0
[0.13.3]: https://github.com/fakoli/anvil-serving/compare/v0.13.2...v0.13.3
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
