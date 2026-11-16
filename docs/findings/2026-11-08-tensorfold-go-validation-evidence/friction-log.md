# Failures and friction

1. **Repeated preparation remains open.** The first ordinary Go16 submission
   repeated an earlier standalone preparation into the same owner/run path.
   Its selected-oracle requalification hit `FileExistsError` at an existing
   starter directory before preflight or model transport. The worker exited,
   produced no request-event stream and left the observed engine completion
   counter unchanged. The failed job is retained in `setup-failure.json` and
   excluded from quality grades. The reviewed amendment uses fresh execution
   IDs without standalone preparation into those paths; source, oracle,
   controls and budgets remain unchanged. This avoids the collision and does
   not fix the [open product defect](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-11-08-aider-selected-oracle-prepare-submit.md).
2. **Tool setup failed before traffic.** An initial readback used an incorrect
   interpreter subdirectory. The installed runtime has no pytest; the existing
   development interpreter passed 46 focused fixtures against the clean pinned
   executing source. An initial offline preparation used the default cache and
   rejected the selected receipt; explicit existing cache/store selections
   corrected this without rebuilding or pulling an image. An unauthenticated
   metadata preflight reported missing credentials; authenticated metadata
   preflight passed. None generated model traffic.
3. **Grade and accounting labels need interpretation.** Go16's independent
   tests failed after `length` responses, while its whole-job budget remained
   unexhausted. Native Aider labels those responses as exhausted context windows
   and records zero task-token totals. Transport events instead retain actual
   usage well below configured serving context. Go32 records one passing coding
   attempt and two complete transport calls; call count is not attempt count,
   and the auxiliary call's precise purpose is not labeled by this event schema.
4. **Capture and comparison limits remain.** Process text is truncated,
   visible-token counts are unknown, warm cache is uncontrolled and historical
   direct checks differ from the routed path. Counter consistency is an
   observation rather than isolated performance attribution. Native preemption
   semantics and earlier benchmark coverage gaps are not qualified by this run.
5. **Project tracking unavailable.** The existing Anvil state engine supports
   schema 22 while the selected project database is schema 23. No competing
   project, downgrade or direct database edit was attempted. This is a tracking
   limitation, separate from the retained job evidence.
