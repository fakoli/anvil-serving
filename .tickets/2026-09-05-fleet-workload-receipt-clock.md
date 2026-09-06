# Validate remote workload timestamps against the receipt clock

Status: source fix integrated; consolidated acceptance and deployment pending.

A literal node-composition probe has a node timestamp29 seconds after the
collector's now and an empty COMPLETE controller source timestamp59 seconds
after now. The node schema permits their30-second difference, but the remote
receipt contract requires the source itself to be no more than30 seconds
ahead of the collector. normalize_node_workloads returned that source as
COMPLETE/error=None, preserving a59-second future offset.

Cause: the normalizer validates the node header against receipt now, then
passes the remote node collection time as the node builder's now. The two
separate skew allowances compound. That also applies the recent-work window
at the remote collection time rather than the trusted receipt time. A forged
naive node datetime is silently replaced by now in header validation instead
of being rejected.

Correction: normalize valid node time strictly, validate each source and query
through build_node_workloads using trusted receipt now, then reconstruct the
canonical node with its original valid node timestamp. A missing/rejected
source gets its fixed failure timestamp at that original node time; unchanged
source timestamps remain untouched. Do not rewrite observations to look fresh.
Add receipt-relative future, recency, malformed datetime and stale-node
healthy-peer regression cases. This is a routine source-wiring compatibility
correction; formal consolidated review and acceptance remain deferred.

Additional literal probes confirmed that a forged naive node timestamp is
accepted with a COMPLETE controller source, and a wrong-host node timestamped
one hour ahead escapes build_fleet_workloads as WorkloadError instead of an
isolated node failure. Invalid-header fallbacks must use trusted receipt time;
only a fully valid header can supply the preserved original node time.

Candidate a54c25c3 fixes receipt-relative skew/recency and invalid-header
fallbacks. The added regressions failed against the predecessor; postcommit
evidence EVCF77296C records 99 focused tests and Ruff passing. Fleet reader
and collector candidates include this correction before their claim-bound
proof. Source integration does not establish live acceptance.
