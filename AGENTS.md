# AGENTS.md — anvil-serving

> See `CLAUDE.md` for the full product context, architecture diagram, gotchas, and design
> decisions. This file covers the agent-specific bits: how an agent should orient to this
> repo and what the working conventions are.

## What you're working in

Anvil Serving is one umbrella product for Model Serving, Capability Gateway,
Evaluation & Evidence, Anvil Voice, Anvil Media, and Control Plane & Fleet.
The capability meta-router is the thin gateway in `anvil_serving/router/`; it
is one product family, not the whole product.
(`eval usage`, `models sync`, `serves render`, `eval preflight`, `eval benchmark run`,
`serves multiplex`, plus `init`, `doctor`, and `host gpus`). The router is shipped: token-authed
containerized service, cross-dialect tool translation, true upstream SSE streaming,
readiness, admission, and metadata-only decisions. All routing surfaces are
serving-engine agnostic: routes select declared capabilities, models, endpoints,
and dialects, never a runtime brand or weight format. The canonical product
description is `README.md`; do not contradict it.

## Read before you write

1. **`README.md`** — source of truth for current product framing.
2. **`CLAUDE.md`** — architecture module map, gotchas, design decisions.
3. **The file(s) you're about to change** — read them fully before editing. The
   gateway is direct-only: extend its explicit route, protocol, readiness, or
   admission seams; do not reintroduce inferred routing or fallback behavior.

## Public product and private operator-state policy

Treat **every tracked file as public**, including `AGENTS.md`, `CLAUDE.md`,
examples, tests, tickets, and raw evidence under `docs/findings/`.

- The generic tailnet placeholder address is `100.64.0.10`; the reserved
  `100.64.0.0/24` range may be used for distinct synthetic test fixtures. Never
  commit a real tailnet/private address, MagicDNS name, personal home path, or
  other network identity of an operator machine. If evidence contains one,
  sanitize it before committing and record the redaction in the finding.
- Real topology values, active/promoted assignments, GPU UUIDs, local paths,
  and unsanitized working evidence belong in the private operator repository or
  the operator home selected by `ANVIL_SERVING_HOME`. Credentials remain
  environment- or file-backed secrets and are never committed, even privately.
- Files under `examples/` and the packaged scaffold are public templates. They
  must remain generic and byte-synchronized; `anvil-serving init` may detect
  real values only while writing to the private operator home.
- Display labels such as `Fakoli Dark` and `Fakoli Mini` may remain in public
  benchmark evidence, but they must not resolve to a reachable network identity.

## Code conventions

- **Stdlib-only** in `anvil_serving/` — no new Python runtime dependencies
  without explicit sign-off. The packaged Mini-side MCP bridge is the approved
  exception: `mcp_bridge/` pins and bundles the official TypeScript MCP SDK,
  and remote MCP proxy mode requires Node.js 20+. Keep that dependency out of
  the router and Dark controller runtime.
- **`127.0.0.1`, never `localhost`** in any URL (config, test fixture, example, docstring).
- **Loopback is host-relative, and Mini is model-free by default.** In the
  reference OpenClaw voice topology, Fakoli Mini's 16 GB RAM is reserved for
  OpenClaw Gateway, Anvil Voice Realtime/proxy, Claude Code, and Codex. Do not
  run STT, TTS, or LLM model serves on Mini for reference testing. Fakoli Dark
  owns the router, candidate LLM serves, and
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
- **Keep routing serving-engine agnostic.** Chat aliases, purpose-model names,
  and audio routes resolve through declared endpoint, dialect, capability,
  readiness, and admission contracts. Do not branch route selection on
  `engine`, runtime image, quantization, launch mechanism, or vendor-specific
  behavior. llama.cpp, NInfer, SGLang, vLLM, TensorRT-LLM, or another engine may
  back a route when its adapter satisfies the declared contract. This includes
  running Unsloth or an Unsloth-provided serving stack: whether Unsloth supplies
  quantized artifacts, launch tooling, or the upstream server, routing still
  sees only the declared endpoint contract. Keep
  engine-specific flags, lifecycle, health/metrics parsing, and tuning in serve
  manifests, recipes, or bounded adapters; metadata may inform observability
  but never hidden selection, fallback, or substitution.
- **Operate model candidates through recipes and Anvil Serving, not raw Docker.**
  Record every reproducible candidate in a serve-recipe registry; start it with
  `models recipes load`, then use `models recipes status`, `models recipes logs`,
  and `models recipes unload` for its lifecycle. Use `serves` verbs for
  manifest-owned deployments. Raw Docker is limited to the narrowest read-only
  diagnosis when the product surface itself is broken; treat that as an
  immediate product gap, create or update a `.tickets/` record, fix the Anvil
  surface, and return to the managed command before continuing.
- **Prune infeasible recipes before expensive qualification.** Use
  `skills/anvil-serving-recipe-feasibility/SKILL.md` when model, draft, KV,
  context, concurrency, VRAM/RAM, quality-loss, or speed constraints can rule
  out candidates. Keep every load-bearing value as a sourced exact value,
  interval, or explicit unknown. Only optimistic-bound physical failures are
  unconditional mathematical rejections; label safe-envelope failures as
  policy-infeasible and paper-feasible candidates as benchmark survivors.
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
- **Coordinated releases require deployment readiness.** Use
  `skills/anvil-serving-release-readiness/SKILL.md` when work includes a merge,
  package release, controller/router rebuild, or live Mini-to-Dark deployment.
  Prove manifest-derived file mounts, exact endpoint version parity, rollback,
  and real Pi/OpenClaw client smokes; do not close while an in-scope outage
  remains.
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
python scripts/run_tests.py tests/ -x -q  # full suite with an isolated pytest temp root
anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 --model <name> --confirm  # live gate
```

The wrapper prevents concurrent Windows worktrees from sharing pytest's
`pytest-current` cleanup link. CI may continue to invoke pytest directly in an
isolated runner.

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
readiness, admission, and `DecisionLog` intact. Route resolution must remain independent of
the selected tier's serving engine and quantization; those are deployment and evidence
attributes, not routing inputs. The OpenClaw adapter contract is documented in
`docs/OPENCLAW-INTEGRATION-SPEC.md`.

## What NOT to do

- Don't add an `anthropic` SDK import or a direct `api.anthropic.com` call. Flag it instead.
- Don't bind to `localhost` — use `127.0.0.1`.
- Don't add FastAPI, uvicorn, or any async framework to the router or substrate.
- Don't auto-promote a model config without a human gate and recorded preflight/benchmark evidence.
- Don't make ad hoc lifecycle or operations scripts the way to run the product.
  Scripts may be demos, fixtures, or validation harnesses, but durable operations
  belong behind the `anvil-serving` utility surface.
- Don't touch `specs/archive/` — those are historical records, not live design docs.
