# Native Windows bootstrap CI fixtures

## Observed scope

PR #472 at `52f2f9c4`, hosted Windows run `34000241193`, reported six native
bootstrap failures. The first five trusted-reader failures reached the drive-root
permission check before any fixture child or leaf check. The matrix leaf check
reported `UNTRUSTED_WRITABLE` after an incremental `icacls` grant. The runner's
specific owner and DACL identities were not established and are intentionally
not inferred here.

## Candidate-only repair

Tests now create a unique disposable subtree under the current Windows user's
profile. Native `SetNamedSecurityInfoW` sets that new root's process-user owner
and protected, inheritable full-control DACL. Later matrix changes replace only
the DACL on an owned direct-child file, preserving one borrowed descriptor and
its offset. The fixture rejects any target outside that root and reparse/symlink
or hard-link targets before native ACL APIs; it restores full control before
cleanup. This is test-fixture work only: no runtime, deployment, route, or
production permission policy changed.

The matrix first seeds an Everyone write grant, observes it as untrusted, then
replaces it with owner-readonly to prove that exact replacement clears the
unwanted grant. It also covers owner-write and owner-read plus Everyone-write.
Final hosted Windows and Linux CI remain the integration proof.

The Windows focused suite passed 87 tests with six other-platform skips;
Ruff and the diff check passed. Root independently reviewed the four-file
fixture-only delta and repeated those gates. Setting the in-memory untrusted
mutation mask to zero made the native matrix fail its first Everyone-write
assertion; production source was never modified for that negative control.

## API references

- [SetNamedSecurityInfoW](https://learn.microsoft.com/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow)
- [ConvertStringSecurityDescriptorToSecurityDescriptorW](https://learn.microsoft.com/windows/win32/api/sddl/nf-sddl-convertstringsecuritydescriptortosecuritydescriptorw)
- [GetSecurityDescriptorDacl](https://learn.microsoft.com/windows/win32/api/securitybaseapi/nf-securitybaseapi-getsecuritydescriptordacl)
