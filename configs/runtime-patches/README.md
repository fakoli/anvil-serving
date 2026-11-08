# Pinned upstream patch series

Keep portable upstream changes under `<project>/<upstream-revision>-<purpose>/`.
Store the patch files, their application order and raw SHA-256 hashes, the exact
upstream commit and prerequisite layers, an application check, and focused tests.
Link the upstream repository in the series README. Do not vendor the upstream
codebase, model assets or deployment configuration to retain a fork.

Apply only to the declared source revision and prerequisite tree; reject drift.
An upstream upgrade gets a new reviewed series revision, intentional patch
rebasing and renewed checks. Preserve historical series and qualification pins.
Build settings and private image references belong in operator declarations.

The [TensorFold telemetry series](tensorfold/f350cd3e-anviltelemetryv1/README.md)
is a complete example. Its five patches depend on a separately pinned author
layer; they are not advertised as directly applicable to clean upstream source.
Community submission requires a separate review against the intended upstream
revision, with private deployment details excluded.
