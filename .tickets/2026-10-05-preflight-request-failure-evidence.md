# Retain failed preflight request observations

Status: implemented; independent review and CI pending.

A diagnostic candidate preflight reported 19/20 tool requests, with a remote
connection closed before a response. Its artifact contained only the nineteen
successful observations. The failed batch index, elapsed time and exception
type were unavailable. The serve remained healthy and reported no OOM; the
transport cause is unresolved. The diagnostic run remains failed.

All eleven request sites now pass through one evidence boundary around the
existing Chat Completions, SSE or Responses request function. Failed attempts
record their stage, optional batch index, monotonic elapsed seconds, original
exception type and bounded detail. The existing operator redactor, including
the supplied credential, runs before truncation. Probe summaries reuse that
safe detail. Response validators, fan-out, timeouts and HTTP behavior are
unchanged. Failed attempts do not acquire fictitious finish data or misleading
finish-policy errors. Response-decoding exceptions are included; downstream
validation exceptions are not relabeled as transport failures.

Regression coverage injects one indexed concurrent failure, proves all twenty
attempts remain with a failed gate, checks redaction and truncation, verifies
SSE/Responses failures and the second tool-result stage, and ensures validation
errors do not create duplicate request-failure observations. No live rerun was
used to overwrite the historical failure.
