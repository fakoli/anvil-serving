# `serves up` must surface bounded pre-container build failures

**Observed:** 2026-08-28

## Problem

The first managed ComfyUI bring-up failed while Docker Compose was building the
image, before a container existed. `serves logs` therefore had no owning
container to inspect, while the JSON command envelope reduced the failure to a
generic `command failed`. A bounded read-only Buildx history inspection was
needed to identify Git's safe-directory rejection of the base image's `/app`
checkout. The repeated managed run then exposed a second pinning error directly:
the `/app` checkout is the ComfyUI release revision, not the distinct container
source revision recorded in the lock.

## Resolution

- Verify the pinned ComfyUI release revision with an explicit
  `safe.directory=/app` override during the root-owned image build, while
  retaining the distinct pinned container-source revision as provenance.
- Project the final bounded, credential-redacted lifecycle-command output from
  `serves up`, where BuildKit places the actionable failure.
- Keep the projection serving-engine agnostic and leave full runtime logs under
  the existing managed `serves logs` surface once a container exists.

## Verification

- `python -m pytest tests/media/test_comfyui_packaging.py tests/test_serves.py -q`
  — 192 passed.
- Repeated managed bring-up and the full repository gate remain part of the
  enclosing media-gateway qualification.
