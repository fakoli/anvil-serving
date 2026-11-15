# Publication preparation review

Decision: **HOLD before full-corpus packaging**. One bounded static pass over publication/publish-evidence.py, check-publish-evidence.py, README.md, and the imported prior wave redactor. No archive execution, tests, source edits, model requests, lifecycle operations, or builds. Only this review note was written.

1. **Archive namespace loses the required phase structure** — publish-evidence.py:85–101 uses source.name, rejects repeated basenames, and records those flattened names. Repeated summary.json / numbered request files cannot coexist in the campaign archive. Small correction: use deterministic public relative member paths that preserve the run/phase hierarchy (or explicit source-to-member mappings), sort by the final member name, and retain that exact name in provenance. Validate traversal and uniqueness after redaction/normalization; never deduplicate by basename or drop a failure to make packaging succeed. Keep USTAR limits explicit; fail rather than truncate names.

2. **Container identity discovery excludes most raw sources** — lines 152–156 scan only artifacts with .json suffix. Archives and .jsonl status streams are omitted. The imported scrubber replaces only IDs in its discovered set (plus two historical literals), so a new ID present only in archived JSON/JSONL survives in every payload that references it. Small correction: first enumerate the union of selected artifact and archive sources, collect container_id values from every JSON document and nonempty JSONL record, then sanitize all sources with that one deterministic mapping. Validate JSONL after scrubbing as JSON is already validated. Preserve image/package/source hashes; do not substitute all hexadecimal strings. Malformed selected structured evidence must cause a visible packaging error, not silent omission. Reject empty discovered IDs so text.replace cannot corrupt every character boundary.

3. **Path safety checks miss symlink ancestors** — lines 43–47 reject only a symlink at the final file. E/link/sub/file.json can read outside E through a symlinked directory. output.exists()/mkdir likewise allow a new output directory through an existing symlink ancestor. Small correction: require resolved source containment in the intended E root and reject symlinks in every selected source component; check existing output ancestors before creating the fresh bundle. Retain absolute/parent traversal rejection, and explicitly reject Windows-drive/backslash escape forms in public paths/member names if bundles are to be safely extracted on Windows. No race-proof filesystem framework is requested; the documented stopped-producer/offline constraint remains necessary.

4. **Output collision checks happen after destructive writes, and generated paths are unreserved** — lines 165–179 call copy/archive before checking seen. Duplicate destinations overwrite the first payload before raising. An inventory destination provenance.json or artifact-manifest-source.json is silently overwritten at lines 184–197, leaving provenance/role hashes inconsistent. Preflight all canonical artifact/archive output paths together before output creation or any payload writes; reject duplicates, file/ancestor conflicts such as raw and raw/a.json, and collisions with the two generated files and the downstream finalizer's output path. Check reserved-path ancestor conflicts too. This is a small inventory validation pass, not a new packaging framework.

5. **Metadata and names bypass the existing redactor** — source_name/public_path/archive_member at lines 73–101 and inventory campaign, size_policy, and override reasons at lines 124–140/189–197 enter public JSON directly. Even a known host identity scrubbed from payload bytes remains public if present in a source filename or inventory metadata. Reuse the same deterministic scrubber for exposed text and names (or reject names that would require redaction), revalidate paths and collisions after that transformation, and compute hashes against final emitted bytes. Keep original source hashes and an unambiguous public provenance mapping. Do not silently rewrite numerical/gate decisions.

The prior scrubber is deterministic and importing it does not invoke its main assembly. Its coverage is a known identity/path/IPv4/UUID replacement policy, not a generic credential or arbitrary private-identity detector. README and provenance should describe reviewed-input exclusion accurately; the current unconditional “Credentials are excluded” is not enforced by this code. Final inventory/content review must still exclude credentials and check for identities outside that policy before publication. This review did not inspect the campaign corpus and cannot certify that it is secret-free.

## Focused repair checks

Extend the tiny offline synthetic check with: two phase directories containing the same basename (both retained and hashed); an archive-only JSON container ID and a different JSONL-only ID appearing again in a log (both scrubbed); malformed JSONL and empty ID handling; source directory symlink escape and output-ancestor symlink refusal; duplicate/canonical-alias/reserved/prefix-conflict destinations rejected before output writes; known host text in source/member/public names and manifest metadata; post-redaction name collision refusal. Verify archive membership, complete source cardinality, payload hashes, and byte-identical repeated builds. The existing check covers only one flat archive member, one sanitized payload and deterministic gzip, so it does not address these failure paths.

