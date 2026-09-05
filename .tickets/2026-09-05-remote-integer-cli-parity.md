# Preserve strict integer spelling across local and controller CLI dispatch

Status: open; required before diagnostic feature release.

## Reproduction

The bounded local controller logs parser refuses `+1`, `1_0`, leading-space
` 1`, and non-ASCII decimal digits. After MCP wiring, the generic
`cli.py::_remote_scalar` calls `int(value)` before MCP schema validation.
The same four spellings therefore become valid integers through remote
`_remote_arguments`, bypassing the lexical refusal without violating the
numeric range check. A read-only in-memory reproduction confirmed all four;
no child, controller, or network call was needed.

## Fix contract

Validate remote integer text before conversion: an optional ASCII minus sign
followed by one or more ASCII digits. Positive-only ranges still refuse
negative values through their existing schema bounds. Preserve leading zeros
where the existing local CLI accepts them. Reject plus, whitespace,
underscores and Unicode digits with a fixed error before transport creation.
Do not change floating-point or string option behavior.

Add real command-dispatch tests with fake resolution/transport/diagnostics,
including accepted default/1/200 and the four malformed spellings. Keep the
fix behind the durable CLI, not a one-off operational workaround. This
cross-command lexical correction needs a scoped PRD task and existing remote
CLI regressions before diagnostic feature closure.
