# Real SSE fixture must synchronize observed events

Linux Python 3.11 CI reported the real SSE overlap fixture as incomplete.
The fixture released the anchor after the server sent contender headers,
then used short sleeps to assume the client had observed those headers and
the anchor delta. Server writes do not establish client-observer ordering.

A deterministic reproduction holds the real contender response callback until
the real anchor content callback. Both requests finish successfully, but
coverage is correctly false: the client sees the anchor content before the
contender response headers. No production benchmark change is warranted.

The fixture now wraps the actual SSE client observer and releases bounded
server waits only after the original observer records contender headers and
anchor content. It keeps real HTTP/SSE parsing and the serial negative control,
with the same timeout bounds and no fixed sleeps for overlap ordering.
Production coverage, retrieval and performance-eligibility rules are unchanged.
