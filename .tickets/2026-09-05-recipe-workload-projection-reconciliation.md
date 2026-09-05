# Validate recipe runtime observations before reconciliation and truncation

Status: open; source inspection findings require regression reproduction.

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
