# Cancelled evaluation pilot: R5

This is a failed, partial pilot at source `6b26ed55285480cd731ae7771f41eea917382f02`. Of 96 planned jobs, seven were attempted: six native jobs completed and the first Aider job was cancelled after a harness startup error; 89 jobs were unrun. The candidate arm was never run. This packet supports failure analysis, not a model ranking, engine comparison, or promotion.

These wrappers were assembled **after the abort**, at 2026-10-25T02:18:04.911788+00:00. They are not a before-requests artifact set. The cancelled attempt is preserved independently of later repairs or retries.

- [Campaign report](campaign-report.json) is the authoritative public aggregate, preserving null and unknown populations.
- [Summary](summary.json) states the diagnostic decision and limits; [identity/control map](identity-control-map.json) records configured model, runtime, image and GPU counts.
- [Source registry](source-registry.json) retains previously pinned source provenance; it is not new research or a performance prior.
- [Projection ledger](projection-ledger.json) maps private originals to four [JSONL containers](projections/). [Projection validation](projection-validation.json) covers record and source bindings; the artifact finalizer alone does not validate JSONL semantics.
- [Failure diagnostic](failure-diagnostic.json), [friction log](friction-log.md) and [restoration record](restoration.json) preserve the startup failure, managed cancellation and incomplete interval.
- [Publication summary](publication-summary.md) is draft copy; [artifact manifest](artifact-manifest.json) binds public derivative bytes across all ten roles.

Private originals remain authoritative. Public projections cannot revalidate the original native identity/hash chain. Original internal hashes bind private bytes; the projection ledger and artifact manifest bind the sanitized derivatives. Redacted text and identities cannot be replayed publicly. Missing/unrun dispositions are evidence gaps, not manufactured records. Reusing one file under several roles does not create independent evidence.

The completed native cells do not establish a 6/96 quality success rate. First-Aider scoring and its interval remain unknown. Sparse engine-zero samples do not prove absence of compute; a separate later zero observation cannot repair the cancelled interval. No model swap or incumbent reload occurred. Model restoration was unnecessary, while full campaign closure remains blocked. Public publication and final wrapper review remain pending; promoted=false.
