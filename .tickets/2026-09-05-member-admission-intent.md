# Persist member quiesce intent without widening its scope

Status: implementation contract closed; batch acceptance pending.

The existing admission-intent writer records only quiesced tiers. A member
quiesce now reaches real admission through the CLI/controller, but a restart
would forget that member's intent. T010 adds the optional closed members map
and restores it independently of tier intent. The historical tier promotion
exception does not erase member promotion intent.

The existing fixed temporary filename and per-change callbacks also permit
concurrent writers or partial restoration to replace a newer snapshot. T010
serializes writes, takes one combined snapshot, uses an exclusive atomic
temporary file and suppresses writes until full restoration is complete.
Bounded input and strict new-field parsing reject malformed intent; valid
removed IDs are ignored. Tests use real admission and temporary files only.

No live service, route, model or operator intent is changed by this ticket.
