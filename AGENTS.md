# AGENTS.md — anvil-serving

> See `CLAUDE.md` for the full product context, architecture diagram, gotchas, and design
> decisions. This file covers the agent-specific bits: how an agent should orient to this
> repo and what the working conventions are.

## What you're working in

A local-model serving and benchmark substrate plus a thin capability gateway
(`anvil_serving/router/`).
(`eval usage`, `models sync`, `serves render`, `eval preflight`, `eval benchmark run`,
`serves multiplex`, plus `init`, `doctor`, and `host gpus`). The router is shipped (v0.7.x): token-authed
containerized service, cross-dialect tool translation, true upstream SSE streaming,
readiness, admission, and metadata-only decisions. The canonical product description is
`README.md`; do not contradict it.

## Read before you write

1. **`README.md`** — source of truth for current product framing.
2. **`CLAUDE.md`** — architecture module map, gotchas, design decisions.
3. **The file(s) you're about to change** — read them fully before editing. The
   gateway is direct-only: extend its explicit route, protocol, readiness, or
   admission seams; do not reintroduce inferred routing or fallback behavior.

## Published-docs topology policy

Everything under `docs/` is published to the public site, including raw evidence
JSON/text under `docs/findings/`. Published files use **generic topology values**:

- The generic tailnet placeholder address is `100.64.0.10`. Never write a real
  tailnet/private address, MagicDNS name, or any other network identity of an
  operator's machine into `docs/`. If evidence output contains one, redact it to
  the placeholder before committing and note the redaction in the finding.
- Real topology values (actual tailnet addresses, host bindings, ports in use)
  live only in repo-internal, non-published files: this file, `CLAUDE.md`,
  `examples/`, and gitignored `.env`/config. Do not "fix" a published doc by
  copying a real address back in, and do not change `examples/` or code to
  generic values — those must keep working against the real deployment.
- Host names (`Fakoli Dark`, `Fakoli Mini`, `fakoli-dark`) are acceptable in
  published docs; they carry no network-reachable information.

## Code conventions

- **Stdlib-only** in `anvil_serving/` — no new runtime dependencies without explicit sign-off.
- **`127.0.0.1`, never `localhost`** in any URL (config, test fixture, example, docstring).
- **Loopback is host-relative, and Mini is model-free by default.** In the
  reference OpenClaw voice topology, Fakoli Mini's 16 GB RAM is reserved for
  OpenClaw Gateway, Anvil Voice Realtime/proxy, Claude Code, and Codex. Do not
  run STT, TTS, or LLM model serves on Mini for reference testing. Fakoli Dark
  owns the router at `http://100.87.34.66:8000/v1`, candidate LLM serves, and
  STT/TTS model endpoints or bridge ports. `mini-dark-audio-proxy` means
  Mini-local proxy ports `127.0.0.1:30110` and `127.0.0.1:30111` forwarding to
  Dark, not local models and not the operator machine. `mini-audio` is an
  explicit optional same-host/local-audio mode only; it is not the normal
  OpenClaw Talk or benchmark topology.
- **Operational utilities live in anvil-serving.** If a utility manages lifecycle,
  ports, host operations, harness config, voice/audio routing, router/serve state,
  or any repeatable operator action, integrate it as an `anvil-serving` CLI verb
  and, where appropriate, an MCP/controller tool. Do not create random one-off
  scripts as the operational path.
- **Operate model candidates through recipes and Anvil Serving, not raw Docker.**
  Record every reproducible candidate in a serve-recipe registry; start it with
  `models recipes load`, then use `models recipes status`, `models recipes logs`,
  and `models recipes unload` for its lifecycle. Use `serves` verbs for
  manifest-owned deployments. Raw Docker is limited to the narrowest read-only
  diagnosis when the product surface itself is broken; treat that as an
  immediate product gap, create or update a `.tickets/` record, fix the Anvil
  surface, and return to the managed command before continuing.
- **Benchmark research must be date-aware.** When choosing or comparing Fast/Heavy
  model candidates, prefer current official sources and recent hardware-matched
  community data. Record the source URL, published/observed date, age class, evidence
  type, hardware/engine relevance, and decision impact. Treat old Reddit/forum posts
  as historical recipe leads only unless local benchmarks or current official sources
  corroborate them.
- **Publish user-relevant benchmark outcomes.** In the same change that records a model
  benchmark, add a dated narrative and raw artifact links under `docs/findings/`, update its
  index, and update `docs/BENCHMARKS.md` when the current recommendation, reference
  deployment, or comparison table changes. State the model/served name, host and topology,
  hardware, engine/quant/context/concurrency, gate status, metrics, failures, and caveats.
  Link evidence rather than copying raw JSON; label external priors and incomplete or failed
  runs accurately. Publishing evidence never bypasses the human gate for promotion.
- **Apply the benchmark publication matrix.** Follow
  `skills/anvil-serving-benchmark-docs/SKILL.md`: update the dated finding and
  index, run catalog, model dossier, and measured hardware page. Update the
  chronological archive only when guidance/comparisons change and methodology
  only when its contract changes. Separate measured hardware from
  protected/co-resident or topology-only mentions.
