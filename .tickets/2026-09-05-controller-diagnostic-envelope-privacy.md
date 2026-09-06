# Protect the complete controller diagnostic CLI envelope

Status: resolved in source; deployment acceptance remains separate.

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

Independent T009 review found three additional regressions before acceptance:
the validator admitted common-only success objects without inspect/log fields;
comparisons before exact type checks allowed user-defined equality methods to
raise raw exception text, and non-ok counters accepted booleans/floats; a help
token after a literal argument separator bypassed captured parser refusal and
echoed an operand in human stderr. These were reproduced through the validator
and actual root CLI. Require complete kind-specific schemas, types checked
before operations, and fixed parser refusals on both human and JSON surfaces.
The existing green focused suite did not prove these boundaries; add explicit
regressions and rerun independent review before disposition.

Accepted controller-diagnostics:T009 at
`bef0fded85a9f910da1a41049643f0ba129cd00b`, evidence `EV81101F4A`, signed
proof `controller-diagnostics-T009-E000653.json`. All 374 post-commit CLI and
diagnostic tests, the five-file Ruff gate and diff checking passed. Independent
malformed-schema probes passed; bypassing the validator produced six expected
regression failures. The complete-envelope and separator-help negative
controls also failed as intended and were restored. The implementation is
integrated into the isolated delivery branch. This disposition claims source
acceptance only, not installation or controller access recovery.
