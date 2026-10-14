# Attribute primary-tier quiesce and readmit transitions

Status: open.

A quiesced primary tier readmitted unexpectedly. The retained
`router-quiesce-drift-analysis.md` identifies no proven actor, so no root cause
or corrective action may be inferred from this observation.

Retain bounded transition audit records for quiesce, readmit, and rejected
transitions: declared owner, authenticated actor class, request or operation
identifier, prior and next state, timestamp, and reason. Redact credentials
and private endpoint details.

Acceptance: focused transition tests prove that every state-changing managed
path emits one attributable audit record, rejected attempts are separately
recorded, and records cannot be written without an actor class or operation
identifier. A fixture replay identifies the actor for a synthetic unexpected
readmit without exposing secret material.
