# Validate recipe runtime observations before reconciliation and truncation

Status: implemented candidate; consolidated acceptance pending.

serve_recipes.py::list_recipe_workloads collects runtime configuration digests
before validating the corresponding records. An invalid or future-dated
runtime observation can therefore suppress a valid configured observation.
The same function reports COMPLETE after query truncation while recording a
positive omitted count; the canonical SourceResult validator rejects that
combination. Existing focused tests do not exercise either boundary.

Add a bounded T004.2 correction with literal regressions: only validated runtime
rows may suppress a matching configured row, valid independent components
survive bad peers, and normal query truncation produces PARTIAL with an exact
omission count and no invented source error. Apply filtering before the limit.
Keep malformed or future source data distinct from ordinary result truncation.

Use the canonical workload validators and IDs. Do not add probes, new runtime
owners, lifecycle actions, identity assertions, or a broad projection refactor.

Candidate 6d32501e764739130accd178d2b7e2e0be24098d implements T004.2.
Both defects were reproduced against predecessor code. Post-commit evidence
EV4367830D passed 175 focused tests and Ruff. The integrated batch at
58686710e392fbade7807f79492e4c9f6858df9c passed 6,447 tests, with 10 skipped.
This is source/regression evidence, not final task acceptance or deployment.
