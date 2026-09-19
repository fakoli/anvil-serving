# Windows official Pi smoke exits before RPC handshake

The qualification worktree full suite stopped at
`tests/workbench/test_pi_official_smoke.py::test_official_pi_native_history_fork_resume_and_model_controls`:
`PiProcessExited: Pi runner exited with status 134`. The run recorded 7,924
passes and 416 skips before this failure. No Pi runtime or test code was changed
by the recipe/documentation campaign.

A bounded reproduction launched the installed Pi with the test's minimal
environment and no prompt or provider request. Captured subprocess stderr shows
Node initialization at `node.cc:1266` failing
`Assertion failed: ncrypto::CSPRNG(nullptr, 0)` and exiting 134. Full private
diagnostic output is retained outside the repository. Standalone Node
`--version` succeeds both with PATH alone and with SystemRoot added, so a missing
SystemRoot explanation is not established.

Follow-up: identify the executable/environment selected by the Windows Pi shim,
capture startup stderr through the approved bounded diagnostic surface, and
repair the native test/runner startup without weakening its RPC assertions.
Then rerun the failing smoke and remaining full suite. This is a verification
limitation, not evidence of a Qwen model failure or live Workbench outage.
