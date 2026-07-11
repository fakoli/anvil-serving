# AGENTS.md — anvil-serving

> See `CLAUDE.md` for the full product context, architecture diagram, gotchas, and design
> decisions. This file covers the agent-specific bits: how an agent should orient to this
> repo and what the working conventions are.

## What you're working in

A quality-gated local-model router (`anvil_serving/router/`) plus a serving substrate
(`profile`, `models sync`, `serves render`, `preflight`, `benchmark`, `multiplexer`, plus the
v0.5.0 onboarding trio `init`/`doctor`/`gpus`). The router is shipped (v0.7.x): token-authed
containerized service, cross-dialect tool translation, true upstream SSE streaming,
residency-aware routing. The canonical product description is `README.md`; do not contradict it.

## Read before you write

1. **`README.md`** — source of truth for current product framing.
2. **`CLAUDE.md`** — architecture module map, gotchas, design decisions.
3. **The file(s) you're about to change** — read them fully before editing. Avoid
   writing code that duplicates existing logic; `verify.py`, `fallback.py`, and
   `policy.py` already implement the hot-path; extend through the `seams.py` hooks.

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
- **Return dicts, not print-side-effects** in library code. CLI wrappers print; modules return.
- **Never self-verify.** Don't write a check that uses the same model to validate its own
  output. Correctness gates (`verify.py`, `preflight.py`, `eval.py`) are independent.
- **Cloud credentials via env vars only.** `secrets.py` handles resolution and redaction.
  Never put a key in a config file, a test fixture, or a log line.
- All new model-calling code MUST use the **Claude Agent SDK** (not the raw `anthropic`
  SDK or a direct `api.anthropic.com` call). See the golden rule in `CLAUDE.md`.

## Verification workflow

```bash
pip install -e ".[dev]"
python -m pytest tests/ -x -q          # full suite; 993 tests expected green
anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 --model <name> --confirm  # live gate
```

For router changes, the unit tests in `tests/router/` are the primary gate. Integration
tests against a live local tier require `preflight`.

## Working with the router

The extension seams are in `router/seams.py` — use them rather than patching core modules
when adding adapter plugins (e.g. the OpenClaw adapter in `plugins/`). The plugin contract
is documented in `docs/OPENCLAW-INTEGRATION-SPEC.md`.

Routing decisions write to `DecisionLog`; quality calibration reads from `ProfileStore`.
Both are in-process; no daemon, no MCP, no external process required for the router itself.

## What NOT to do

- Don't add an `anthropic` SDK import or a direct `api.anthropic.com` call. Flag it instead.
- Don't bind to `localhost` — use `127.0.0.1`.
- Don't add FastAPI, uvicorn, or any async framework to the router or substrate.
- Don't auto-promote a tuned model config or a routing policy change without a human gate.
- Don't make ad hoc lifecycle or operations scripts the way to run the product.
  Scripts may be demos, fixtures, or validation harnesses, but durable operations
  belong behind the `anvil-serving` utility surface.
- Don't touch `specs/archive/` — those are historical records, not live design docs.
