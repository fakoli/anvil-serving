# Make fleet summary composition linear in declared-node metadata

Status: source fix integrated; consolidated acceptance and deployment pending.

A synthetic build_fleet_workloads query with1000 declared nodes, no supplied
node results and no retained records took8.813 seconds locally. It violates
the intended five-second collection budget before any controller call exists.
The full test suite was running concurrently, so this is local regression
evidence rather than a portable performance benchmark.

The cause is structural: each new host rebuilds every previous node/source
summary through _reduce_node. The empty fleet path alone reconstructs roughly
three million source summaries. A global cap on records does not bound this
quadratic metadata work.

Replace repeated whole-prefix reconstruction with per-call lightweight source
metadata and one bounded global selection. Eviction updates only the affected
source's selected records and omission count. Construct immutable canonical
source/node summaries once at finalization; no raw full-node/source references
may keep discarded records alive. Retain at most the global record cap plus
one current normalized node and bounded selection bookkeeping. This is an
ephemeral pure composition buffer, not a persistent workload registry.

Add a deterministic SourceResult construction-count regression demonstrating
linear metadata work, plus existing global tie/eviction/omission cases. Re-run
the same1000-node empty probe and record its measured result without claiming
remote deadline or deployment acceptance.

Candidate 7986e1c1 replaces prefix reconstruction with a bounded worst-first
heap and lightweight per-source buffers. A deterministic regression failed
before the fix at 3,045,012 SourceResult constructions for 1,000 empty nodes;
the fixed implementation passes the ceiling of 64 constructions per node.
The same local empty-fleet probe took 0.138 seconds after the fix, versus
8.813 seconds in the earlier concurrent-run probe. These are local timings,
not a hardware-matched benchmark or remote deadline qualification.

Postcommit proof EVCE315F7D records 102 focused tests passing and Ruff passing.
Repeated eviction, cross-host identical-ID ties, source-relative order,
unknown/overflow omissions and receipt-clock regressions remain covered.
