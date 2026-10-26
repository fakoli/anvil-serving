# Recipe logs must retain stderr-only container output

## Problem

`models recipes logs` can report success with empty command data when a managed
container writes its startup failure to stderr. The command runner then exposes
the captured lines as warnings, while the data field remains empty. This obscures
the reason an exited recipe container failed to start.

The observed Flare startup diagnostic was `error: unexpected argument
'--verbose' found`. A bounded read-only fallback captured the same text on
stderr, confirming that the container emitted the diagnostic and that the
managed log projection failed to put it in its normal output stream.

## Resolution

- Capture recipe logs through the existing bounded capture path, preserving the
  configured byte limit.
- For successful `docker logs` calls, project stdout and stderr container log
  text into the command's data stream and redact recognized secret patterns.
- For nonzero Docker command results, retain the exact nonzero status and keep
  the sanitized diagnostic on stderr.
- Cover registered and discovered recipe paths with stderr-only fixtures and
  verify that Docker failures remain failures.

## Verification

Focused regression selection: 7 passed, 221 deselected. Ruff checks passed for
the changed Python modules, and `git diff --check` passed. No live container or
model request was used.
