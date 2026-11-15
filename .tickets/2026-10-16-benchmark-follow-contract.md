# Make benchmark follow flags honor their advertised contract

Date: 2026-10-16
Status: open; campaign uses detached submit and explicit terminal-status checks

The shared context/agentic/SWE submit parser accepts `--follow`, but local
`benchmarking/jobs_cli.py` and controller `control_plane/mcp/tools/benchmarks.py`
only echo the flag in the acknowledgement. Both launch a detached worker and
return before completion. The command catalog advertises following the job;
`logs --follow` similarly performs only one log read.

A retained agentic run returned a queued acknowledgement with `follow=true`.
The immediate artifact lookup correctly returned `artifact_pending`; a later
terminal status and artifact read succeeded without resubmitting. This is a
follow-contract gap, not a benchmark or artifact-store failure.

Until repaired, submit with `--detach`, inspect the existing job's status, and
collect its artifact only after a terminal state. Never treat acknowledgement
as completion or resubmit to obtain a result.

Define bounded wait, interruption, timeout, and terminal-failure behavior once
for local and controller clients. Preserve durable jobs when callers detach or
time out. Cover completed, failed, and timed-out submit-follow cases, and define
incremental log-follow separately. Do not repair this through an operational
polling script or change existing detached submission behavior.

Deferred from the model campaign: explicit status/artifact commands already
provide the required completion proof; implementing follow behavior changes a
separate client lifecycle contract.
