# Operator image-audit traversal scope

**Status:** Resolved in source; independent review and full-suite verification passed.

`host docker-image remove` scanned every JSON and YAML file below the operator
home. Dependency, cache, and evidence trees then made the exact-image audit
fail closed even though they cannot declare a live runtime or rollback.

The audit now skips dependency, cache, raw-run, and evidence trees but
continues to parse every supported TOML and JSON file. YAML with Compose
structure or name, a potential image key, or YAML escape, alias, or tag syntax
is validated through Docker Compose. Plain non-image YAML is scanned only for
immutable image identities, so ordinary settings files do not cause a false
failure. The native parser has a small aggregate process and time budget;
exhaustion fails closed. Nested recipes, workbench bootstrap, rollback, and
arbitrary candidate-stack declarations remain protected. Invalid, linked, and
unresolved image configuration still blocks removal.

The focused audit suite passed 33 tests. The full suite at commit
`a625569595bf0cd151561c52ec6b282fe90511ba` passed 10,368 tests with 62 skips.
The live removal preview still refused deletion when retained Compose files
contained unresolved image references, preserving the fail-closed boundary.
