# Connector resource extension has no managed command sequence

## Problem

Adding a resource to an already-enrolled Anvil Connect connector cannot be
completed by the managed `anvil-connect-ctl` surface alone. Three contracts
interact into a deadlock:

1. The deployment manifest is validated as a closed whole: every gateway
   resource needs a connector backing, so the gateway section cannot learn a
   new hostname before the connector section declares it.
2. The activation transaction swaps the rendered tree atomically and refuses a
   partial activation when an unselected target's rendered file changes
   (`activation would change an unselected target`). A gateway-only activation
   is therefore impossible whenever a connector's rendered config changes.
3. A connector process refuses to start when its enrolled local state lists a
   different resource set than its declaration
   (`sameResources(state.Resources, resourceIDs(declaration))`), and
   re-enrollment (`init --bundle`) requires an invitation whose resources the
   running gateway must already assign — which requires the new generation to
   be active first.

The all-targets transaction then always fails its startup-stabilization gate
(`managed units did not stabilize after startup`) because the connector
crash-loops with the new declaration until it is re-enrolled, and the failure
rolls the whole generation back.

## Observed during

Pi Web Connect enablement on fakoli-dark (2026-09-11), adding the `pi-web`
browser resource to the `dashboard` connector. Four managed attempts rolled
back for this reason; the classified error codes
(`connect_operation_failed`, `connect_operation_partial`) hid the actual gate
each time, and only direct module invocation under the pinned release exposed
the real messages.

## Resolution

Worked around with a narrow operator bridge that reuses the pinned release's
own library primitives: stage the new generation, swap the rendered tree,
restart only the gateway units, write the reviewed activation record, then run
the documented enrollment sequence (native admin revoke/invite, managed
connector `init --bundle`, managed `identity`, native admin fingerprint
approval, managed connector-only `up`). Transient invitation material lives
only in role-owned 0700 state directories (the connector `init --bundle`
reader requires exactly that contract; `/tmp` is rejected).

Two further operator-visible contract details worth surfacing in product docs:
the connector `init --bundle` parent-directory requirement, and the fact that
`installation-revoke` is a prerequisite for re-inviting an active installation
in the same epoch.

## Suggested product change

Add a managed resource-extension flow, for example
`connect connector extend --manifest ... --service connector:<id>` that
performs: closed-manifest validation, gateway-scoped generation activation,
native admin invitation, connector state reset plus `init --bundle` redemption,
identity fingerprint approval, and the connector-only activation — with the
same rollback and evidence gates as the existing transaction.

## Verification

Pi Web enablement completed through the bridge plus managed commands on
2026-09-11; connector identity reports `enrolled` with the extended resource
set and the new resource's reverse tunnel and origin listener are live. The
public Cloudflare hostname record and tunnel ingress for the new resource host
remain operator-gated steps outside this repository.