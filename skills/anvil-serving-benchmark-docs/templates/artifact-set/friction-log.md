# Campaign friction log

Record entries while the campaign is running. Preserve the earliest actionable
failure and distinguish configuration issues, product defects, and model
behavior. For each configuration trial, link its version and parent, hypothesis
and dated source, intended/effective setting delta, targeted and regression
results, final qualification evidence, and next action or stopping reason.
A stopped cell is not a rejected model; budget-limited investigation stays
unresolved. Use the qualification skill's configuration-search workflow.

| Time | Stage | Category | Earliest actionable evidence | Immediate disposition | Durable fix-forward artifact | Independent verification | Status |
|---|---|---|---|---|---|---|---|
| YYYY-MM-DDTHH:MM:SSZ | RESEARCH, FEASIBILITY, SCOUT, FINALIST, QUALITY, RESTORATION, OR PUBLICATION | manual-workaround, ambiguous-output, missing-identity, unsafe-default, repeated-command, or failure | BOUNDED SYMPTOM OR ERROR; LINK RAW EVIDENCE INSTEAD OF COPYING LARGE OUTPUT | PAUSE CELL, DIAGNOSE, RESEARCH, CONFIGURATION TRIAL, REJECT WITH EVIDENCE, RESTORE, OR NONE | TICKET, CLI, TEST, RECIPE, SKILL, UPSTREAM WATCH, OR EXPLICITLY DEFERRED REASON | COMMAND OR RETAINED ARTIFACT | open or closed |

For skill/process lessons, also link the baseline and candidate instruction
identity, applicability limits, development and independent-case results,
reviewer, observed cost, accepted/rejected/unresolved decision, and expiry
trigger. A reflection note alone is not an independently validated lesson.

If there was no friction, retain the file with one sentence stating that no
manual workaround, ambiguity, missing identity, unsafe default, repeated
command, or actionable failure was observed. A retry closes the incident only
when a durable disposition and its independent verification are recorded.
