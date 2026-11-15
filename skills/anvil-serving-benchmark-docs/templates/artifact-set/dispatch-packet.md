# Campaign dispatch packet

Use one packet for one bounded assignment. Do not copy the full parent-session
history into the assignment.

- **Campaign ID:** `YYYY-MM-DD-SLUG`
- **Task ID:** `PORTABLE-TASK-ID`
- **Stage and gate:** RESEARCH, FEASIBILITY, SCOUT, FINALIST, QUALITY,
  RESTORATION, OR PUBLICATION; NAME THE ADVANCE GATE
- **Objective:** ONE VERIFIABLE OUTCOME
- **Frozen comparison matrix:** PROFILE × WORKLOAD × CONTEXT × REQUESTED CONCURRENCY;
  list required metric families or state that this is a focused smoke/quality-only check
- **Metric coverage:** For every required metric, return `measured`, `configured`,
  `estimated`, `blocked`, or `not_collected` plus artifact or disposition
- **Owned outputs:** REPOSITORY-RELATIVE PATHS OR READ-ONLY
- **Authoritative inputs:** REPOSITORY-RELATIVE PATHS, SCHEMA, AND SHA-256 WHEN
  RETAINED
- **Authority:** READ-ONLY, REPOSITORY-WRITE, OR EXPLICIT LIVE SCOPE; STATE
  WHETHER SERVICE, ROUTE, CACHE, OR NETWORK MUTATION IS FORBIDDEN
- **Stop conditions:** PAUSE UNSAFE WORK ON INFRASTRUCTURE, RESOURCE, OOM,
  ADMISSION, OR IDENTITY FAILURE; PRESERVE THE CELL AND ITS LIMIT. RETAIN AN
  ORDINARY QUALITY/CORRECTNESS MISS SEPARATELY AND COMPLETE ONLY REMAINING SAFE
  DIAGNOSTIC OR PERFORMANCE CELLS. RETURN EVIDENCE AND NEXT HYPOTHESES. THE
  CAMPAIGN OWNER CONTINUES RESEARCHED CONFIGURATION TRIALS WITHIN AUTHORITY AND
  BUDGET; STATE THE EVIDENCED CANDIDATE-REJECTION OR UNRESOLVED CONDITION
  SEPARATELY
- **Verification:** EXACT INDEPENDENT COMMAND OR ARTIFACT CHECK
- **Return contract:** STATUS; BOUNDED FACTS; EVIDENCE PATHS; FAILURE AND
  FIX-FORWARD; VERIFICATION RESULT

Use a no-history or small-window fork when the agent platform supports it and
the packet plus referenced artifacts are sufficient. Return paths and concise
facts rather than copying raw logs, prompts, responses, or large command
outputs.
