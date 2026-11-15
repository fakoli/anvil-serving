# Managed Flare package preparation and trust-profile boundary

## Scope

`models recipes prepare` provides a guarded, non-serving path for the pinned
Flare qualification candidate. It acquires the exact immutable image and model
revision, retains the exact primary filename in the persistent Flare home
volume, applies an 8 GiB RAM / 8 GiB RAM-plus-swap bound, and runs native
`flare inspect --json` without GPU access.

## Acceptance boundary

A preparation receipt is emitted only when `flare inspect` verifies the signed
index and its returned publisher namespace and fingerprint match the recipe's
pins. The receipt explicitly labels payload/full digest coverage as unchecked
by inspection and loader validation as not run. The command never treats the
serving approval flags as verification keys and never changes remembered trust.

The pinned source commit has an empty source `publishers.json`; source-built
images therefore do not embed a publisher profile by default. Whether the exact
release image embeds a profile or the persistent Flare home already remembers
one has not been established here. The managed command first uses Flare's native
default trust. If that is unavailable, it accepts an optional public profile only
after checking that its namespace and SHA-256 public-key fingerprint match the
recipe pins, then mounts it read-only for native `flare inspect`. The profile's
source URL is `https://huggingface.co/local-inference-lab/Qwen3.8-Flash-Next-FLARE/resolve/main/publisher.json`, derived by the pinned source's identity resolver. It does not fetch or trust arbitrary key material. Missing or mismatched trust still fails closed.
