# Native evidence format registry review

Decision: **HOLD for small documentation corrections below; no framework or producer changes needed.** Reviewed publication/native-evidence-schemas.json against small r2-A retained receipts and frozen run-phase.py/replay.py/decode.py/normalize.py/analyze.py plus the reviewed closure/descriptive consumers. No metric analysis, network, model request, lifecycle, build, full-corpus scan or source mutation. Only this note was written.

1. **Correct native path semantics in the gate-proof family.** `core_fields.proof` says evidence entries contain evidence-relative source paths. Native functional.json actually contains absolute paths, including sibling campaign bodies and product source files outside this campaign root; proofs.json likewise stores absolute proof pointers. The registry's own representative paths are evidence-root-relative, but that is not the raw payload path format. State this distinction explicitly and require the existing publication redactor/provenance mapping for public copies. Do not imply raw proof payloads can be published merely because the registry itself has safe relative paths.

2. **Correct missing/null outcome semantics and identify the decode variant.** Successful replay attempts commonly omit `error`; successful wrapper summaries use error=null. Those mean no error reported when the explicit success/outcome fields pass, not unknown failure. Missing optional wrapper fields alone do not make execution incomplete. Missing timings/reasoning counts/work remain unknown, and absent required validity fields still fail their consumer contracts. The HTTP-attempt family should also explicitly distinguish compact decode attempts: they omit replay iteration/start_ns/start_epoch_ns/message/finish_reasons/done/native_first_token_seconds fields, retain decode_tokens_per_second (tokens/second), and set speculative_work=null with batch_only_no_request_attribution. Raw response files retain the stream/finish/timestamp inputs validated before compaction. Batch speculative work is correctly documented as whole_drained_batch in the decode summary and must not be duplicated per request. This is a small format-description correction, not a request to change artifacts.

A minor clarification can accompany those corrections: “normalized-matrix is produced only after corpus closure” is publication policy for the final selected artifact, not a producer invariant. Frozen normalize() can process incomplete corpora and deliberately retains missing/failed/uncertain denominators. Label final-artifact scheduling as policy without implying partial normalization is impossible.

Other reviewed content is sound within its declared selected-format scope. Eight family count, schema IDs, Unix seconds versus integer nanoseconds, wrapper/HTTP denominator roles, historical-query timestamps, diagnostic flags and strict/descriptive schema markers match retained sources. The registry explicitly does not validate receipts or authorize publication. Failed numerical acceptance, absent teacher-forced/logit oracle, transfer-byte limits and lack of promotion/confidentiality certification are accurately retained. The visible registry contains no operator names, private hostnames/addresses, GPU UUIDs, personal absolute paths or container identities. This text review is not the later full artifact/secret scan.

Retain the existing frozen-normalizer decode parsing limitation and the scope of independently reviewed functional proofs. Listing collect_restoration as a format producer must not be treated as operational approval of the held whole collector; the actual retained restoration receipts and separate review disposition govern execution.

## Focused correction recheck

Compared the updated registry with preserved publication/review-history/native-evidence-schemas-88ba8da02e732c1896fbcda76619789e3c24a568c82ee455116a7306364e696b.json; JSON parses and eight families remain. Native absolute proof-path semantics and final-publication versus partial-normalization policy are corrected. HTTP missing/error wording now distinguishes unknown measurements from no error reported.

**HOLD for two residual text corrections within the original findings:**

- The first family's unknown_or_null still says absent optional wrapper fields describe incomplete/failed execution. Replace that with: optional-field absence alone does not establish failure; missing required validity fields/non-null errors must be handled according to the producer/consumer outcome contract. This fixes the remaining contradiction with the corrected HTTP paragraph.
- The new compact decode list incorrectly includes request_ids. Frozen decode.normalize and the inspected r2-A compact attempt rows omit request_ids; those remain in raw response files. Remove request_ids from the compact retained-field list (or explicitly place them in the raw-response description). The rest of the decode variant, units and batch-only work description is consistent.

No new review scope or framework requested. No operations or artifact/source edits were performed; only this note was appended.

## Final second correction recheck — GO

Verified only the two remaining sentences against preserved publication/review-history/native-evidence-schemas-a38a4ac1843a61cb751fe46318d15932ed7ad959df477addc309c2ab18c64dff.json. JSON parses. The diff changes exactly the optional-wrapper outcome wording and removes request_ids from compact decode retained fields while explicitly listing it as omitted. Both residual findings are resolved.

**GO for the registry as format documentation.** No remaining material issue within the reviewed scope. This does not approve corpus publication, certify sanitization, validate individual receipts, change gate outcomes or authorize the held whole collector. Final artifact/identity/secret scanning and publication review remain separate. No further research or operation was performed.
