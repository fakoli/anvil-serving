# ADR-0003 — Portable-by-default: out-of-box router correctness and a generated bring-up

- **Status:** **Accepted** (2026-07-01)
- **Date:** 2026-07-01
- **Relates to:** the `genericity` PRD (v0.5.0, in anvil-state) · [ADR-0001](0001-cloud-cost-and-subscription-auth.md)
  (the advise-and-defer failover contract the relay-timeout decision protects) ·
  [ADR-0002](0002-serves-are-compose-defined.md) (serves are compose-defined — this ADR closes the
  "example topology, not a portable default" trade-off it accepted) · a 42-finding genericity audit ·
  CLAUDE.md gotchas #1 (loopback bind) / #6, #9 (thinking-by-default empty content) / #13 (GPU-UUID
  pinning) / #14 (WSL2 UVA) / #15 (9P bind-mount) · `anvil_serving/router/{config.py, serve.py,
  backends/cloud.py, relay.py, commit_window.py}`, `anvil_serving/{deploy.py, serves.py, multiplexer.py}`,
  `configs/example.toml`, `templates/docker-compose.yml.tmpl`.

## Context

anvil-serving v0.4.x works end-to-end — but only on the authors' two-box topology (a macOS OpenClaw
gateway routing to GPU serves on a Windows/WSL2 box over Tailscale). A 42-finding audit across five
dimensions, grounded in hands-on onboarding onto a second machine, found the product is pinned to that
one setup in two **independent** ways:

1. **The shipped router config 404s out of the box.** `configs/example.toml` — the file the quickstart
   tells users to run — omits the per-tier `model=` served-name, so the router forwards the harness's
   preset token (`quick-edit`) as the upstream OpenAI `model` id, and every local request 404s
   (`backends/cloud.py`: `upstream_model = self._tier.model or request.model`). This is a *product
   default* defect, not an environment quirk. It compounds with two more: thinking-by-default local
   models (the strong current ones — gpt-oss, Qwen3.x) return **empty final content** on real agent
   turns, and the `allow` streaming fast-path (`serve.py`) runs **zero verifiers**, so the very
   `NonEmptyContent` check that exists to catch this is bypassed for exactly the common local classes;
   and a **hardcoded 120s relay timeout** (`serve.py` `build_backend_for_tier`) means a slow/hung local
   tier blows the caller's budget, defeating the fast native failover the advise-and-defer design
   ([ADR-0001](0001-cloud-cost-and-subscription-auth.md)) depends on.
2. **The environment bring-up is machine-pinned with no generator.** GPU UUIDs, absolute
   `C:/Users/…` model paths, ports, and a Tailscale IP are hand-authored; `serves`' default manifest is
   literally the authors' private `examples/fakoli-dark/serves.toml`; and `deploy` emits an incomplete,
   SGLang-only, `0.0.0.0`-exposed compose that neither pins the GPU correctly on WSL2 (integer
   `device_ids` are silently ignored — gotcha #13) nor wires the **three artifacts that must agree on
   one string** (compose `--served-model-name` ↔ `serves.toml` ↔ router tier `model=`).

[ADR-0002](0002-serves-are-compose-defined.md) explicitly accepted the compose files as "an example
topology, not a portable default — an operator on other hardware edits them (or renders a fresh one
with `deploy`)." In practice that escape hatch does not hold: `deploy` renders an artifact that is
incomplete, unsafe, and SGLang-only, so "render a fresh one" does not produce a working serve.

**Constraints.** The package stays **stdlib-only on the hot path** (no new runtime dependency); the
advise-and-defer posture (ADR-0001) and compose-defined serves (ADR-0002) are unchanged; the work is
**additive** and the existing suite stays green.

## Considered options

1. **Document the gaps; leave the defaults as-is** (treat `examples/fakoli-dark` as reference only).
   Rejected: the shipped default config *literally 404s on the first request*. "Read the docs more
   carefully" does not fix a broken default, and the empty-content/timeout failures are silent.
2. **Ship curated per-hardware preset configs.** Rejected: hardware is too varied (GPU count, UUIDs,
   model paths, ports); static files just re-encode the machine-pinning problem N times and drift.
3. **Make the defaults correct out-of-box AND generate the bring-up from detected hardware, safe by
   default.** Chosen. It fixes the product defect (broken default) and the portability defect (no
   generator) at their roots, and finally makes ADR-0002's "render a fresh one with `deploy`" true.

## Decision

anvil-serving is **portable-by-default**. Three commitments:

- **A. Out-of-box correctness.** The shipped defaults must serve a real request. A local tier's
  `model=` (the upstream served-model-name) is **required** — the config loader **warns** when a local
  tier omits it (the routing token would 404 upstream) — and may be **auto-derived** once at startup
  from `GET {base_url}/v1/models` (fail-fast on 0 or >1 candidates; an explicit `model=` always wins).
  The router is correct for **arbitrary local models**: a per-tier **`extra_body`** passthrough lets an
  operator inject `chat_template_kwargs={enable_thinking:false}` (taming thinking-by-default models); a
  minimal **verify-on-local-`allow`** gate (`NonEmptyContent`/`NotTruncated` via the commit window)
  ensures an empty/truncated local `200` is caught and escalated/deflected, **never delivered** to the
  harness; and the **relay timeout is configurable** and defaults short (≈15–30s) so a hung local tier
  fails fast into the ADR-0001 native-failover handoff instead of stalling ~120s.
- **B. Generated, consistent bring-up.** `anvil-serving init` (alias `onboard`) detects GPUs (index +
  UUID) and reads the `models sync` catalog, then emits **compose + `serves.toml` + router config**
  that agree on served-name and port. `deploy` becomes the generation engine: **UUID-based GPU pinning**
  (`CUDA_VISIBLE_DEVICES=<uuid>` + `CUDA_DEVICE_ORDER=PCI_BUS_ID`, belt-and-suspenders with
  `device_ids`, because integer `device_ids` are ignored on Docker-Desktop/WSL2 — gotcha #13); it emits
  the **`serves.toml` entry + a router-tier stub**; and it supports **vLLM as well as SGLang** — reusing
  `multiplexer.py`'s GPU-UUID resolution and per-engine argv (factored into a shared
  `anvil_serving/gpus.py`) so there is no second, drifting implementation. `serves` stops defaulting to
  the authors' private manifest (defaults to `./serves.toml`, points at `init` when absent).
- **C. Safe by default.** Generated serves bind **`127.0.0.1`**; exposing an (unauthenticated) serve
  beyond loopback is an explicit **`--expose-lan`** opt-in that prints a security warning — matching
  `SECURITY.md` and the front door's `--host` behavior. This closes the ADR-0002 trade-off: the
  portable default is now both **real** and **safe**.

## Consequences

- **What improves.** A stranger's first two commands (install, `serve`) succeed on a current, common
  environment; one command generates a working, correctly-pinned, loopback-bound bring-up with no
  hand-editing of UUIDs, paths, or served-names; the advise-and-defer failover degrades gracefully
  under a down/slow local tier; thinking-by-default models are usable through the router; and the
  example's machine-specific values are parameterized (`${…}`) or `# REPLACE:`-annotated.
