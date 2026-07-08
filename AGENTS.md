# AGENTS.md — anvil-serving

> See `CLAUDE.md` for the full product context, architecture diagram, gotchas, and design
> decisions. This file covers the agent-specific bits: how an agent should orient to this
> repo and what the working conventions are.

## What you're working in

A quality-gated local-model router (`anvil_serving/router/`) plus a serving substrate
(`profile`, `models sync`, `deploy`, `preflight`, `benchmark`, `multiplexer`, plus the
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
- **Loopback is host-relative.** In the reference OpenClaw voice topology,
  OpenClaw Gateway and Anvil Voice run on Fakoli Mini; `mini-audio` uses Mini
  loopback STT/TTS at `127.0.0.1:30010` and `127.0.0.1:30011`. Fakoli Dark owns
  the router at `http://100.87.34.66:8000/v1` and any Dark audio bridge or
  candidate LLM serves. `mini-dark-audio-proxy` means Mini-local proxy ports
  `127.0.0.1:30110` and `127.0.0.1:30111` forwarding to Dark, not the operator
  machine. A non-gateway checkout failing to reach those loopback ports is a
  topology negative control, not proof that the live Mini/Dark path is down.
- **Operational utilities live in anvil-serving.** If a utility manages lifecycle,
  ports, host operations, harness config, voice/audio routing, router/serve state,
  or any repeatable operator action, integrate it as an `anvil-serving` CLI verb
  and, where appropriate, an MCP/controller tool. Do not create random one-off
  scripts as the operational path.
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
anvil-serving preflight --base-url http://127.0.0.1:30000/v1 --model <name>  # live gate
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
