# Errata

The campaign plan said finish reasons would be retained. Successful
`anvil-serving.benchmark/v1` timing rows retain first-output, first-visible,
E2E, usage-derived token totals, chunk counts, and capture-truncation state.
They do not retain finish reasons, terminal form, raw usage objects, or visible
character count. The bundle therefore claims runner-validated visible output
and terminal observation only, never `stop` validation or semantic correctness.
