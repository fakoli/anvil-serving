# Warmup and whole-matrix normalization

`normalize.py` now builds the retained native runs automatically; see PROOFS.md for the exact hash-bound receipt schema. Use `python3 normalize.py > normalized-matrix.json`, then the existing analyzer. Missing phase/proof evidence remains false/incomplete.

`--lane warmup --phase prime` uses the existing run-phase.py wrapper. Exactly three
frozen one-token bodies run serially in order0,1,2 on every fresh incarnation and
arm. Their literal prompts differ from original, fixed, pageheavy, cold, pressure,
restore and decode targets. Profiles reuse8000/16000/29000 filler repetitions;
those are NOT exact token counts. Parent must validate actual native lengths/capacity
in the pilot before full use. Request temperature0 is effectively clamped around.95;
no deterministic sampling claim. No adaptive warmup or existing-body edits.

Warmup remains performance_eligible=false and is excluded from the2550 formal
requests.54 fresh incarnations would add162 warmup requests. Actual incarnation
count and rollback windows remain parent-owned. Warmup cache history is recorded
as its own phase; never merge its IDs/counters into target attempt denominators.

## Existing analyzer input recipe

Create one `runs` row per `(lane,repetition,arm)` using the native HTTP artifact
JSON without rescoring model answers. Retain source directory names/hashes beside
the normalized file. The analyzer command remains:

```sh
python3 analyze.py normalized-matrix.json > diagnostic-analysis.json
```

For every run:

1. Concatenate target `http/attempts.json` arrays in phase order, keeping failed
   attempts and native prime/measured phase tags. Do not append warmup or pilots.
2. Keep small12 measured iteration1 and2 as distinct case IDs (`index:iteration`).
   Keep prime IDs unchanged. Never collapse two replays into one dictionary.
3. For decode only, copy `http/summary.json.batch_speculative_work` ONCE to run level.
   Keep each request's speculative_work null; never distribute batch counters.
4. Read every HTTP and wrapper summary. Preserve errors and actual counts in source
   metadata; any error or incomplete summary keeps functional/monitoring receipts
   false. Missing artifacts remain missing, and no denominator is reduced.
5. Attach reviewed receipt booleans and their original evidence references. Do not
   manufacture passes from HTTP success. Restore fidelity remains failed under the
   diagnostic amendment, regardless of numeric comparisons.
6. Record repetition1..3 and actual zero-based arm order from warmup epoch anchors; verify against frozen AB,BA,AB. Preserve body
   hashes, prompt usage, raw IDs, reasoning counts/spans and measured metrics.

| Lane | Ordered target phases per run | Expected attempts |
| --- | --- | ---: |
| original-reverse | prime52 + reverse52 |104|
| original-forward | fresh prime52 + forward52 |104|
| small12-original | prime12 + replay1(12) + replay2(12) |36|
| fixed-work | prime52 + reverse52 |104|
| small12 | prime12 + replay1(12) + replay2(12) |36|
| pageheavy | prime12 + replay12 |24|
| cold | measured12 |12|
| decode-c1 | measured batch1 |1|
| decode-c4 | measured batch4 |4|

Input top-level diagnostic policy must retain this immutable reference:

```json
{
  "diagnostic_only": true,
  "diagnostic_amendment": {
    "path": "OPERATOR_PATH_ea2fb369a27f/diagnostic-amendment.json",
    "sha256": "b6be295ebe5e6e41de64ffa43d65990866479dbcaf2a1cf0183b6068f8e0c68a"
  }
}
```

Each run contains `lane`, `repetition`, `arm`, `order`, `attempts`, `receipts`, and,
for decode, `batch_speculative_work`. Original raw paths and receipt references may
be retained as extra metadata. See prospective.json for required receipt names.
No new normalization framework or operational command is required.

Analyzer always detects the canonical amendment independently of caller-supplied
receipt values, emits its current path/hash and diagnostic_only=true, and forces
recommend_candidate=false/performance_eligible=false. A mismatched input amendment
hash is reported. `comparison_gates_passed` reports the original strict comparison
gates separately; it cannot authorize acceptance. All5percent/3percent thresholds,
output/work matching and failed-request denominators remain intact.