- **What we keep.** The **stdlib-only hot path** — `init`/`deploy`/`gpus` are operator-side tooling on
  the same footing as the Docker/Compose prerequisite ADR-0002 already established, not a runtime
  import. The advise-and-defer cost posture (ADR-0001) and compose-defined serves (ADR-0002) are
  unchanged; this is additive and config-gated.
- **What it costs (new surface).** A generator that must keep `deploy`, `serves`, `models sync`, and the
  router config schema consistent — mitigated by reusing existing code (`multiplexer` GPU/argv, `deploy`
  render) and keeping emitted artifacts minimal rather than feature-rich. An optional `/v1/models`
  startup probe (kept fail-fast; explicit `model=` always wins). A bounded verify/buffer cost on the
  local streaming happy path (scoped to empty/truncated checks, local tiers only).
- **Relationship to prior ADRs.** Supersedes nothing. It **partially resolves** the ADR-0002 trade-off
  ("machine-specific example, not a portable default") by making `deploy`/`init` render the portable,
  safe default that ADR-0002 assumed a user could produce. It **protects** the ADR-0001 failover
  contract by making a local-tier stall fail fast enough for the gateway's native failover to succeed.
- **Open tactical choices** (deliberately deferred; tracked in the `genericity` PRD's Open Questions):
  interactive vs declarative `init`; `/v1/models` probe at startup vs lazily on first request;
  named-volume vs bind-mount as the generated model-mount default (the 9P-slowness gotcha #15); and
  whether a `models pull` weight-fetch helper is in scope for v0.5 (currently a Non-Goal).
- **Tracking.** Implementation is the **`genericity` PRD (v0.5.0)** in anvil-state — 19 requirements,
  3 features (Router correctness / Portable generation / First-run truth), 15 tasks.
