# Guard model-cache removal against active consumers

Status: open; bounded operator preconditions protected the completed cleanup.

`models cache remove` validates an exact repository revision and collects only
unreferenced snapshot blobs, but its apply path does not atomically check or
exclude a live container starting against that cache revision. A separate
precheck can race with another consumer.

The qualification cleanup verified rejected containers absent before apply,
kept the restored baseline on its separate cache bind, and verified target
absence, unrelated revisions and baseline health afterward. Active-use safety
was an operator precondition, not a product-enforced invariant.

Add an owner-controlled active-consumer guard and concurrency regression before
presenting exact-revision removal as safe against concurrent lifecycle changes.
Preserve the existing revision/path and unreferenced-blob checks.
