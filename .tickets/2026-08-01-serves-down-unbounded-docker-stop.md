# Serves down can block forever on Docker Desktop stop

Status: fixed locally

## Symptom

The final `serves mode leave` for the Inkling TP=2 campaign entered
`cmd_down()` and remained blocked in `docker stop` for several minutes. The
container stayed running and exclusive GPU ownership remained internally
consistent, but the mode transaction had no product-level timeout or recovery
path.

## Root cause

`cmd_down()` called the Docker CLI without a subprocess timeout. Docker's
in-container stop grace period does not bound a Docker Desktop client or daemon
request that itself stops responding.

## Fix

Bound the Docker stop client call to 45 seconds. For the normal remove-by-default
contract, a timeout proceeds to an independently bounded `docker rm -f`, which
is the same final lifecycle state requested by `down`. With
`--keep-container`, fail closed and preserve the container for diagnostics
instead of force-removing it. Exclusive-mode leave uses the narrower
`force_remove` path directly: the mode contract has already committed to
removing the experiment owner, and bypassing a graceful stop prevents Docker
Desktop from trapping the whole mode transaction before split restoration.

## Verification

Unit coverage must prove both timeout branches. The live exclusive-mode leave
must then remove the stuck Inkling owner through the managed surface, restore
the saved `pre-campaign` split group, and report split mode with no unresolved
GPU owner.

Verified: timeout and keep-container branches plus direct mode-release ordering
are covered by unit tests. The final managed live retry force-removed Inkling,
restored healthy Omni, and exited 0; independent status reported split mode and
no unresolved owner.
