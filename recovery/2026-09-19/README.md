# Inactive work recovery — 2026-09-19

These are unintegrated source patches recovered from older development
worktrees. They preserve missing work for review without replacing current
implementations or importing the old Git history. Nothing in this directory
is packaged, imported, or executed by Anvil Serving.

| Recovery group | Purpose | Integration work still required |
| --- | --- | --- |
| `derived-image-builds/` | Recipe-owned pinned image builds, context validation, provenance checks, CLI wiring, and regression tests. | Reconcile with the current image-build and recipe interfaces; run the included tests and applicable command gates. |
| `docker-recovery-and-graceful-unload/` | Offline Docker disk copy/adoption, socket recovery, volume inventory compatibility, graceful candidate removal, and tests. | Review Windows filesystem/lifecycle safety and reconcile with current host commands before execution. Historical verification claims in the recovered tickets are source records, not fresh acceptance. |
| `retained-recipe-lifecycle-and-windows-pi/` | Explicit retained-container stop/start, bounded deadlines and admission checks, MCP wiring, Windows subprocess-pipe reads, and tests. | Split the lifecycle and Pi changes into focused implementation changes; independently review admission and platform behavior. |

The [manifest](manifest.json) records each original source path, source HEAD,
source-file SHA-256, and patch SHA-256. Every patch was checked using an isolated
Git index populated from its recorded source HEAD. All 39 apply to those
original bases. Python source parses successfully. This does **not** establish
that the patches apply to current `main`, work together, or pass runtime tests.
See [validation](validation.json). Some old base commits have rewritten
counterparts; the retained patch contains the old and new lines needed for
manual integration even when a fresh clone lacks the old commit.

Current media backend binding and controller-auth-file handling already
supersede the older local versions. The current agent role definitions also
replace the older generated copies. Those files were not reverted. Generated
CLI inventories should be regenerated after integration rather than restored
from old snapshots.

Historical Qwen benchmark receipts and operator deployment/configuration
snapshots are retained in the private operator repository under
`evidence/recovery-20260919/`. They are not new benchmark
publications, live configuration, or promotion evidence. That private record
also contains the per-file disposition audit.

Only disposable root `.codex-scan-*` directories are newly ignored publicly.
Source, tests, tickets, agent definitions, and benchmark evidence remain
eligible for tracking. The separate recovered Docker Desktop per-user
installation ticket remains open; this recovery does not claim to fix it.
