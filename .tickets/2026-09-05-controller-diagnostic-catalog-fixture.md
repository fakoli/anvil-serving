# Preserve exact MCP catalog coverage after adding diagnostics

Status: open; controller-diagnostics:T011 prerequisite to final source gates.

The full suite stopped with 3111 passed and 7 skipped at the public MCP catalog
characterization: runtime discovery contains controller_inspect/controller_logs
after operation_contracts, while the frozen names and two hashes predate them.
Review the two exact specs and handlers, update their expectations, and add
readable closed-schema assertions. Keep exact order, hashes, existing tool
contracts and family order intact. Re-run the full suite through T006 afterward;
this fixture correction does not prove deployment or resolve endpoint access.
