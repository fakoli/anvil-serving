# Structured JSON errors are duplicated as warnings

**Status:** Resolved 2026-08-30

## Problem

The global JSON wrapper copied every captured stderr line into `warnings` before
it knew whether a command succeeded. A failing command that already provided a
typed error therefore repeated its human error line as a warning. For example,
an unconfirmed guarded mutation returned both `error.code =
confirmation_required` and the same refusal text in `warnings`.

An independent GPT-5.5/xhigh adversarial review found the regression on the
`1.0.0` candidate at revision
`ae39f0f0e52aa92a7b6e327f3e1ebaf1d4ebf151`.

## Acceptance

- A typed JSON failure keeps the structured error and does not reclassify its
  human stderr rendering as a warning.
- Explicit typed warnings, including Fleet partial diagnostics, remain intact.
- Successful legacy commands may still expose nonempty diagnostic stderr as
  warnings.

## Resolution

The JSON wrapper promotes captured stderr to warnings only for a successful
command. Failing commands retain only warnings explicitly recorded by the
dispatcher or typed result, and a guarded-mutation regression pins the contract.
