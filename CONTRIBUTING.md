# Contributing to anvil-serving

Thanks for your interest in improving anvil-serving. This guide covers the local setup, the few
hard rules that keep the project shippable, a map of the codebase, and recipes for the most common
kinds of extension.

## Local setup

Requires Python >= 3.11 (the router config loader uses stdlib `tomllib`).

```bash
git clone https://github.com/fakoli/anvil-serving
cd anvil-serving
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
```

The full suite is hermetic and should pass offline on Linux, macOS, and Windows.

## The hard rules

These are non-negotiable — a PR that breaks one will not pass review:

1. **stdlib-only runtime.** The package ships with `dependencies = []` and must stay that way. Do
   not add a third-party runtime dependency. Test-only tooling belongs in the `dev` extra
   (`pytest`); nothing it pulls in may leak into the importable package.
2. **Bind `127.0.0.1`, never `localhost`.** On Windows, `localhost` can trigger a ~21s IPv6
   lookup stall. Every host default, example, doc snippet, and test must use `127.0.0.1`.
3. **Tests stay hermetic.** No real network, no real LLM endpoint, no GPU. Use fixtures and fakes;
   a test that reaches out is a broken test. This is what lets CI run the same suite on
   `ubuntu-latest` and `windows-latest`.

Two design invariants sit right behind those rules — new code must respect them too:

- **No self-verification.** Every correctness gate (a verifier, preflight, the eval judge) must be
  independent of the model that produced the output it checks.
- **Secrets are env-var names, never values.** Configs carry names like `ANVIL_ROUTER_TOKEN`;
  `router/secrets.py` redacts resolved values from logs and decision records. Do not add a code
  path that stores or prints a raw credential.

## Module map

The package is one CLI (`anvil_serving/cli.py`) dispatching to per-verb modules, plus the router
package that is the product's data plane. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how
these fit together at runtime.

| Area | Modules | One-liner |
|------|---------|-----------|
| CLI | `cli.py` | Verb dispatch; each verb lazily imports its module. |
| Router data plane | `router/front_door.py`, `router/dialects/`, `router/intent.py`, `router/classify.py`, `router/policy.py`, `router/profile_store.py`, `router/fallback.py`, `router/verify.py`, `router/commit_window.py`, `router/serve.py`, `router/backends/` | HTTP front door, wire dialects, intent → policy → verify-and-fallback pipeline, backend composition. |
| Router support | `router/config.py`, `router/decision_log.py`, `router/metrics.py`, `router/secrets.py`, `router/fingerprint.py`, `router/discovery.py`, `router/seams.py`, `router/registry.py` | Config loading, audit records, redaction, serve fingerprints, `/v1/models` payload, typed extension seams. |
| Quality loop | `profile.py`, `eval.py`, `calibrate.py`, `score.py`, `router/profile_bootstrap.py`, `router/calibrate.py` | Usage measurement, shadow eval, guarded profile write-back, role scoring. |
| Local serving tools | `serves.py`, `models.py`, `deploy.py`, `init.py`, `preflight.py`, `benchmark.py`, `multiplexer.py`, `cache_prune.py`, `doctor.py`, `host.py` | Compose-defined serves, model catalog, tuned compose rendering, correctness/capacity gates, single-GPU swapping, environment checks. |
| Control plane | `mcp.py`, `controller.py`, `harness.py`, `router_manage.py` | Guarded MCP tools, tailnet HTTP controller, harness config sync, deployed-router lifecycle. |
| Voice | `voice/`, `voice_sidecar.py` | Realtime voice pipeline and speech-to-speech sidecar rendering. |
| External benchmarks | `external_benchmarks/` | Advisory benchmark priors: import, report, compare. |

## Extension recipes

The router's stages are typed seams (`typing.Protocol`, catalogued in `router/seams.py`), so most
extensions are "implement the Protocol, register it where the pipeline is composed" — no framework.
The design rules for all of them: a plugin that throws is treated as a fallback trigger, data-plane
code respects a latency budget, and heavy work goes async. Details in
[Quality-gated router §10](docs/QUALITY-GATED-ROUTER.md#10-extensibility--plugin-seams).

**Add a verifier** (structural response check):

1. Implement the `Verifier` protocol in `anvil_serving/router/verify.py`: a class with `name` and
   `verify(view: ResponseView) -> VerifyResult`. Keep it cheap, purely local (no I/O), and bounded
   (see the existing `MAX_SCAN_BYTES` guards).
2. Add it to `default_verifiers()` (order matters — cheap checks first) or wire it per-preset.
3. Mirror an existing test file in `tests/router/` (e.g. the `verify` tests): cover pass, fail,
   and the pathological input that motivated the bound.

**Add a wire dialect** (a new protocol the front door speaks):

1. Add a module under `anvil_serving/router/dialects/` implementing the `Dialect` protocol used by
   `openai.py` / `anthropic.py`: request parse → `InternalRequest`, response render, and SSE
   streaming.
2. Route it in `router/front_door.py` and, if tools must cross dialects, extend
   `router/dialects/translate.py`.
3. Add parity tests alongside `tests/router/`'s dialect tests (both directions, streaming and
   non-streaming, malformed payloads degrade instead of raising).

