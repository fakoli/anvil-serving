# ADR-0036 — Voice relocates to the opportunistic macOS node; the freed discrete-GPU host becomes the qualification and ComfyUI host

- **Status:** Proposed (drafted 2026-08-09; operator review pending — one open selection in §2)
- **Date:** 2026-08-09
- **Relates to:** ADR-0017; ADR-0024; ADR-0027; ADR-0028; ADR-0032; ADR-0033; ADR-0035
- **Amends:** ADR-0034 §5 (voice ownership) and §6 (reference-instance classification)

This ADR is written for a project intended to be publicly usable. Fakoli host
names appear only as the reference instance; concrete addresses, GPU
identities, and route assignments remain private operator state per ADR-0032.

## Context

ADR-0034 (2026-08-08) declared voice ownership of the single-GPU host
(`fakoli-mid-mod` in the reference instance) **sacred**, classified the Apple
Silicon laptop (`ai-mbp25`) as `native / unified / opportunistic`, and derived
promotion eligibility from the availability class: only a `continuous` host may
back a promoted alias.

The operator has since relocated the voice capability — STT, TTS, and the
resident low-latency voice LLM — from the single-GPU host to the macOS node.
The single-GPU host now runs nothing. This creates three facts the record must
absorb rather than drift past:

1. **The map disagrees with the territory again.** The private host README,
   `CURRENT_STATE.md`, ADR-0034 §5/§6, and the open node-agent ticket all still
   describe the single-GPU host as the permanent voice host.
2. **An `opportunistic`-classified host now backs configured voice
   capabilities.** Under ADR-0034's derivation this host is not
   promotion-eligible, so the voice aliases' standing must be resolved
   explicitly (§2 below), not implicitly.
3. **A free `docker / discrete / continuous` GPU host exists with no declared
   role** — the only promotion-eligible silicon besides the serving host.

## Decision

### 1. Voice ownership follows the capability, not the machine

ADR-0034 §5's "sacred" clause bound voice to a specific host. The durable rule
is restated host-independently: **the voice capability has exactly one owning
host at a time, declared in topology, and the operator host may never reclaim
the voice owner's reserved resources for general serving.** In the reference
instance the owning host is now `ai-mbp25`. The former voice host's voice
manifests receive SUPERSEDED headers (the same convention used when Dark's
voice manifests were retired on 2026-08-07).

### 2. Voice-alias standing on an opportunistic host — one selection required

The promotion-eligibility derivation in ADR-0034 §6 stands unamended. That
leaves exactly two consistent resolutions; the operator must select one:

- **Option A — reclassify the macOS node as `continuous`.** Legitimate only if
  the machine's posture has actually changed: docked, mains-powered, sleep
  disabled, thermally stable, on a fixed network. Reclassification makes voice
  promotion-eligible again, and also upgrades the node's evaluation evidence
  from conditioned to standard.
- **Option B (drafted default) — declare `llm.voice` and the audio routes
  best-effort, non-promoted capabilities.** The aliases stay configured and
  routable; readiness still gates them at call time; but no promoted-alias
  claim and no published latency evidence may rest on them without the
  opportunistic conditions stated prominently (per ADR-0034's forbidden list).

Option B is the drafted default because it requires no claim about hardware
posture, and because a WER/latency figure measured on a machine that sleeps,
throttles, and runs on battery is exactly the non-reproducible evidence
ADR-0027/0028 exclude. If the laptop is in fact permanently docked, Option A is
the cleaner end state.

### 3. The freed single-GPU host becomes the qualification and ComfyUI host

Declared axes: `docker / discrete / continuous`, **no reserved capability**.
Its roles:

1. **Candidate qualification** — run the internet-recipe evidence ladder
   (`START_HERE.md`) for candidate models on its own GPU, so qualification
   campaigns stop competing with the serving host's exclusive-mode production
   serve. It is promotion-eligible: a candidate qualified here may back a
   promoted alias if the operator so decides — qualification evidence still
   never promotes by itself (ADR-0027/0028).
2. **On-demand ComfyUI tenant** — the reservation that voice residency
   displaced now fits again.

### 4. Router and topology reconciliation is part of this decision

The serving host's router must stop advertising voice routes anchored to the
former voice host: `llm.voice` and both audio routes repoint to the new owner's
declared endpoints (or are parked until the new owner's serves are validated
through the gateway). The fleet topology gains the macOS node as the voice
owner and the single-GPU host under its new role. Per ADR-0035, these are
confirm-gated installs with dated backups, and the private repository mirror is
updated in the same transaction (`config-adopt` semantics once the verb
exists).

### 5. Node-agent sequencing is unchanged and now easier

ADR-0034's sequencing — remote mutation read-only first, then the mid-tier
host, last the serving host's exclusive-mode transitions — stands. An empty
host is the ideal target for the first node-agent deployment. The open
node-agent ticket retargets: the agent is required on **both** the new voice
owner (scoped to voice + benchmark operations) and the qualification host
(scoped to serve-lifecycle + benchmark operations).

## Consequences

- **Positive.** Qualification traffic leaves the production host. The only
  free continuous discrete GPU gets a declared, promotion-eligible role. The
  voice-ownership rule becomes host-independent and survives future moves.
- **Negative.** Voice now depends on a unified-memory, possibly opportunistic
  machine; under Option B its aliases are formally best-effort. Two hosts now
  need the node agent before the fleet report is truthful. The native (MLX)
  serve path on the macOS node reaches production use ahead of the two-engine
  parity evidence ADR-0034 §8 scheduled.
- **To record before Accepted** (private repo): move date, the new owner's
  serve runtime (`native`/MLX vs `docker`) and endpoints, round-trip evidence
  for the relocated stack, whether the router repoint has been applied, and
  the §2 selection.

**Forbidden by this decision.** Reclaiming the qualification host for voice
without a superseding ADR. Publishing voice measurements from an
`opportunistic` owner without prominently stated conditions. Treating the
former voice host's stale voice manifests as live configuration.
