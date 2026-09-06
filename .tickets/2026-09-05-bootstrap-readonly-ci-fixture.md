# Bootstrap Linux read-only CI fixture

Status: test-only repair verified locally; final PR CI remains mandatory.
Task: `workload-contract-repairs:T019`, a bounded merge-gate follow-up.

PR #472 at `52f2f9c4` failed both Linux Python 3.11 and 3.13 jobs in
[CI run 34000241193](https://github.com/fakoli/anvil-serving/actions/runs/34000241193).
Each reported one failure, 7211 passes and 27 skips. The failure was
`test_linux_require_readonly_refuses_owner_writable_file`: DID NOT RAISE.

The helper defaults `require_readonly` to False, correctly permitting an
owner-writable trusted configuration file. The refusal probe omitted True.
The implementation already rejects owner-writable files when True is supplied;
the existing cross-platform test also exercises that refusal. No runtime
permission change, default change or skipped-test workaround is warranted.

An unchanged Git archive extracted onto native Linux storage reproduced the
same DID NOT RAISE failure. A first probe from the mounted Windows checkout
instead encountered its untrusted-ancestor permission boundary; that was not
accepted as native Linux permission evidence.

The repair explicitly supplies True for refusal and adds the False-policy
positive assertion on the same actual owner-writable file. The existing native
chmod-to-owner-readonly positive case stays intact. The test still skips only
on non-Linux systems. No production, operator ACL, receiver, deployment or
unfinished enrollment behavior changes.

Native Linux Python 3.13.5 and Windows Python 3.13.13 focused suites each
passed 84 tests with six other-platform skips. Ruff and diff checks passed.
An in-memory negative control forced the existing permission predicate to
ignore require_readonly; the corrected native test failed DID NOT RAISE at
its True-policy refusal. No production file was modified for that probe.
