# Product discovery: persona walkthroughs and derived features

**Date:** 2026-08-08
**Status:** Analysis feeding the active program
**Relates to:** `STRATEGY-MAKE-DIVERGENCE-LOUD.md`; ADR-0027; ADR-0028; ADR-0033; ADR-0034

Five questions, answered by walking the project as each likely user. Every
claim below is grounded in a specific file, command, or incident — where a
persona "cannot do X", that was verified against the code, not assumed.

## 1. Persona walkthroughs — what can't they do, or only awkwardly?

### The solo GPU owner (first public adopter)

Installs the wheel, runs `init`, wants first tokens from their one card.

What they hit: serving one model requires holding **six concepts** before the
first request works — serve manifest, recipe, compose file, router tier, alias,
operator home — and three of those must agree byte-for-byte (`serves.toml`
port ↔ compose service port ↔ router tier `base_url`). `init` scaffolds all of
them and `deploy` keeps one model consistent, but the moment they add a second
model they are hand-editing three files whose agreement nothing checks until a
request fails.

The awkwardness is not the concepts — the separation is load-bearing
(ADR-0028) — it is that **no command walks the chain for them**. The join key
exists in the data: serve entries carry `router_tier`, the router config maps
alias → tier. Nothing joins them.

### The harness developer (OpenClaw / agent-SDK user)

