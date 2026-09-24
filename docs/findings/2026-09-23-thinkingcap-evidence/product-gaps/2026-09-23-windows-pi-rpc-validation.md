# Windows official Pi smoke cannot complete

Status: open; deferred outside the ThinkingCap model campaign.

The required repository test run stopped with 7,923 passed, 417 skipped and one
failure in `tests/workbench/test_pi_official_smoke.py`. Independent reproduction
identified two Windows problems before any model request:

1. The fixture replaces the child environment and drops `SystemRoot`. Pinned
   Node 22.23.2 aborts with status 134 at `ncrypto::CSPRNG(nullptr, 0)`. Adding
   only `SystemRoot` fixes the isolated crypto probe.
2. With that variable restored, Pi returns valid RPC output, but
   `PiRpcClient._default_read_chunk` uses `select.select` on an anonymous pipe.
   Windows rejects that operation with WinError 10093; the client swallows it
   as empty output and the test times out.

The exploratory fixture-only change was reverted because it was insufficient.
The model campaign leaves both runtime and test sources unchanged and does not
claim a passing repository-wide test gate.

Fix the Windows pipe reader with a bounded platform-specific path, preserve the
POSIX path, retain bounded child stderr on unexpected exit, and preserve
`SystemRoot` in the Windows test environment. First pass the real pinned Pi
smoke, then the full test wrapper. Do not replace the real smoke with a mock or
skip it to obtain a green gate.

Evidence: `docs/findings/2026-09-23-thinkingcap-evidence/validation-pi-failure.json`.
