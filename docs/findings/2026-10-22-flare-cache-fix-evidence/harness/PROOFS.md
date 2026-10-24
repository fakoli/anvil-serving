# Normalizer receipt contract

Run the existing offline pipeline:

```sh
python3 normalize.py > normalized-matrix.json
python3 analyze.py normalized-matrix.json > diagnostic-analysis.json
```

normalize.py defaults to `../formal`. It enumerates all9 lanes in all3 pairs:
`r{1,2,3}-{a,b}/{lane}/{warmup,prime,measured,measured-1,measured-2}`.
Only applicable target phases are expected; warmup is always3 extra requests and
is excluded from the2550 target denominator. Missing phases and failed attempts
are retained, never removed from the expected denominator.

Each lane supplies `proofs.json` containing only references:

```json
{
  "schema": "flare-cache-fix-lane-proofs/v1",
  "proofs": {
    "functional": {"path":"functional.json","sha256":"<actual file sha256>"},
    "monitoring": {"path":"monitoring.json","sha256":"<actual file sha256>"},
    "independent_oracles": {"path":"oracle.json","sha256":"<actual file sha256>"},
    "causal_chain": {"path":"causal.json","sha256":"<actual file sha256>"},
    "restore_fidelity": {"path":"fidelity.json","sha256":"<actual file sha256>"},
    "restoration": {"path":"../restoration.json","sha256":"<actual file sha256>"}
  }
}
```

A referenced machine proof uses `flare-cache-fix-proof/v1`, the exact gate name,
`ok`, nonempty named boolean `checks`, and nonempty raw `evidence` references.
It passes only when ok/checks are literally true and every referenced file exists
with the exact SHA256. Relative paths resolve from the containing JSON's directory.
Restoration may reference the actual corresponding window file using any exact
relative/absolute path. Missing, false, incomplete or hash-mismatched proof is false.
Do not write passing checks without deriving them from actual probe evidence.
`proof-template.example.json` is explicitly synthetic and cannot pass verification.

`identities` needs no extra proof: native wrapper/status/running/HTTP model evidence
already proves the phase identity. Normalizer rechecks image, container incarnation,
registry/recipe labels and HTTP alias, requires one incarnation across all phases,
and rejects reuse across lanes/runs. Actual arm order comes from warmup epoch anchors;
it is not filled from the expected AB,BA,AB sequence. Missing peer anchors leave order
unknown. Functional/monitoring proofs additionally require successful native phases.
The canonical diagnostic amendment forces restore_fidelity=false and the analyzer
still forces recommendation/performance eligibility false.

Native attempts are preserved in phase order, including failures. Small-set replay
iteration IDs remain distinct. Decode batch_speculative_work is copied ONCE at run
level from its native summary; no request attribution is invented. Phase summaries,
normalization issues and exact source-file hashes remain in the normalized artifact.
Analyzer surfaces those issues even if the request arrays happen to be complete.

If a response capture proves a request started but no persisted validation row exists,
normalization preserves it as a failed, explicitly reconstructed attempt with its raw
reference. A before-metrics snapshot without request evidence is only an uncertain
initiation slot. It is never fabricated into a completed/attempted request; totals
are marked as a lower bound and the phase remains incomplete. Partial failures do
not stop normalization of later lanes.