- **Kernel tunes are explicit, pinned artifacts.** Use
  `skills/anvil-serving-kernel-tuning/SKILL.md` for missing MoE/GEMM config
  warnings, kernel bottlenecks, and retuning after runtime or GPU changes.
  Store tunes under
  `configs/kernel-tunes/<engine>/<engine-revision>/<gpu-slug>/` with a
  `kernel-tune-manifest/v1`; use a short Windows-portable repository filename,
  record the exact engine-required filename in the manifest, and keep raw
  default/tuned evidence under the dated finding. A stored tune is inert until
  an exact managed recipe supplies it under that engine-required filename
  through a read-only mount preparation step or pinned derived image layer,
  selects it through the engine-supported control, and startup logs prove it
  loaded. Never reuse a tune across a different GPU product,
  engine/Triton/CUDA build, dtype, TP size, or model geometry without
  requalification. A missing-config warning is a tuning candidate, not proof
  that tuning will improve end-to-end performance.
- **Return dicts, not print-side-effects** in library code. CLI wrappers print; modules return.
- **Never self-verify.** Don't write a check that uses the same model to validate its own
  output. Preflight and benchmark gates must be independent.
- **Credentials via env vars only.** Never put a key in a config file, a test fixture,
  a decision record, or a log line.
- All new model-calling code MUST use the **Claude Agent SDK** (not the raw `anthropic`
  SDK or a direct `api.anthropic.com` call). See the golden rule in `CLAUDE.md`.

## Verification workflow

```bash
pip install -e ".[dev]"
python -m pytest tests/ -x -q          # full suite
anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 --model <name> --confirm  # live gate
```

For router changes, the unit tests in `tests/router/` are the primary gate. Integration
tests against a live local tier require `preflight`.

## Failure investigation

- Treat a failed health check, router response, or benchmark request as a
  symptom, not a root cause. Inspect the authoritative logs for the component
  that actually failed before changing models, images, routes, or recipes.
- For managed containers, use the product's bounded lifecycle/status/log verbs
  first. Capture the earliest actionable startup or request error from the
  owning container, plus relevant exit state and health detail. If the product
  cannot retrieve those logs, use the narrowest read-only Docker inspection
  needed and record that missing product capability as a tooling gap.
- Follow failures down the stack: caller response, router decision/upstream
  error, serving-container logs, then engine/runtime/model-download details.
  Do not stop at a higher-level 4xx/5xx when lower-level evidence is available.
- Distinguish authentication, authorization/license, missing dependency,
  incompatible engine/model, resource exhaustion, and application-quality
  failures. Preserve the exact error and attempted identity in the dated
  finding or ticket so later benchmarks can compare causes rather than just
  pass/fail outcomes.
- When the fix is in scope, make it durable in the managed image, manifest,
  recipe, CLI, or controller surface, then rerun the failing probe and the
  relevant regression gate. Do not silently work around the defect with an
  unrecorded one-off command.

## Agent model strategy

Use model depth at decision boundaries, then reduce it for bounded execution:

- Use **GPT-5.6 Sol with high reasoning** for behavior-first PRDs, product or
  architecture boundaries, cross-cutting compatibility changes, and final
  deprecation/removal review.
- Use **GPT-5.6 Terra with medium reasoning** for focused implementation tasks
  whose acceptance criteria and verification commands are already explicit.
  Raise Terra to high for parser, router, migration, or edge-case-heavy work.
- Use Terra low or medium for mechanical renames, fixtures, inventories,
  documentation synchronization, and straightforward tests.
- Prefer PR-sized tasks and the lowest reasoning effort that reliably passes
  the independent verification gate. Avoid Max, Ultra, and subagent fan-out
  unless the work is both genuinely difficult and cleanly decomposable.
- A stronger planning pass is an efficiency measure only when it reduces
  ambiguity and rework; it does not replace recorded tests or human promotion
  approval.

## Working with the router

Chat aliases in `[router.model_routes]` map to exactly one local tier. Unknown aliases
are 404s; unavailable selected tiers are errors. Keep auth, dialect translation, streaming,
readiness, admission, and `DecisionLog` intact. The OpenClaw adapter contract is documented
in `docs/OPENCLAW-INTEGRATION-SPEC.md`.

## What NOT to do

- Don't add an `anthropic` SDK import or a direct `api.anthropic.com` call. Flag it instead.
- Don't bind to `localhost` — use `127.0.0.1`.
- Don't add FastAPI, uvicorn, or any async framework to the router or substrate.
- Don't auto-promote a model config without a human gate and recorded preflight/benchmark evidence.
- Don't make ad hoc lifecycle or operations scripts the way to run the product.
  Scripts may be demos, fixtures, or validation harnesses, but durable operations
  belong behind the `anvil-serving` utility surface.
- Don't touch `specs/archive/` — those are historical records, not live design docs.
