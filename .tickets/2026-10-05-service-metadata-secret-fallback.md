# Service metadata ignores durable credentials

Status: fixed and verified.

Recovery exposed `credential_unavailable` from `host services status` despite a
configured file-backed controller credential. The engine metadata adapter read
only the process environment, unlike the shared durable credential contract.
Use the existing environment-first `resolve_env_value` fallback. Preserve exact
endpoint/no-redirect handling and never include credential values in receipts.

Regression: `tests/service_runtime/test_engine.py` verifies file fallback,
environment precedence, absent-credential refusal, and receipt redaction.
The shared resolver also skips unreadable encodings, covered in
`tests/test_envfile.py`. A restored controller passed authenticated readiness
through the saved credential reference without exposing or rewriting its value.
