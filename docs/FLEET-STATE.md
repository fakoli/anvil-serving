# Fleet state: relationships, planes, and what replicates

Companion to [ADR-0035](adr/0035-fleet-configuration-reconciliation.md).
That ADR decided *how* fleet configuration converges (git as the state
store; controller-mediated install and adopt; reconciliation over
replication). This document finishes the design: the relationship model
between the systems, and the file-class-by-file-class answer to "how do
these files replicate."

Written generically per ADR-0032; the four-host reference instance appears
only as illustration.

## The gateway hierarchy

A fleet like this contains three kinds of gateway, and the design's load-
bearing rule is about how layers bind to each other:

```text
            humans · agents · personal assistants · dashboards   (CONSUMERS)
                  │  bind to STABLE NAMES only
      ┌───────────┴────────────┬──────────────────────┐
      │ harness / LLM gateway  │ realtime voice proxy │   CONSUMER GATEWAYS
      │ (where intent enters)  │                      │   (ingress host / island)
      └───────────┬────────────┴───────────┬──────────┘
                  │  aliases (llm.primary, llm.voice, audio purposes)
      ┌───────────┴───────────────────────────────────┐
      │ model router (data plane, authenticated)       │   MODEL GATEWAY
      └───────────┬───────────────────────────────────┘
                  │  installed intent: tiers → serves
      ┌───────────┴───────────────────────────────────┐
      │ serves (GPU hosts, native islands)             │   SERVES
      └───────────────────────────────────────────────┘

      CONTROL PLANE: operator repository (git)
        ── config-install / config-adopt ──▶ node agents on every host
```

**The binding rule: each layer references only the stable name of the layer
below.** Consumers hold gateway hostnames. Gateways hold aliases. The router
holds tier→serve bindings. Nothing skips a layer.

The payoff is measured, not theoretical. In the reference instance, one week
relocated the entire voice capability and its LLM across hosts twice. The
consumer that obeyed the rule (a personal assistant bound to `llm.primary`)
required zero changes. Every file that needed hand-editing during the moves —
a workbench env file, a proxy topology, a pipeline config — was a file
holding a **direct URL**, a layer-skipping binding. Replication problems are
mostly self-inflicted: the fewer files that hold anything but stable names,
the fewer files ever need to move.

## Three planes, opposite physics

| Plane | Contents | Change rate | Failure mode to avoid |
|---|---|---|---|
| **Intent** | topology, capabilities, gateway/tier bindings, consumer bindings | days | *wrong* (stale-for-minutes is fine) |
| **Observed** | model status, health, versions, drift | seconds–minutes | *stale* (wrong-for-seconds is fine) |
| **Data** | live request paths (router relay, voice legs) | per-request | *blocked on either other plane* |

Intent is human-written and belongs in files under git. Observation is
machine-derived and must **never be written into shared files** — a
replicated health file is how a request routes to a dead backend, and a
removed interpreter under a live CLI is invisible to any file sync but
obvious to one version probe. Data-plane services read intent at
start/reload only (config and process move together — several intent fields
are fail-closed coupled to process flags) and learn liveness from their own
probes with TTLs.

## The replication matrix

| File class | Authority | Replicas | Mechanism | Trigger |
|---|---|---|---|---|
| Canonical topology + capability policies | operator repo (git) | per-host derived views, provenance-stamped | `config-install` | operator, post-merge, confirm-gated |
| Host serve/voice manifests | operator repo ↔ live operator home | one live + one mirror | `config-install` / `config-adopt` | operator |
| Router vocabulary | operator repo mirror | file layers + installed volume | domain installer owning the coupled reload | operator |
| Consumer bindings | operator repo (consumer registry) | one per consumer | `config-install`, consumer domain | only when a *gateway* changes |
| Secrets | per-host env file | **never replicated** | host-side provisioning only | manual |
| Evidence | operator repo, append-only | none needed | captured after runs | per run |
| Model status | — | **never a file** | derived by inspection at query time | on query |
| Health | — | **never a file** | per-consumer probe caches with TTL | continuous |
| CLI artifacts | repo-named canonical pin (tag) | per-host installs | `fleet upgrade`, per-host strategy | operator |
| Fleet history registry | operator-seat database | disposable read-model | scheduled read-only fan-out | cron |

Reading the matrix top to bottom: **git carries the top four rows, with both
arrows explicit** (install makes the fleet match the repo; adopt makes the
repo admit what the fleet became — a one-arrow system rots in the direction
it cannot express). The bottom rows never replicate because they are
questions, not files.

## Roles in the reference instance

- **Ingress host (gateway/operator, model-free):** the harness/LLM gateway
  and tenant ingress (tailnet-scoped TLS names onto loopback services);
  designated fleet operator per ADR-0034.
- **Serving host (router + heavy GPU):** the model gateway and the promoted
  primary serve.
- **GPU host:** serving capacity; capabilities move on and off it through
  the intent plane (its entire voice stack relocated without it losing its
  identity).
- **Always-on evaluation host:** node agent, scheduled read-only reporting
  (`fleet version` / `fleet drift`), evaluation jobs, and — as a
  `native`/`unified`/`opportunistic` node — model-capable capabilities that
  are deliberately not promotion-eligible.

## What a capability move touches (the checklist this design produces)

1. Serve lifecycle on source and destination hosts (park, don't delete).
2. The model gateway's tier bindings — via its domain installer, all layers.
3. Consumer-gateway configs *only if the gateway itself moved*.
4. Nothing else: consumers bound to names come along for free; observation
   discovers the new reality by inspection; evidence records it.

Every deviation from that short list is a file that skipped a layer — find
it, and either bring it under the consumer registry or rebind it to a name.
