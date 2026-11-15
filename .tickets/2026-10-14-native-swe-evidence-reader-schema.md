# Read retained native SWE evidence schema

Status: open.

The native evidence reader rejected a retained SWE artifact schema. Manual
custody of the artifact is not reader acceptance.

Add explicit support for the retained schema while preserving attempt order,
empty attempts, grader state, hashes, and failure fields. Reject malformed
artifacts with actionable schema errors rather than flattening their records.

Acceptance: reader tests load the retained public-safe fixture, preserve its
attempt and terminal-state counts, and reject a malformed variant. Rendering
and summary code must distinguish empty, ungraded, graded, and resolved
attempts.