**Add a backend** (how a tier is called):

1. Subclass or mirror `router/backends/relay.py` / `cloud.py` (urllib only — no SDK), or
   `local.py` for deterministic in-process backends.
2. Compose it in `router/serve.py`'s `build_backends` based on tier config.
3. Tests must be hermetic: fake the HTTP layer, never call a real endpoint.

**Add an MCP tool** (operator/agent-facing operation):

1. Register it in the `TOOLS` registry in `anvil_serving/mcp.py` with a JSON schema.
2. Follow the safety gates the existing tools enforce: argv lists (never shell strings), mutating
   or expensive actions default to `dry_run=true` and require `confirm=true`, bounded numeric
   knobs, secret redaction on all output, and probe URLs restricted to loopback/private/tailnet
   hosts.
3. The controller (`controller.py`) reuses `mcp.list_tools`/`call_tool`, so a correctly registered
   tool is automatically available over the split-host transport. Test in `tests/test_mcp.py`.

**Record design decisions.** A non-trivial contract, routing/auth model, dependency, or protocol
choice needs an ADR in `docs/adr/` (one file per decision, from `docs/adr/template.md`). Never
silently change direction and never delete an ADR — supersede it.

## Tests

- Layout: top-level `tests/test_<verb>.py` per CLI verb, a large `tests/router/` suite for the
  data plane, plus `tests/external_benchmarks/`, `tests/voice/`, and shared `tests/fixtures/`.
- Run everything: `python -m pytest tests/ -q`. Run a slice while iterating:
  `python -m pytest tests/router/ -q` or `python -m pytest tests/test_mcp.py -q`.
- CI runs the suite on `{ubuntu, windows}` × `{3.11, 3.12, 3.13}`, plus `ruff check .` and a
  wheel-build/clean-install smoke test.
- The OpenClaw plugin has its own `node --test` suite under
  `plugins/openclaw-anvil-intent-router/`; `tests/router/test_keyword_parity.py` guards drift
  between the plugin's classifier data and the Python classifier.

## Docs

- The docs site builds with MkDocs: `pip install -r requirements-docs.txt && mkdocs build`
  (treat warnings as failures — they usually mean a broken link).
- A new page under `docs/` needs a `nav:` entry in `mkdocs.yml`.
- Doc snippets follow the same hard rules as code: `127.0.0.1` in URLs, env-var names for secrets.
- Dated evidence snapshots go in `docs/findings/` (and its index); durable reference content goes
  in the named docs.
- A user-relevant model benchmark is a documentation change as well as an artifact: add a dated
  findings narrative with the raw artifact link, tested configuration, gate outcomes, metrics,
  failures, and caveats; update `docs/findings/README.md`; and update
  `docs/BENCHMARKS.md` when the result changes the current recommendation, reference deployment,
  or a reader-facing comparison. Clearly distinguish local measurements from external advisory
  priors. Publishing results never authorizes a routing or production-model promotion.

## Workflow

1. Branch off `main` (e.g. `fix/...`, `feat/...`, `docs/...`).
2. Make the change; add or update tests alongside it.
3. Run `python -m pytest tests/ -q` locally and make sure it is green.
4. Open a PR. CI runs the suite across `{ubuntu, windows}` x `{3.11, 3.12, 3.13}`, builds the
   wheel, and smoke-tests a clean install — it must be green before merge.

Keep PRs focused: one logical change per PR, with a clear description of the *why*. If you are
unsure whether a change fits the direction, open an issue first.

> **Design history:** internal design discussions, planning PRDs, dated bake-off findings, and
> pre-pivot spec archives live in the companion repo `fakoli/anvil-serving-notes` *(private — not
> accessible to external readers; the conclusions this repo relies on are restated in its public
> docs and ADRs)*.
