# Managed promotion lock and deployed config mount

**Observed:** 2026-09-11. **Status:** fixed in the qualification branch; merge pending.

Promotion spawned a router-transition subprocess while holding the same cross-process
lock that the child required. The legacy installer also assumed a named config volume,
so a file-bind deployment could restart with unchanged configuration.

The transition now reuses its CLI handler in the owning thread. The installer resolves
only the deployed config path and matching mount, preserves metadata during replacement
and rollback, and fails closed for unsupported layouts. Tests cover the held lock,
false readmit, file bind, canonical no-command volume and failed-restart restoration.
A fresh managed promotion verified the exact routed identity and admission after both
failures were retained. Recipe status still omits restart count/OOM detail; a narrowly
scoped read-only state observation was used for that evidence.
