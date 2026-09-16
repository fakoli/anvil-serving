# Standalone media job status never collects completed output

`media workflow run` successfully submitted an image and exited. ComfyUI
completed it, but repeated `media job status` calls stayed queued forever:
only the gateway daemon and qualification path constructed a reconciler.

`media job status --backend-url ORIGINAL_URL` now performs one observation of
the selected principal-owned job through the existing reconciler and artifact
capture boundary. It never starts a worker or resubmits a prompt. Status with
no backend argument remains a stored snapshot. Tests cover completion and
artifact retention, repeated terminal status, and ownership before backend access.

Independent review required endpoint provenance as well as principal ownership.
Real backend submissions now pin a private normalized endpoint digest before
the remote request. Refresh rejects changed or unrecorded endpoints before any
network access; older records remain readable without retroactive binding.
