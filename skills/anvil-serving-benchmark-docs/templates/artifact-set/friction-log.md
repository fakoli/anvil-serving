# Campaign friction log

Record entries while the campaign is running. Preserve the earliest actionable
failure and distinguish product defects from model behavior.

| Time | Category | Command or stage | Observation | Workaround | Durable follow-up |
|---|---|---|---|---|---|
| YYYY-MM-DDTHH:MM:SSZ | manual-workaround, ambiguous-output, missing-identity, unsafe-default, repeated-command, or failure | STAGE | OBSERVATION | NONE OR EXACT WORKAROUND | NONE, TICKET, CLI, TEST, OR SKILL CHANGE |

If there was no friction, retain the file with one sentence stating that no
manual workaround, ambiguity, missing identity, unsafe default, repeated
command, or actionable failure was observed.
