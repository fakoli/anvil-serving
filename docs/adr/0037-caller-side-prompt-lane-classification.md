# ADR-0037 — Caller-side prompt-lane classification: an advisory encoder sidecar outside the gateway

- **Status:** Proposed
- **Date:** 2026-08-09
- **Relates to:** ADR-0002 (serves are Compose-defined); ADR-0022 (evaluation
  evidence protocol); ADR-0028 (thin capability gateway); ADR-0032 (public
  product, private operator state); ADR-0033 (durability and plane contract)

This ADR is written for a project intended to be publicly usable. The
reference deployment appears only as motivation; concrete host names,
addresses, and route assignments remain private operator state per ADR-0032.

## Context

The gateway's chat vocabulary is a closed map: each configured alias resolves
to exactly one local tier, and the operator's fleet strategy is **model
diversity over replicas** — GPU capacity is spent on different capabilities,
never on copies of one model. As the vocabulary grows, every caller must
decide *which* alias fits a given prompt. Today that decision is hardcoded in
each harness (a coding agent always sends `llm.primary`; OCR flows always
send `vision.ocr`). With more specialist tiers, static per-harness rules stop
scaling: the knowledge of "which capability fits this prompt" is duplicated,
drifts, and is never measured.

Small zero-shot prompt classifiers now exist that make lane selection cheap.
The candidate that prompted this ADR is
[LiquidAI/LFM2.5-Encoder-350M-Prompt-Router](https://huggingface.co/LiquidAI/LFM2.5-Encoder-350M-Prompt-Router):

- **Architecture:** LFM2 bidirectional encoder (`AutoModel`, model type
  `lfm2`, architecture `Lfm2BidirForSequenceRouting`), 355,008,768
  parameters, full fine-tune of the masked-LM base
  `LiquidAI/LFM2.5-Encoder-350M` with a **zero-shot routing head**.
- **Interface:** `model.route(prompt, lanes, tokenizer=...)` scores one
  prompt against a caller-supplied list of **free-text lane descriptions in a
  single encoder pass** — lanes are not baked in at training time, so the
  lane set can mirror a gateway's configured aliases and evolve with them.
- **Runtime:** `transformers` + `torch`, **CPU-capable by design** (the
  vendor demo Space runs CPU-only). The published checkpoint is float32
  safetensors, approximately 1.42 GB; any reduced-precision conversion is a
  separate artifact and requires its own pin and qualification. Single-pass
  CPU latency remains to be measured rather than assumed (ADR-0022).
- **Languages:** 15 (en, de, es, fr, it, nl, pl, pt, ar, hi, ja, ru, tr, vi,
  zh).
- **Load caveat:** requires `trust_remote_code=True` — the repository ships
  custom modeling code that executes at load time.
- **License:** LFM Open License v1.0 (`license: other` on the Hub) — terms
  must be reviewed before any durable adoption.
- **Source freshness:** the official model card and repository were observed
  on 2026-08-20 at revision
  `35ca4a0469f180f1cf05a630df8842fa17ac18e3` (upstream last modified
  2026-07-28). That revision is the starting candidate, not an unpinned
  `main` reference.

Three standing decisions bound the option space:

1. **ADR-0028** removed intent classification from the product. The thin
   gateway documents, by name, that there is *no classifier, no policy
   selection, and no `/v1/route` decision endpoint*. An unknown alias is a
   404, never a redirect.
2. **Working rule 1** keeps `anvil_serving/` standard-library-only. A
   `torch`/`transformers` dependency — with remote code execution — cannot
   enter the router process.
3. **Working rule 6 and ADR-0022**: no capability claim without recorded
   evidence. A classifier's routing quality is exactly such a claim, and it
   must be measured on real traffic before it is trusted with anything.

The question is therefore not "can this model run here" (it can, cheaply,
on CPU) but **where the decision authority sits**: the gateway's identity is
that the *caller* decides and the gateway *verifies it can honor the
decision* (health, identity, admission). A classifier inside the gateway
would create a second decision-maker inside the trust boundary — the exact
premise whose consequences (fallback chains, calibration loops, cooldown
tuning) ADR-0028 deliberately excluded.

## Considered options

### Option 0 — No adoption

Callers keep hardcoded alias selection. Zero new surface, zero new
operational burden. But the lane-selection knowledge stays duplicated per
harness and unmeasured, and the cost of *evaluating* whether a classifier
would help is never paid — the question just recurs.

### Option A — Caller-side advisory sidecar (chosen)

Run the encoder as an ordinary Compose-defined serve (ADR-0002) **outside**
the `anvil_serving` package and outside the gateway request path. Callers
(harnesses, workbench) consult it *before* calling the gateway, map the
winning lane to a configured alias themselves, and call the gateway exactly
as today. The gateway never knows the classifier exists.

- Contract-preserving: the caller still decides; the gateway still verifies.
- stdlib rule untouched: heavy dependencies live in the sidecar container.
- Evidence-first: advisory operation produces the labeled agreement data
  that any future authority claim would require.
- Failure containment: a down or slow sidecar degrades to the caller's
  static default alias; gateway traffic is unaffected.

### Option B — Gateway pre-dispatch classification (rejected)

The router calls the classifier before tier resolution and rewrites the
alias. Rejected: reverses ADR-0028's central omission; adds a request-path
network dependency with a new failure mode ("classifier down → route to
what?"); makes routing quality a permanently maintained, calibrated claim;
and silently changes what a caller asked for — the failure mode this
project's wire-fidelity stance (fail-closed video translation, mid-stream
honesty) consistently refuses.

### Option C — Gateway-hosted advisory endpoint (`/v1/route`) (rejected)

The gateway proxies the sidecar under its own token as a read-only advisory
surface, without acting on the result. Rejected: the thin-gateway document
excludes a `/v1/route` decision endpoint by name; even advisory, it moves
lane semantics inside the gateway's public contract, couples gateway
availability to a torch process, and invites drift from "advisory" to
"authoritative" without a fresh decision. If cross-host callers need the
advice, they can reach the sidecar the same way they reach the gateway.

## Decision

Adopt **Option A**: a caller-side, advisory-only lane-classification
sidecar. The gateway contract (ADR-0028) is reaffirmed unchanged. Any future
grant of routing *authority* — even partial — requires a new ADR carrying
recorded agreement evidence per ADR-0022.

### System design

**Placement and lifecycle.** One Compose-defined serve (working name
`lane-router`), CPU-only: it takes **no GPU reservation** and never competes
with model serves for VRAM. The container bundles `torch` (CPU wheels),
`transformers`, and the application; the model weights mount read-only from
the model cache. Lifecycle is the ordinary serve lifecycle — no new
daemon class.

**Supply-chain pinning.** `trust_remote_code` is acceptable only with the
executed code frozen: the Hub repository is snapshotted at a **pinned
revision hash** into the model cache, and the custom modeling code is
**vendored into the image at build time** — the runtime never fetches code
or weights from the network. Upgrading the model is an image + snapshot
change, reviewed like any dependency bump.

**API contract.** A single authenticated JSON endpoint, loopback-bound by
default (the tailnet edge, ADR-0019, can path-route to it if cross-host
callers need it):

```
POST /v1/lanes/route
Authorization: Bearer <token from an env-var-named secret, tier-style>

{
  "prompt": "<text to classify>",
  "lanes": [
    {"id": "llm.primary",  "description": "coding, agentic tool use, long reasoning"},
    {"id": "vision.ocr",   "description": "reading text out of documents and images"},
    {"id": "llm.voice",    "description": "casual conversational voice replies"}
  ]
}

-> 200
{
  "object": "lane_scores",
  "scores": [
    {"id": "llm.primary", "score": 0.83},
    {"id": "vision.ocr",  "score": 0.11},
    {"id": "llm.voice",   "score": 0.06}
  ],
  "model_revision": "<pinned hash>",
  "latency_ms": 18
}
```

Validation mirrors the router's stance: bounded body (1 MiB), bounded prompt
length (32 KiB), 2–16 lanes each with bounded description length, unknown
fields rejected, bearer compared constant-time, `GET /healthz`
unauthenticated. Lane `id` strings are opaque to the sidecar — it scores
descriptions and echoes ids. **The gateway's `[router.model_routes]` remains
the only authority on what an id means**; the lane list lives in caller
configuration, private operator state per ADR-0032.

**Content safety.** The prompt is the request content. Mirroring the audio
gateway's contract: prompt text never enters logs, error messages, or the
decision trail. The sidecar emits one content-free line per request — lane
ids, scores to two decimals, latency, outcome — and nothing else.

**Caller integration.** The harness sends the sidecar request with a hard
advisory budget (suggested 150 ms connect-to-response); on timeout, error,
or low-margin scores it falls back to its static default alias. The caller
stamps its existing correlation id (`X-Request-Id`) on both the sidecar call
and the subsequent gateway call, so sidecar lane scores and the gateway's
`DecisionLog` records join offline on one key with no new gateway field.

**Evidence loop (the actual point).** Advisory operation continuously
produces `(top_lane, caller_chosen_alias, request_id)` pairs. Per ADR-0022,
evaluation is model-aware, repeated, and comparison-safe: a dated finding
reports agreement rate, per-lane confusion, score calibration, and latency
distribution on the operator's real traffic mix, with raw artifacts linked.
Pre-registered promotion bar (to be confirmed in the evidence, not adjusted
to meet it): sustained ≥95 % agreement with correct-by-inspection caller
choices, no lane below 90 %, before any ADR proposes giving the score even
tie-breaking authority. If the model measures poorly, the sidecar is
removed and this ADR's record of *why* remains.

## Consequences

- The thin-gateway contract survives contact with intent classification:
  zero router-package changes, zero new gateway endpoints, zero new
  request-path dependencies.
- One new CPU serve to build, pin, operate, and monitor; ~1–2 GB RAM
  budget on the serving host; a license review (LFM Open License v1.0) gates
  installation.
- Callers gain a measured, shared lane-selection mechanism and keep a
  fail-open static default; per-harness hardcoding becomes the fallback
  rather than the mechanism.
- Deferred, each requiring a new ADR with recorded evidence: any routing
  authority for the classifier; any gateway-hosted advisory surface; any
  automatic lane synthesis from `[router.model_routes]` metadata.
- Follow-up work: the sidecar implementation and Compose entry; the
  agreement-evaluation harness and first dated finding; the license review.
