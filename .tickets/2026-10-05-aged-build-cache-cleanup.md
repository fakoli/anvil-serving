# Aged build-cache cleanup

Status: implemented and independently reviewed; focused tests and live cleanup passed.

Model-cache inventory exposed BuildKit last-use dates, but no managed verb
could remove old build cache. Model filesystem timestamps and image creation
dates cannot establish last use, so broad model/image/volume cleanup is unsafe.

Added `host docker-build-cache inventory|prune`: explicit context, Docker-driver
builder and old cutoff; unknown-use retention; repeat inspection; exact-ID,
age filters and BuildKit's native locked live-reference guard; confirmation and
postinspection. BuildKit's `inuse` field is presence-only: do not send the string
comparison `inuse=false`, which matches no records. The first live attempt
demonstrated this safe no-op; the corrected command retains native protection.
Daemon-reported reclaimed storage remains distinct from rounded size estimates
and physical VHDX allocation. No broad prune or model-volume deletion is added.

Verification: 150 focused tests passed, 8 skipped; Ruff passed. A live exact-ID
prune removed nine unused old cache records while image, container, model-cache,
and volume identities stayed unchanged; both protected services remained ready.
Unsanitized operator evidence is retained outside this public repository.

Follow-up inventory gap: Docker 29.8 reports volume rows under `Volumes`, while
model-cache inventory reads `LocalVolumes`. Until handled, do not interpret an
empty managed volume list as absence of volumes. Cleanup here does not delete
volumes; a narrow read-only volume-name comparison independently verified them.
