# Protect the complete controller diagnostic CLI envelope

Status: open; required before diagnostic feature completion.

The strict integer refusal works, but a root CLI JSON regression remains:
`controller logs --container synthetic-controller --tail 1_0 --json` exits two
and includes the rejected raw value in the envelope's `command` field. The
generic wrapper joins raw argv for controller commands. Focused human/parser
tests did not cover that outer disclosure boundary.

The live bounded inspect/log commands also return their canonical dictionaries
as JSON text inside the root envelope's `data`, rather than a structured value.
This is not a daemon failure; it is the generic print-wrapper integration seam.

Add a scoped follow-up task before final diagnostic documentation/acceptance:
return structured results through the existing CommandResult idiom, preserve
human/module compatibility, and protect the exact two diagnostic leaves in
the root wrapper. Command labels must be operand-free; contexts, errors,
warnings and data must not leak supplied paths, endpoints, invalid arguments,
or transport details. Do not broaden unrelated controller output behavior.
Tests must use actual cli.main for local and fake-controller dispatch and
include JSON success, parser refusal, schema refusal and transport errors.
No rejected argument may invoke diagnostics or a request transport.

Keep this gate independent of healthy container state and actual deployment.
The reproduced request used only a synthetic selector and malformed integer;
no child or live endpoint was required to establish the wrapper gap.
