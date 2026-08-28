# ComfyUI staging must expose portable verifier failures

**Observed:** 2026-08-27

## Problem

The first managed staging attempt downloaded the exact pinned FLUX.2 Klein
diffusion checkpoint into its owned partial target, then failed before atomic
placement. The staging error retained the target and exit code but omitted the
verifier's actionable diagnostic. A bounded read-only inspection proved that
the partial file had the exact expected byte count and SHA-256 digest.

The cause was the use of GNU `sha256sum --strict` with the BusyBox-compatible
digest-pinned staging image. BusyBox supports check mode but not `--strict`.

## Resolution

- Use portable `sha256sum --check` in the pinned staging container.
- Recognize an already complete, exact owned partial and place it without a
  second network transfer.
- Make curl quiet except for failures and return a bounded URL-redacted
  diagnostic on managed staging errors.
- Keep existing final files immutable and preserve unrelated model files.

## Verification

Pending the focused bundle tests, resumed managed stage, and full repository
gate.
