# Managed GPU power-cap operation for model recipes

Status: open. Discovered during the 2026-09-25 RTX 5090 Swift-1.5 NInfer trial.

The community recipe requested a 450 W GPU cap. Anvil Serving can inventory
GPUs and launch a pinned serve, but it has no guarded command to preview, apply,
read back, and restore an exact power cap for one selected GPU. The operator
used a one-off `nvidia-smi -pl 450` host command and retained the before/after
readback in the campaign evidence. This makes an otherwise recorded recipe
depend on manual host state outside the managed lifecycle.

Add a host GPU power-limit verb with one discovered GPU identity, supported
range validation, a dry-run showing current/requested limits and rollback,
confirmed apply, exact readback, and an explicit restore action. Keep real GPU
UUIDs and host addresses in private operator evidence. The recipe or serve
manifest should declare the required cap and refuse a launch when it differs.
