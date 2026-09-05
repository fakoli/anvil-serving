# Explicit qualified replicas across hosts

Status: open
Priority: P2
Depends on: qualified-replica-sets, replica-capacity-scheduler, fleet-node-enrollment

## User outcome

An operator explicitly assigns two equivalent instances of one model on
different systems to one capability alias, then distributes new requests among
those exact instances. One instance may be reached through private networking
outside the home. There is no request intent classifier or alternate model.

## Current boundary

The existing replica implementation campaign is deliberately same-host.
ADR-0034 excludes cross-host request scheduling. Adding remote members requires
an explicit successor contract, not accepting a URL in the same-host parser.
Single declared remote endpoints already fit the direct tier contract.

## Implementation sequence

1. Reconcile the shipped replica/fleet contracts before defining a successor
   ADR and PRD. Preserve the alias-to-logical-tier boundary.
2. Require explicit membership, immutable model revision/tokenizer/template
   identity, context/output/tool/modality parity, and per-member qualified
   evidence. Equal display names or a health 200 are insufficient.
3. Bind each remote member to an expected node identity and network trust
   policy; endpoint failure and stale observations must remain visible.
4. Select only before dispatch using bounded per-member admission and declared
   weights/capacity. Define fairness and optional caller-explicit affinity.
   Never infer affinity or intent from prompt content.
5. Preserve streaming ownership and cancellation until terminal cleanup. No
   replay after dispatch, hidden cross-model fallback, or automatic cloud spend.
6. Extend request evidence with logical tier, selected member, configured
   membership generation, and reason enum; no endpoint or payload retention.

## Acceptance

Use synthetic loopback nodes to prove exact membership, identity mismatch,
stale/absent remote state, capacity races, all-unavailable behavior, no duplicate
dispatch, stream failure, cancellation, and admission release. Then separately
qualify the operator's exact private remote deployment and clients before
promotion. Do not report the current same-host work as cross-host support.
