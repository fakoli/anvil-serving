# Campaign friction log

Record entries while the campaign is running. Preserve the earliest actionable
failure and distinguish product defects from model behavior.

| Time | Stage | Category | Earliest actionable evidence | Immediate disposition | Durable fix-forward artifact | Independent verification | Status |
|---|---|---|---|---|---|---|---|
| YYYY-MM-DDTHH:MM:SSZ | RESEARCH, FEASIBILITY, SCOUT, FINALIST, QUALITY, RESTORATION, OR PUBLICATION | manual-workaround, ambiguous-output, missing-identity, unsafe-default, repeated-command, or failure | BOUNDED SYMPTOM OR ERROR; LINK RAW EVIDENCE INSTEAD OF COPYING LARGE OUTPUT | STOP, REJECT, RETRY, RESTORE, OR NONE | TICKET, CLI, TEST, RECIPE, SKILL, UPSTREAM WATCH, OR EXPLICITLY DEFERRED REASON | COMMAND OR RETAINED ARTIFACT | open or closed |

If there was no friction, retain the file with one sentence stating that no
manual workaround, ambiguity, missing identity, unsafe default, repeated
command, or actionable failure was observed. A retry closes the incident only
when a durable disposition and its independent verification are recorded.
