# Preserve bounded relay failure diagnostics

Status: open

During a C1 routed multimodal acceptance run, image and OCR calls returned
`RelayBackendError` / HTTP 500. Identical direct calls and a subsequent routed
retry passed without a configuration or runtime change. The engine stayed
healthy. This is an intermittent relay incident with unresolved root cause.

The request ledger and managed router logs retain the gateway request ID and
exception class, but omit the upstream phase/status. Controlled transport
failures and non-success HTTP responses are sanitized in
`router/backends/relay.py`; the front door records only the exception class.

Add bounded, secret-safe telemetry keyed by gateway request ID: phase
(connect, headers, body, response parsing), upstream HTTP status when known,
exception type, and bounded parser category. Never emit request/response
bodies, authorization headers, credentials, or unredacted upstream URLs.
Expose it through managed request diagnostics. Cover transport failures,
non-2xx and malformed JSON, and ensure payload secrets cannot enter output.

Keep the failure and successful retries as separate evidence. A retry does
not establish root cause or justify a speculative model/recipe change.
