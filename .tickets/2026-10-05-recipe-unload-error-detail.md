# Retain Docker failure detail for discovered recipe unload

Status: implemented; targeted regression and independent review pending.

Observed: a managed candidate unload selected by container discovery returned only `recipe unload failed`. Subsequent owning status observed exit137 with OOMfalse; retry through the exact registry/model removed the container. The generic error discarded the original Docker diagnostic, so this evidence cannot establish the daemon failure cause or attribute it to model execution.

The registered unload path already displayed stderr/stdout; discovery had no documented security rationale for suppressing the same command's result. Share bounded, operator-redacted diagnostic rendering across both paths, preserve the original nonzero exit status and immutable-ID ownership recheck, and never claim successful removal or run post-removal cleanup on failure.

Regression coverage: stderr precedence, stdout fallback including whitespace-only stderr, empty-output fallback, redaction before truncation, exit status, exact container ID, and no cleanup/success on failure. No live removal is required to verify this reporting-only change.
