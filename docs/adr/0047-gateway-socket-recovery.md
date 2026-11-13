# ADR-0047 — Recover gateway crash sockets under exclusive ownership

- **Status:** Accepted for implementation
- **Date:** 2026-11-13
- **Relates to:** [Anvil Connect](../ANVIL-CONNECT.md)

## Context

An ungraceful gateway exit can leave its Unix socket names behind. Managed
activation can recover stopped gateway sockets, but an autonomous supervisor
restart does not execute that manager operation. Blindly unlinking an existing
socket in a general listener would risk displacing another listener.

## Decision

The shipped gateway validates ingress directory custody before authority work,
then acquires the writable authority store's existing exclusive lock. With that
lock held, it preflights both the private administrative socket and the declared
isolated ingress socket. Only exact owned, single-link sockets with stable
identities and a bounded `ECONNREFUSED` probe are recoverable. Each leftover is
renamed, without replacement, into a new private directory on its own mount.
Both directories are synced. Failed recovery retains evidence; it never restores
over a new socket name. No generic drop-in or prestart command is introduced.

Listeners close before the store lock is released, including failed startup.
Ordinary listener functions keep their existing refusal behavior. An unavailable
public entry may still coexist with an explicitly configured healthy local
tunnel; recovery never changes that declared sibling policy.

## Consequences

Autonomous Linux restart and managed activation use the same ownership boundary
without changing authority databases, browser grants or service configuration.
Live, foreign, ambiguous or changed socket entries still require owner recovery.
Quarantined sockets are retained until an operator separately disposes of them;
this change does not authorize retiring external unit overrides or bypassing
managed upgrade and rollback admission.
