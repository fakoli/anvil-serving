# Preserve the legacy writer contract when optional replica fields are absent

Status: implemented candidate 659355f6 in workload-visibility:T008; final batch acceptance pending.

The broader router run after candidate 10b49e4c passed 763 tests before failing
test_ordinary_writer_shape_is_unchanged_when_workload_fields_are_absent. That
fixture derives the expected public JSON keys from dataclasses.asdict(record),
subtracting only workload fields. The new optional replica metadata is correctly
omitted, so the fixture incorrectly expects two new null keys on direct records.

Update the expected legacy key contract explicitly rather than deriving it from
every future dataclass field. Preserve assertions for all existing writer fields
and for omission of both optional metadata families. Keep new replica metadata
tests and rerun the router integration gate. No runtime compatibility workaround
or broadened JSON payload is needed.

The explicit legacy field fixture and 118 focused lifecycle/streaming tests
pass at 659355f6. The broader router gate remains part of batch integration.
