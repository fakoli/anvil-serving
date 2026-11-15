# R10 monitoring identity failure and incomplete restoration acceptance

The candidate was created and reached startup readiness, but managed load exited1 because Anvil Serving ignored Ignis `--model-id` when deriving served identity. Monitoring correctly withheld registration. The full [48-row native report](campaign-report.json) remains planned with zero submitted, attempted, completed or scored jobs. No candidate generation, quality or inference-latency result is claimed.

The candidate was removed. The first incumbent restoration omitted explicit GPU order and was rejected by monitoring; the exact-order retry restored the original service and active monitoring. Routed coding/JSON/approximately128k needle/20of20 tool checks passed. **Restoration acceptance remains incomplete:** the clean-input Pi attempt performed its real read and completed with the declared provider/model, but returned extra prose and a duplicated digest. Both Pi attempts remain retained, with no further retries.

- [Summary](summary.json), [frozen identity](frozen-identity.json), [monitoring failure](monitoring-failure.json).
- [Startup events](startup-events.json), [bounded startup containment](candidate-containment-snapshot.json), [candidate removal](candidate-removal.json).
- [Failed restoration attempt](restoration-failed-attempt.json), [final service after-state](final-restoration-evidence.json), [Pi validation](pi-validation.json), [restoration boundary](restoration.json).
- [Failures](friction-log.md), [source registry](source-registry.json), [derivative ledger](projection-ledger.json), [manifest](artifact-manifest.json), [public summary](publication-summary.md).

This packet was assembled after the attempt. Raw candidate HTTP bodies and the inner registration exception were not durably retained; source-backed independent diagnosis and startup logs are distinguished from raw API evidence. Private originals and frozen plans remain authoritative. Their hashes cannot be revalidated from sanitized derivatives; no complete native projection or repaired measurement is claimed. No promotion. Independent packet/size/publication gates remain separate.
