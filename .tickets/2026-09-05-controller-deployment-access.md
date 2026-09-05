# Establish controller identity and access before coordinated deployment

Status: open
Priority: P1 deployment gate
Date: 2026-09-05

## Observation

Read-only deployment preflight received HTTP 401 from the default loopback
controller status target and HTTP 403 from both declared controller transports.
The required credential is present in the current process environment; checking
the user-local and selected operator-home credential files did not supply a
replacement. This is not established to be a missing-credential problem.
A retry with the existing no-proxy/no-redirect transport returned the same 403,
so proxy interference is not an established root cause either.

These observations do not establish the responding service's expected node
identity, build version, authorization policy, or controller/router parity.
CLI version agreement and router reachability alone do not prove those facts.
Raw identities, endpoints, credentials, and private evidence remain outside the
public repository.

## Required fix-forward investigation

1. Obtain the authoritative managed service status and bounded startup/request
   logs through the product's existing operations surfaces.
2. Distinguish the expected controller from another listener or intermediary;
   distinguish invalid authentication from authorization or origin rejection.
3. Record the root cause and implement any product defect through supported
   CLI/controller surfaces with regression tests. Do not bypass a denial by
   silently broadening authority or weakening authentication.
4. Re-run the original status probe and verify exact node/build identity.
5. Before coordinated deployment, establish manifest-derived mount closure,
   same-selected-model recovery, endpoint version parity, and real-client
   acceptance. Do not represent a merge or a successful health response as
   deployment completion.

## Closure evidence

Pending: root cause, durable correction if needed, regression results, and
successful identity-checked status/deployment verification. No live route,
model serve, controller, or private configuration has been changed for this
investigation.
