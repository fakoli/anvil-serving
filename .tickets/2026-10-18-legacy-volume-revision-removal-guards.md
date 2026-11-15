# Guard legacy Docker revision removal before reuse

Status: resolved by fail-closed retirement of legacy selective Docker deletion.

The legacy `models cache remove --volume ... --confirm` path invokes the
container helper's recursive snapshot deletion without the config, consumer,
lock, fingerprint, and timeout safeguards used by native and whole-volume
retirement. `models._volume_cache_report` also lacks a subprocess timeout.
An active or retained shared-volume revision can therefore be selected for
unguarded removal.

Confirmed legacy apply now refuses before any Docker operation, including when
confirmation is inherited from the dispatcher. The wrapper and embedded helper
both reject the removed destructive action. Preview remains available with a
bounded read-only bind to an existing daemon volume; unlike a named-volume
mount, a missing bind source fails rather than creating a new cache. Pull
preflight and verification retain report-only acquisition behavior.

Guarded native exact-revision removal, dedicated volume disposal and whole-cache
discard are the supported migration paths. Tests cover confirmation surfaces,
unchanged data on direct helper invocation, missing/replaced volume identities,
read-only mounts and timeout refusal. Whole-cache tests separately cover retained
containers, configurations, native consumers, locks, drift and uncertain outcomes.
