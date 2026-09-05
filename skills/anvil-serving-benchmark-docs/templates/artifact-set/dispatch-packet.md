# Campaign dispatch packet

Use one packet for one bounded assignment. Do not copy the full parent-session
history into the assignment.

- **Campaign ID:** `YYYY-MM-DD-SLUG`
- **Task ID:** `PORTABLE-TASK-ID`
- **Stage and gate:** RESEARCH, FEASIBILITY, SCOUT, FINALIST, QUALITY,
  RESTORATION, OR PUBLICATION; NAME THE ADVANCE GATE
- **Objective:** ONE VERIFIABLE OUTCOME
- **Owned outputs:** REPOSITORY-RELATIVE PATHS OR READ-ONLY
- **Authoritative inputs:** REPOSITORY-RELATIVE PATHS, SCHEMA, AND SHA-256 WHEN
  RETAINED
- **Authority:** READ-ONLY, REPOSITORY-WRITE, OR EXPLICIT LIVE SCOPE; STATE
  WHETHER SERVICE, ROUTE, CACHE, OR NETWORK MUTATION IS FORBIDDEN
- **Stop conditions:** CORRECTNESS FAILURE, RESOURCE FAILURE, IDENTITY DRIFT,
  OR OTHER BOUNDED STOP RULE
- **Verification:** EXACT INDEPENDENT COMMAND OR ARTIFACT CHECK
- **Return contract:** STATUS; BOUNDED FACTS; EVIDENCE PATHS; FAILURE AND
  FIX-FORWARD; VERIFICATION RESULT

Use a no-history or small-window fork when the agent platform supports it and
the packet plus referenced artifacts are sufficient. Return paths and concise
facts rather than copying raw logs, prompts, responses, or large command
outputs.
