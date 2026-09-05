# Keep snapshot validation private through the actual CLI dispatcher

Status: implementation open; qualified-replica-sets:T014 amended before dispatch.

The root CLI handles topology commands directly in `_dispatch`, bypassing the
focused module's `main` wrapper. Its existing JSON envelope also uses raw argv
as a command label outside protected router/edge families. A module-only
validation implementation would therefore miss the requested human rendering
and could expose private config/topology paths through the outer envelope.

T014 now includes `cli.py` and explicit actual-entrypoint regressions. The
nine-field validation object stays in the standard envelope's `data` on both
success and refusal; command is operand-free, context null, warnings empty,
and errors fixed. Human rendering uses one safe line. Parser refusals must
never echo private operands or call the validator. Other topology commands
retain their existing output behavior.

Source inspection established this boundary before implementation; no live
config or private path was sent to a controller. Completion requires actual
`cli.main` tests, not an independently shaped module-only fixture.
