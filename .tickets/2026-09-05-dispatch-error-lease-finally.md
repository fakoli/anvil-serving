# Release admission even when eager-error metadata fails

Status: open; qualified-replica-sets:T008 independent-review correction.

The eager backend-error handler records metadata before releasing admission.
A bounded injected regression combines a selected member's eager failure with
an ordinary metadata-recording failure; the counting lease records zero
releases. Although the ordinary decision sink is best-effort, its surrounding
clock, formatting or output path can still fail. Cleanup must not depend on
successful error reporting.

Put the existing lease release in a finally block around eager-error metadata
recording. Keep the single selected backend invocation, no peer retry and the
same direct-tier cleanup seam. Add a regression that forces both failures and
proves one release, zero peer calls, and no active aggregate/member remainder
with the real admission owner. Do not introduce a new exception-text log or a
second lease owner. Streaming cleanup remains covered separately by T009.
