# Failure and friction dispositions

| Finding | Durable disposition | Verification / state |
|---|---|---|
| Cache admission chose an absent default helper before GPU load | Optional explicit `download.inspection_image` in managed recipes; trial uses immutable digest | Focused tests and independent source review passed; source retained, unreleased |
| Router rejected prose reason with HTTP400 | Use supported managed `operator` reason code | Managed quiesce/readmit passed; operational invariant |
| Auxiliary volume/package metadata missing from product surface | Tracked ticket `2026-10-21-recipe-auxiliary-volume-inventory` | Deferred; narrow read-only inventory and exact non-secret package-index stat/hash used; no raw lifecycle |
| r2 weight loading failed io_uring EPERM | Hash-bound profile adds only three required io_uring calls to pinned Moby default; per-container scope | r4 completed; recipe tests and independent review passed. Keep path immutable through launch; residual path-swap race remains |
| r3 compilation too slow at two workers | r4 explicitly uses eight total workers, four per rank, retaining completed caches | 511 operators,26 forwards completed; reviewed upstream ceiling; not pure cold preparation |
| Forward progress stayed0/26 until first future completed | Preserve upstream watch item: progress count is completed futures, not activity heartbeat | Worker activity and all26 completions verified; no-compile fallback deliberately unrun |
| Export took717.6seconds and reached52GiB cap | Preserve bounded memory limit and plan preparation separately from warm serving | max events7463; no observed OOM; warm restart21.555seconds with cache package,7/7 protocol |
| Strict output exceeded exact word target | Keep failures performance-ineligible; use natural-output scout only | Both arms0/4 eligible C1/C4; all response canaries passed |
| Unique namespace repeats across cells | Label C4 warm; future cold comparison must freeze distinct cross-cell seeds/namespaces | Both arms report100% hits in C4; no cold-C4 claim |
| Absent candidate status returns error envelope | Interpret exact no-discovered-container error alongside managed unload | False-negative closure check corrected; exact incumbent direct/routed restoration verified |

No skill instruction change was warranted: root causes belong in product options, recipe controls, benchmark design, or the deferred product ticket. See [process lesson](raw/process-lesson.json). Test receipts and independent review are retained separately from live quality claims.
