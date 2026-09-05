# Bootstrap plan identity contract

Status: implemented candidate; post-commit regression fix and consolidated acceptance pending.

Fleet enrollment T011 requires a closed distinction between its private plan
hash and safe public output. An implementation that hashed only the public
projection could miss install-root or receiver drift. Its protocol/catalog
expectations also need an authoritative source rather than caller overrides.

The Fleet PRD now binds all resolved private target fields, the immutable
manifest, policy, package protocol, and configured per-node operation allowlist.
The public projection omits paths and transports. The bounded catalog has its
own fixed encoder so a 256-operation contract does not weaken the generic
manifest/receipt JSON decoder's 128-node limit. T014 must reuse the catalog
identity only after validating the installed command catalog.

Implementation: T011, with exact target resolution fixtures, drift/shape/privacy
tests and existing bootstrap regressions. No live installation is authorized
by a plan, a source commit, or this ticket.

The exact-SHA gate at 140c1a3a failed in
test_bootstrap_plan_from_resolved_topology_has_canonical_private_identity:
BootstrapPlatform reached the strict canonical JSON helper as an enum instead
of an exact string. The hash projection must convert typed enums to their wire
values without relaxing the generic encoder. Keep this regression in the
required bootstrap test command and rerun after the fix-forward commit.
