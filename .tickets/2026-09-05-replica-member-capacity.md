# Atomic replica member capacity and independent drain scopes

Status: implemented source candidate; scheduler wiring and consolidated acceptance pending.

Capacity scheduling needs one reservation boundary: separate member and tier
checks could oversubscribe a selected endpoint, and a tier-only drain wakeup
would leave member waiters blocked after that member reaches zero.

Scheduler T001 adds strict replica strategy/member-ceiling configuration,
aggregate and member ceilings under the existing tier condition, immutable
snapshots, independent member quiesce/readmit/drain, and exactly-once compound
release. Direct-tier admission and its five-field public snapshot stay intact.
The caller must pass replica-only ceiling mappings; routing construction and
capacity ranking follow in T002-T004, so this is not an enabled scheduler claim.

The scheduler boundary is now closed without a second state owner. T002 adds
only immutable scheduler values, a pure pressure normalizer/rank function, and
their integration into the existing `TierAdmission` condition and cursor. A
capacity lease carries its immutable decision; legacy round robin carries no
decision. Readiness and pressure inputs are copied and validated before the
condition, and selection, count increments, and cursor advancement remain one
atomic admission operation with no I/O or caller callback under the lock.

Normalized request pressure uses ceiling parts per million for
`(running + waiting) / configured_member_ceiling` and deliberately remains
above one when backlog exceeds capacity, up to 2000000000000000 ppm. KV
pressure is bounded to 0-1000000 ppm. T003 must attach monotonic observation
time to its existing running/waiting/KV snapshot; no engine-specific capacity
metric is inferred. T004 must pass strategies and ceilings through both
admission construction sites and must not wrap replica members in the separate
backend concurrency limiter. These are pending implementation contracts, not
claims that capacity scheduling is routed or deployed.

Freshness evidence remains four-state: fresh, stale, failed, or unknown. Every
non-fresh state is equally conservative in ranking, but the selected score
retains the source class so the later capacity projection can distinguish
expiry, collection failure, and malformed or absent evidence.

Implementation 4180f1bb passed 163 focused config/admission tests and Ruff after
commit, recorded as EV91B9A27F. Twenty concurrent attempts are bounded with and
without an aggregate ceiling; event-driven tests cover member/tier drains and
quiesce races. The old unknown-field fixture used max_concurrency, now a valid
member field; it now uses an actually unknown key and retains the rejection gate.

T002 implementation 1cb94e56 passed 163 focused scheduler/admission tests and
Ruff after commit, recorded as EV91673477. Exact-ratio ranking, pressure signal
validation, freshness, rotating ties and atomic capacity reservations are now
implemented candidates; routing construction remains T004.

T003 closes the request-latency gap in an inline metrics refresh: use a fixed
two-worker cache with at most 256 registered member keys, one queued/running
refresh per key, nonblocking reads and shutdown, one-second refresh/deadline,
and five-second freshness. A slow/trickling collector cannot be forcibly
cancelled by Python threads, so overdue and late samples fail conservatively
and workers never grow to compensate. This is an explicit bounded degradation
contract, not a claim that a socket timeout is an absolute body deadline.

No live route, model, or deployment was changed. Final batch acceptance remains
open, and these synthetic tests are not routed hardware qualification.

During T004 call-site preparation, candidate f4f94f26 (123 focused tests passing)
still allowed an upstream scheduler_capacity field to replace the configured
ceiling, called the injected clock under its cache condition, and did not
consistently downgrade invalid/backward clocks to unknown. That candidate is
not accepted or enabled. T003 is fixing these explicit contract mismatches,
with separate regressions for configured authority, clock callback boundaries,
single-flight state independent of timestamps, and malformed-vs-failed samples.
