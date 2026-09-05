# Construct replica backends without an empty endpoint

Status: open; qualified-replica-sets:T007 owns construction, T008 owns dispatch.

A bounded offline probe of the accepted configuration and current backend
builder confirmed that a replica tier constructs one RelayBackend whose
base_url is the internal empty sentinel. A dispatched request could therefore
reach a URL builder without a real selected member. No live request was made.

Build one immutable member aggregate at the existing logical-tier map key.
Each member uses the existing relay builder with its validated direct endpoint
view and shared tier policy. Generation requires an explicitly selected member;
the aggregate must never select, retry, or substitute on its own.

The runtime preflight also identified two existing authoritative owners that
must not be duplicated: HttpHealthAvailability already caches composite
tier/member identities, and the outer concurrency wrapper owns the one tier
semaphore. T007 reuses those owners rather than allocating a cache or a new
concurrency allowance for each replica. Event-controlled tests must prove the
same semaphore covers different member IDs and releases on terminal paths.

Independent design review also confirmed that RoutingBackend reads structured
results from the logical backend after drain. The aggregate must delegate that
read to the selected adapter through the existing thread-local idiom; otherwise
tool calls, finish reasons and upstream usage would disappear while text still
appears to succeed. Tests cover both the aggregate and its concurrency wrapper.

This ticket is source implementation work only. A built aggregate is neither
live qualification nor a deployed route. T008 and streaming acceptance remain
separate gates, as do model promotion and coordinated release verification.
