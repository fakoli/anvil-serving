# Keep fleet workload surfaces behind scoped controller authority

Status: controller, schema and client candidates integrated; final acceptance pending.

The workload PRD requires workloads:read for unified operator surfaces.
The existing local static MCP catalog has no authenticated caller boundary:
its generic dispatcher does not enforce requiredScope. Registering workload
collectors there would create an unscoped alternate path. The old Python
proxy also attaches generic target context that is outside the workload
disclosure contract.

Deliver node/fleet workload tools through the authenticated controller /mcp
and the modern bridge's dynamic controller tool declarations. Keep static
local and legacy-proxy workload exposure absent in v1. Add the sealed fleet
REST/MCP operation, a separate explicit fleet-topology startup option, and
scope/context/lifecycle regressions under workload-visibility T014.1/T014.2.
CLI and dashboard must query scoped HTTP authority, not directly collect
local fleet inventory. This preserves their canonical metadata view without
granting an alternate local collector path.

Node declarations currently omit required/maxProperties even though the
canonical MCP object-schema idiom requires both. Share the exact seven-field
schema with required=[] and maxProperties=7 in the same production slice;
update the exact node fixture rather than silently forking declarations.

No live credential, endpoint, deployment or approval is implied by this
contract. Consolidated acceptance and real client evidence remain pending.

Source candidates: shared declarations `6ccf10f2` (`EV217C4A50`, 79 tests),
sealed fleet REST/MCP `3a0840c0` (`EV40834EDE`, 130 tests), and separate
startup option `5002dce6` (`EV98A232A8`, 452 tests). The static MCP catalog
was not expanded. Canonical fleet client `30d054c1` (`EVE1C7C31B`, 95 tests),
dedicated MCP/chaining regressions `0398c5a9` (`EV2269709C`, 105 tests), and
scoped CLI `e8441649` (`EVB5A79A49`, 441 tests) are locally integrated.
Dashboard authority service `0e4a9f6d` (`EV91B07611`, 150 tests) and reserved
observability route `2406fee0` (`EV12955494`, 123 tests) are also integrated;
dashboard assets/startup remain in progress. These are scoped postcommit
gate counts, not a total or a deployment/acceptance claim.
