# Product journey JSON changes a shared schema field type

**Status:** Resolved 2026-08-30

## Problem

`product families --json` and `product journey FAMILY --json` both declared
`anvil-serving.product-families/v1`, but the catalog represented `umbrella` as
an object while the journey represented it as a string. A client branching on
the shared schema version therefore needed an undocumented endpoint-specific
type exception.

An independent GPT-5.5/xhigh adversarial re-review found the defect on the
`1.0.0` candidate at revision
`481057c04d0d80a5eac2752e10078c4e54e866cb`.

## Acceptance

- Both responses keep the same schema identifier and the same `umbrella`
  object shape.
- The object preserves its name, promise, and authority boundary.
- A regression compares the catalog and journey representations directly.

## Resolution

Both response builders now use one bounded umbrella-object builder, and the
regression requires exact equality under the shared schema version.
