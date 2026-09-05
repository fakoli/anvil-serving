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

Implementation 4180f1bb passed 163 focused config/admission tests and Ruff after
commit, recorded as EV91B9A27F. Twenty concurrent attempts are bounded with and
without an aggregate ceiling; event-driven tests cover member/tier drains and
quiesce races. The old unknown-field fixture used max_concurrency, now a valid
member field; it now uses an actually unknown key and retains the rejection gate.

No live route, model, or deployment was changed. Final batch acceptance remains
open, and these synthetic tests are not routed hardware qualification.
