# Evidence inspection rejects the durable job artifact wrapper

Status: open; deferred outside the ThinkingCap context qualification.

CLI 1.2.1 `eval benchmark evidence show` rejects a completed durable context job's `artifact.json` with `JSON file is not recognized benchmark evidence`. The file has schema `anvil-serving.benchmark-result/v1`; its `results.evidence` contains a valid canonical `anvil-serving.benchmark-evidence/v1` object.

Extracting that nested object without changing its schema or values lets the same product inspection command pass with no validation errors. Both the complete wrapper and the nested evidence are retained. This is an inspection limitation, not a failed model request.

Add bounded wrapper recognition to the evidence inspector, preserving the outer completion/failure state, provenance and nested canonical schema. Reject malformed, incomplete or contradictory wrappers rather than inferring success from the inner object. Verify completed, failed, partial and malformed job artifacts independently. Avoid requiring a one-off extraction script for the routine inspection workflow.

Evidence: `docs/findings/2026-09-24-thinkingcap-context-evidence/native/context-scout/artifact.json`, `native/context-scout/cross-suite-evidence.json`, and `native/context-scout/evidence-inspection.json` in the same bundle.
