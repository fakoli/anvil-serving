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
source revision recorded in the lock. The successful build finally exposed a
Windows console issue: Compose emitted a byte outside CP1252, causing Python's
background output reader to raise even though the worker became healthy.

## Resolution

- Verify the pinned ComfyUI release revision with an explicit
  `safe.directory=/app` override during the root-owned image build, while
  retaining the distinct pinned container-source revision as provenance.
- Project the final bounded, credential-redacted lifecycle-command output from
  `serves up`, where BuildKit places the actionable failure.
- Decode lifecycle subprocess output explicitly as UTF-8 with replacement so
  undecodable progress bytes cannot hide the command result on Windows.
- Keep the projection serving-engine agnostic and leave full runtime logs under
  the existing managed `serves logs` surface once a container exists.

## Verification

- `python -m pytest tests/media/test_comfyui_packaging.py tests/test_serves.py -q`
  — 193 passed after the UTF-8 decoder regression was added.
- Repeated managed bring-up built the corrected image and reached HTTP 200
  readiness without a console decoder exception.
- The full repository gate remains part of the enclosing media-gateway
  qualification.