Points a harness at the gateway, discovers models via `/v1/models`, reads
capability compat (0.23.0's `supportsStrictMode` / `supportedReasoningEfforts`).

What they hit: when an alias starts returning 503, **they can see nothing**.
The decision log is metadata-only and operator-side by design; readiness state
is operator-side; `fleet-status` is operator-side. From the caller's seat,
"tier draining", "serve stopped", "model identity mismatch", and "admission
full" are all the same 503. They file a bug against their own harness first.
(This reproduced live: an OpenClaw integration spent a day on breaker trips
that were a router-side `max_completion_tokens` clamp.)

### The benchmark researcher

Runs `eval preflight` and benchmark commands, publishes dated findings.

What they hit: results are only comparable **within** one engine and one
instrument. The three throughput instruments are documented as non-comparable,
and there is no artifact type that holds a same-model, same-quant, two-engine
comparison — the exact artifact ADR-0034 §8 committed to. They also cannot
prove *environment*: an artifact records model, engine, and hardware, but not
what else was serving at measurement time, which matters on shared cards.

### The fleet operator

Runs several hosts from one seat.

What they hit: everything cross-host is SSH plus memory. Version skew between
hosts silently changes code paths (observed: a host two minors behind resolved
transports differently and produced an error naming the wrong cause). Config
drift between a live operator home and its repository snapshot is invisible
(observed: a live home six commits behind main while serving production).
Features 7, 8, and 10 of the active program exist precisely for this persona.

### The voice user

Talks to the assistant; the pipeline spans proxy → STT → LLM → TTS across hosts.

What they hit: bring-up is three confirmed commands (`voice audio up`,
`serves up omni-small`, `voice proxy up`), and "is voice working?" has no
single answer anywhere. `fleet-status` proves each endpoint is *reachable*,
but reachability of four parts is not a working pipeline — nothing exercises
the path end to end short of speaking into it.

## 2. What is one or two small abstractions away?

- **The alias→serve join.** Both halves exist: `model_routes` (alias → tier)
  and `router_tier` (serve → tier). One join function yields `serves up-for
  llm.primary`, and lets `fleet-status` say not just "unreachable" but *which
  serve backs this alias and the exact command that starts it*. This is the
  single highest-leverage small abstraction in the codebase.
- **Fleet version/drift commands.** `host config inventory`/`export` (0.23.0)
  plus the existing SSH/controller transports are all the machinery needed;
  features 7 and 8 are thin compositions over them.
- **The two-engine parity artifact.** The benchmark runner, artifact schema,
  and evidence conventions all exist; the comparison is a new artifact type
  plus a second endpoint, not new infrastructure.
- **Rollback proof.** `_validate_promotion_topology` already encodes the
  correctness contract; `rollback-check` (feature 4, in flight) wraps it with
  image-presence verification.

## 3. Which capabilities become more valuable interacting?

- **`serves lint` + `rollback-check` + promotion.** Today they are separate
  commands an operator must remember. Run as automatic pre-transaction gates
  inside `serves promote` and `serves mode enter`, they convert "the operator
  should have checked" into "the transaction refuses to start on a broken
  precondition" — at zero new concept cost, since the commands already exist.
- **Reservations ledger + fleet-status.** The ledger answers "what does this
  card hold" per host; fleet-status answers "what is reachable" per alias.
  Joined, they answer ADR-0034 §5's fleet-level question — *is `llm.primary`
  served anywhere, and by what* — including why not ("declared on a host whose
  GPU is exclusively owned").
- **Benchmark artifacts + fleet-status.** Stamping each artifact with the
  fleet state at measurement time (what else was serving, versions, GPU
  occupancy) makes evidence self-describing about its environment — directly
  serving the ADR-0027 reproducibility contract.
- **Decision log + caller-visible reasons.** The router already *knows* why it
  refused (draining, identity mismatch, admission). Exposing a bounded reason
  code to the caller turns the harness developer's blind 503 into a one-glance
  diagnosis, without leaking operator detail.

## 4. What would eliminate several steps, commands, or concepts?

- **`serves up-for <alias>`** (from §2) eliminates the manual alias → tier →
  serve → up-command walk — today four file-reads and a cross-reference.
- **Gate integration** (from §3) eliminates two remembered commands per
  transaction by making them implicit preconditions.
- **A `voice` umbrella verb.** The `voice` group already tags stt/tts/proxy;
  the omni serve is one more member. `voice stack up --confirm` replacing three
  confirmed commands is group machinery that already exists, pointed at the
  actual persona workflow.
- **Promotion plan derivation.** A `[[promotion]]` block is ~15 hand-written
  lines whose values are all derivable from the target serve, the rollback
  serve, and their router profiles. `serves promote --derive TARGET ROLLBACK`
  writing the plan (validated by the existing topology check) removes the
  most error-prone TOML authoring in the product — the same file where the
  duplicate-name incident lived.

## 5. What is the architecture being used for that it didn't anticipate?

- **Operator homes are copied across hosts wholesale.** The voice host's live
  home was a byte-copy of the serving host's — which is how a missing rollback
  profile was recovered, and also why per-host "authoritative" state diverges
  by copy. The architecture assumed per-host authored homes; reality treats
  them as a distributable artifact. ADR-0034 §9's materialized-deploy model is
  the answer; feature 7 makes the interim drift visible.
- **The product checkout is used as live ops tooling.** Editable installs from
  scratch worktrees served production for weeks. The architecture assumed
  installed releases; reality wants a sanctioned "run from checkout" story
  with the worktree-anchor detection (feature 6) as the guardrail.
- **The evaluation worker grew a serving role.** ai-mbp25 was declared
  model-free; unified-memory MLX serving is now wanted on it. ADR-0034 §6
  absorbed this (three capability axes), and the native lifecycle (feature 9)
  completes it.
- **The fleet emerged from a single-host design.** Voice moved hosts; the
  router's routes followed; the topology documents lagged reality on every
  host. The system is *operated* as a fleet while being *described* as
  independent hosts — the entire divergence program is downstream of this one
  mismatch.

## Derived features

Added to the program backlog, ranked by persona reach × leverage:

| # | Feature | Persona | Builds on |
| --- | --- | --- | --- |
| 11 | `serves up-for <alias>` + `fleet-status --explain` | solo owner, fleet operator | alias→serve join (§2) |
| 12 | Lint + rollback-check as implicit transaction gates | fleet operator | features 1, 4 |
| 13 | Caller-visible bounded deny reasons | harness developer | decision log |
| 14 | Evidence environment stamping | benchmark researcher | fleet-status + artifact schema |
| 15 | `voice stack up` umbrella verb | voice user | serve groups |
| 16 | `serves promote --derive` plan generation | fleet operator | topology validator |

Features 11 and 12 are next after feature 4 lands: 11 is the highest-leverage
abstraction in §2, and 12 converts three shipped detectors into enforced
preconditions at zero concept cost.
