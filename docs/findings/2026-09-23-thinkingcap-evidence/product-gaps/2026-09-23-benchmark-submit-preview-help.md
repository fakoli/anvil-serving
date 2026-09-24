# Benchmark submit advertises an unsupported preview flag

Status: open; help/parser consistency gap.

The operational CLI 1.2.1 advertises `--dry-run` for
`eval benchmark context submit`, but the actual parser rejects it as an
unrecognized argument before creating a job. Retained campaign evidence keeps
that failed preview separate from the later confirmed submission.

Align command help with supported behavior. Either implement a side-effect-free
preview that validates the exact job specification and execution owner or remove
the unsupported flag from this command's help. Keep `--confirm` on submission.

The current campaign used an independently reviewed, explicit job JSON and a
successful native preflight before its authorized `--confirm` submission. The
first topology refusal was separately resolved by a private campaign topology
declaring the local evaluation resource; deployed topology was unchanged.
