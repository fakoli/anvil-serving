# Router and fleet PRD implementation-contract audit

- Status: resolved (implementation contracts only)
- Scope: qualified-replica-sets, replica-capacity-scheduler,
  fleet-node-enrollment, workload-visibility
- Source: independent pre-implementation review of PR #469

## Confirmed reproduction

The original replica PRD required both `metadata_source = "upstream"` and
`model_identity = true`. `router/config.py::_parse_tier` rejects that
combination. Its configured identity probe checks the served model name;
`RuntimeModelMetadata` does not expose model revision, image digest, or
configuration fingerprint. The original contract therefore cannot be
implemented by simply composing the existing probes as its task text says.

The capacity PRD also needs a precise unknown-telemetry ordering contract:
local pressure is its primary score dimension, so a blanket assertion that
unknown telemetry never beats a fresh idle sample conflicts with lower local
pressure. Tests must specify whether that guarantee applies at equal local
pressure.

## Completion conditions

- Review all four sources against current code, recording concrete gaps.
- Close identity/provenance, scoring, bootstrap, and projection contracts.
- Synchronize public mirrors with canonical Anvil sources and reparse them.
- Independently review corrected contracts before approval and task planning.
- Preserve user-delegated approval provenance and actual implementation/test
  evidence; documentation approval alone does not complete feature tasks.

## Resolution

Independent router and fleet reviews closed the identity/provenance distinction,
exact score ordering, member-scoped transitions, bootstrap digest domains and
forced-receiver protocol, scoped authorization, and workload state/provenance
mappings. Corrected command and code breadcrumbs were checked against source.

The public sources match their dedicated delivery State partitions. All four
were reparsed, independently reviewed, and approved under the operator's explicit
autonomous delegation. Planning produced 58 tasks (13 replica, 13 scheduler,
16 enrollment, 16 workload), each with at most four likely files and no remaining
complexity expansion queue. This closes the contract audit, not the feature,
qualification, publication, or deployment gates. The unrelated historical planner
scope problem has its own mitigation ticket.