Two focused repair passes maximum; no full-corpus packaging is needed to resolve these findings. Preserve raw failures and all selected source records. This tooling review neither changes strict benchmark gates nor grants publication/promotion approval.

## R2 focused re-review

Decision: **HOLD for one final targeted correction to blocker 4**. Ran `python3 -B check-publish-evidence.py` in publication/: exit 0. This created only temporary synthetic fixtures, not a campaign archive. No model requests, builds, or lifecycle actions.

The revised implementation addresses archive hierarchy, JSON/JSONL container discovery across selected artifact/archive sources, source-component/output-ancestor symlink checks, and redaction of public names/metadata. Generated names are now included in preflight and ID discovery precedes final sanitized-name planning. Existing inherited-redactor limits and the requirement for an independent final secret/identity scan still apply. Preparation approval would not approve corpus publication.

One collision mechanism remains incorrect in two ways, reproduced with tiny in-memory calls to public_relative/ensure_no_conflicts:

- `raw/a.json` and `raw/./a.json` are both accepted. safe_relative parses them into the same Path, but public_relative returns the original scrubbed string rather than the canonical path. The output loop writes both to the same file without detecting the duplicate. The same issue affects repeated separators and tar member aliases.
- `raw`, `raw-other`, `raw/a.json` are all accepted. Sorting and checking only adjacent pairs misses the ancestor because raw-other sorts between raw and raw/a.json. Likewise `provenance.json`, `provenance.json-other`, `provenance.json/child` bypass the reserved-name ancestor check. This causes partial writes before an eventual filesystem error instead of preflight rejection.

Small final correction: return the validated scrubbed Path.as_posix() from public_relative; then reject duplicate canonical paths and test each path's parent chain against the complete path set (including generated paths). Do not rely on lexicographic adjacency. Add the three exact probes above as rejection tests before output creation; include a repeated-separator alias and post-redaction duplicate. No broader redesign or corpus run is requested.

R2 reviewed file hashes:
- `publish-evidence.py`: `cfbca36cafc860a43661d0d4b8bb0e1286c8f8a476ca4f2fec1492c50390d1a4`
- `check-publish-evidence.py`: `b6932372d36ad1a0cf1ed0f3f4f0eac2bb7d72ed2045a1e6a3808e92261802bc`
- `README.md`: `561494a47d43957f3a424cce050d86078003857b4e131746ed6dcbeb00520c3c`

## R3 final focused re-review

Decision: **GO for offline preparation using a reviewed inventory; no corpus-publication approval**. The final reported collision blocker is closed. public_relative now returns the validated scrubbed Path.as_posix(), so dot and repeated-separator aliases become the same destination before preflight. ensure_no_conflicts rejects duplicate canonical paths and checks every parent against the complete set, including generated reserved names. The parent-chain check no longer depends on adjacent sort order; intervening names cannot conceal the previously reported ancestor conflicts.

Ran exactly one small synthetic check: `python3 -B check-publish-evidence.py`, exit 0. Its added negative cases exercise canonical aliases and reserved-prefix collisions before output creation. The formerly interleaved three-name example is covered by static inspection of the new full-set algorithm, not claimed as a separately executed probe. No full campaign archive, model request, lifecycle action, or build was performed.

Reviewed R3 SHA-256 values:
- publish-evidence.py: `dd12456d5835a943b2523df23c6772f9553ea3b2bb2c1acbd7b3d1a71c6b0186`
- check-publish-evidence.py: `923fb0ec7343332835b8e48a433e40bafa1399c111cb2e0d530250b6222f6886`
- README.md: `561494a47d43957f3a424cce050d86078003857b4e131746ed6dcbeb00520c3c`

All five originally reported mechanisms are addressed within this bounded review. The inherited redactor still requires independent final secret/identity scanning and complete inventory review, including retained failures and raw attempts. Successful synthetic packaging does not certify full-corpus completeness, confidentiality, size policy, benchmark qualification, publication, or promotion. This closes the two permitted correction passes; no further implementation expansion is requested.
