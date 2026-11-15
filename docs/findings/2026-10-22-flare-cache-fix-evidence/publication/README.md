# FLARE-FIX-001 public evidence packaging readiness

`publish-evidence.py` is an offline closure tool. It imports the prior diagnostic wave's reviewed redactor, copies only files explicitly named in a reviewed inventory, and creates a sanitized staging bundle plus deterministic gzip archives. It does not contact a model, run a lifecycle action, evaluate performance, decide a gate, or publish documentation.

At closure, create a reviewed `flare-cache-fix-publication-inventory/v1` JSON outside this directory. Its `artifacts` entries name one regular UTF-8 source file, its public relative path, optional `gzip`, and one or more artifact roles. Its `archives` entries name a public `.tar.gz` path, a complete explicit list of regular source files, and roles. Archives preserve sanitized evidence-relative member paths, sort by those paths, use USTAR with zero mtimes and fixed mode, and use gzip `mtime=0`.

The inventory must supply the exact campaign identity, native schemas, and an explicit `missing` or `not-applicable` reason for every empty semantic role. It must enumerate raw attempts, failures, configuration, restoration receipts, and provenance needed for the final retained record. The tool refuses unsafe paths, symlinks, duplicate or prefix-colliding public paths, reserved generated names, empty roles, and malformed JSON or JSONL after redaction. `provenance.json` maps private source hashes to sanitized payload hashes and archive members. `artifact-manifest-source.json` is an input to the product finalizer, not a final manifest.

Run only after producers stop and the corpus is reviewed:

```text
python3 publication/publish-evidence.py --inventory REVIEWED-INVENTORY.json --output publication-staging
python3 OPERATOR_PATH_d6825dbd2f6e/finalize_artifact_set.py publication-staging/artifact-manifest-source.json
```

Then run the finalizer twice and require byte-identical output as the benchmark documentation contract requires. A closed r1-A alone was 1,916 files / 118.74 MiB uncompressed, with response captures near 1.16 MiB; the final inventory therefore needs an explicit size-policy review and directory-preserving compression. It must not drop failed attempts or raw responses merely to meet a size target. This preparation does not provide the inventory, final role decisions, a publication finding, a public schema for FLARE native data, any accepted metrics, or promotion approval. The campaign remains `no-promotion` until its separate human gate.
