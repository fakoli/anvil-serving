# Validate remote workload timestamps against the receipt clock

Status: reproduced on candidate e66bfcc6; fix-forward required before fleet
reader integration or final acceptance.

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
