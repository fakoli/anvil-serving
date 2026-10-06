# Device-key protocol fixtures must await their own audit

Windows Python 3.13 CI received HTTP 503 instead of the expected unknown-model
404 after successful and denied purpose-model requests. The captured assertion
did not retain the response body. A controlled reproduction holds a prior
denied request's post-response audit transaction: its 403 is already delivered,
but the next credential admission receives `503 key_store_unavailable` with
`OperationalError('database is locked')`. The production one-second storage
bound correctly fails closed; finishing a response is not finishing its audit.

The fixture's sequential client now assigns a unique test-only completion
marker and awaits the corresponding handler's cleanup event. Keep-alive is
preserved. Independent clients in the explicit saturation test remain concurrent
and do not wait on the sequential client's marker. A bare socket close or an
unrelated handler cannot acknowledge a different request.

All 15 device-key tests pass, including streaming, revocation on a reused
connection, deliberate store failure, scoped-credential independence and
concurrent authentication saturation. No production change, retry, storage
timeout increase or expected-status relaxation is included.
