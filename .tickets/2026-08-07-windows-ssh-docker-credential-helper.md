# Windows SSH sessions cannot pull public images: Docker Desktop credential helper requires an interactive logon

**Status:** Open — worked around by `docker save | docker load` image transfer

## Problem

Driving `serves up` on a Windows host over SSH (network logon) fails at the
image-pull step for **public, anonymous** images:

```text
FAILED: Image vllm/vllm-openai@sha256:e4f88a... Pulling
error getting credentials - err: exit status 1, out: `A specified logon session does not exist. It may already have been terminated.`
```

Observed 2026-08-07 during the fakoli-mid-mod auxiliary bring-up (Docker
Desktop 29.6.2, per-user install, `desktop-linux` context). The failure
persists after removing `credsStore` from `%USERPROFILE%\.docker\config.json`,
with empty `auths`, with an isolated `DOCKER_CONFIG`, and for a plain
`docker pull hello-world` — the credential lookup appears to happen inside
Docker Desktop's Windows credential integration, which requires an
interactive logon session that SSH does not have. WSL bypass was unavailable
(the only user distro's vhdx was missing; the `docker-desktop` distro refuses
direct CLI use).

## Impact

- A model-capable Windows host cannot be remotely onboarded or operated by
  `serves up` when any image is missing from the local daemon cache.
- The error message misleads: no credentials are needed for these pulls.

## Workaround (applied)

Pull the pinned image on another host and stream it:
`docker save <image> | ssh <host> docker load`. Loads bypass registry auth.
Once images are cached, `serves up` succeeds.

## Required behavior

1. Document the limitation and the save/load workaround in the multi-host
   operator docs.
2. `serves up` / `models recipes load` should detect the
   "error getting credentials ... logon session" signature and emit a typed,
   actionable error naming the interactive-logon constraint and the
   workaround, instead of a raw compose failure.
3. Consider a product-managed `serves image ship <serve> --to host:ID`
   verb (save/load over the declared transport) so multi-host image
   distribution is a managed operation, consistent with the one-stop-shop
   principle.

## Acceptance

- Hermetic test for the error-signature mapping.
- Docs section for Windows-over-SSH operation.
- Optional: the image-ship verb with a dry-run preview.
