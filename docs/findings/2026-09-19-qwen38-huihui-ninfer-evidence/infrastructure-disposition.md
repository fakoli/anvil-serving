# Infrastructure disposition

The candidate failed the correctness gate. No deployment, route, Workbench
resource assignment, Grafana model mapping, or private fleet inventory was
changed. The media environment was recreated through the existing managed
`serves up` command and verified healthy; [restoration](restoration.json) records
unchanged source manifest hashes. This proves the observed warm-cache media
restoration, not a clean-machine install or immutable candidate recreation.

The deployed ai-infra checkout was inspected at revision
`766792d5082ee7ae9a200070e739d72a3e9845bc`. An unrelated modified systems-knowledge
document was preserved. Workbench's active system service uses the private
operator inventory; the canary was inactive. The secondary Qwen resource still
described the established GGUF configuration.

Repeatable update surfaces already present in that checkout:

- `modern/observability/scripts/import_benchmarks.py` reads
  `inventory/benchmark-sources.json` and can generate deterministic Workbench
  cards as well as the private Grafana benchmark inventory. Failed/noneligible
  capacity records are excluded. This campaign has no eligible capacity run;
  its native quality failures must not be relabeled as throughput evidence.
- The model-switch workflow updates the existing stable service's engine,
  model, upstream observer port, and verification timestamp in the private
  fleet inventory, renders targets, then runs `validate.sh`. Inventory-only
  updates do not require an inference restart.
- `modern/scripts/capture-anvil.py` previews allowlisted operator configuration
  capture; `--write` follows approved operator changes. Its manifest is
  `modern/roles/anvil_config/files/manifest.json`. Broad capture is forbidden;
  secret material remains referenced, not copied into versioned configuration.

These scripts were inspected, not changed or applied. NInfer lacks a qualified
telemetry adapter in the observed engine set; relabeling it as another engine
would be incorrect. Candidate recreation remains blocked by mutable package and
linked-runtime resolution even though the locally compiled executable is now
SHA-pinned. Before a future promotion, bake and pin the full runtime, prove clean
managed recreation and rollback, qualify telemetry, then apply the private
deployment and validate real client plus Workbench/Grafana identity. None of
those future gates is reported as completed here.
