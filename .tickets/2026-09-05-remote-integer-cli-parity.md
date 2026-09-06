# Preserve strict integer spelling across local and controller CLI dispatch

Status: source fix accepted; diagnostic feature release remains separately gated.

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

## Resolution

Implementation `3b1a59afc3945e9d058bfa6e42e1701faada8924` enforces the
ASCII integer grammar generically. It also maps rejected MCP schema bounds to
a fixed CLI usage error: previously out-of-range values could escape as a
`ToolError`. The correction preserves string/float behavior and permitted
negative/leading-zero scalar spellings, and sends no operation request for
rejected input. Existing parser handling of separate negative option values
is unchanged; callers may use the existing inline option form.

Independent root review covered the scalar, remote argument, schema and CLI
error paths. The exact post-commit CLI/diagnostic gate passed 354 tests and
Ruff passed. Removing the integer guard reproduced the missing refusal in
the scalar regression. Anvil accepted `controller-diagnostics:T008` with
proof `controller-diagnostics-T008-E000461.json`. This is source/dispatch
evidence, not controller deployment or live diagnostic acceptance.
