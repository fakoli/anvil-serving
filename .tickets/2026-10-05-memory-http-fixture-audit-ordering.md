# Sequential memory HTTP tests must await request cleanup

Windows Python 3.11 CI received HTTP 503 while testing malformed MCP envelopes.
The retained failure did not include its response body, so that individual
response cannot be attributed to a specific admission branch retrospectively.

A controlled reproduction establishes a fixture race: hold four credential
audit calls after their HTTP responses have arrived. The first four malformed
requests receive the expected HTTP 200 / JSON-RPC invalid-request envelopes;
the fifth receives `503 key_store_unavailable` because the audits still hold
the four shared credential-store slots. Production admission correctly fails
closed. Reading a complete response does not imply that its audit has finished.

The sequential protocol fixture now requests one-shot connections and waits
for an explicit handler-completion event before its helper returns. It retains
the real credential store, authorization, audit writes and HTTP path. A bounded
event regression holds the audit, observes the response body and completion
wait, then proves that releasing the audit allows the request to return.
No status assertion, security limit, retry policy or production behavior changes.
