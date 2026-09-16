# Missing cached verifier blocks media bundle staging

Observed on Windows Docker Desktop: `media bundle inventory` returned only
`media_bundle_inventory_failed`, exit 125. Bounded execution diagnostics showed
`No such image` for the exact curl helper pinned by the workflow lock.
`stage` called the same cache-only inventory before it could obtain the helper,
so the documented managed recovery path could not proceed.

The inventory now reports a missing helper with managed recovery guidance.
Staging preview discloses the exact helper pull and leaves existing asset state
unknown. Confirmed staging pulls that digest, repeats inventory, and still
refuses mismatches before any layout or model mutation. Inventory and previews
never pull. Regression tests cover those boundaries and a failed helper pull.
