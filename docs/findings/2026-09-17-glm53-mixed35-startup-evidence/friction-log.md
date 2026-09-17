# Friction and disposition

- **Host RAM exhaustion (open):** two tensor-parallel workers reached about 77 GiB combined resident memory while loading; all 8 GiB swap was consumed. The candidate had no container RAM/swap ceiling. It was unloaded and is excluded from further tests until a managed host-memory limit, desktop reserve, and bounded loader have independent evidence. Tracked in the repository ticket `2026-09-17-recipe-host-memory-containment`. This blocks retry; it does not establish a model defect.
- **Image-build directory mode (fixed):** default umask produced group-writable evidence directories. PR #519 creates them with mode 0700 and regression-checks rejection of unsafe existing directories.
- **Quiesce reason (resolved):** initial prose reason was rejected before mutation. The accepted request used the content-free code `candidate_test`; admission and drain records were retained privately.
- **Interrupted restoration (recovery):** desktop shutdown interrupted the foreground session. Recovery resumed from the pinned baseline recipe; post-run acceptance is recorded in restoration.json. A detached recovery supervisor remains a follow-up in the containment ticket.
