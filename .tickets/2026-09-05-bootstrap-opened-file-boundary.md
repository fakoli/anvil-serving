# Bootstrap anchored file reads and Windows ancestor ownership

Status: implementation contract; source and native regression evidence pending.

The opened-descriptor permission primitive does not yet safely open receiver,
configuration or staging files. A path preflight followed by an ordinary open
can cross a replaced ancestor. The receiver needs retained no-follow ancestor
handles, bounded same-descriptor reads, identity/permission rechecks and fixed
errors before its dispatcher can claim trusted target identity.

Read-only Windows inspection also found a system-drive root owned by the fixed
TrustedInstaller service SID
`S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464`.
The current classifier returns `untrusted-writable` for that SID even with an
empty DACL in an ancestor-only fixture. Treating it as an arbitrary caller-owned
directory makes a normal Windows ancestor chain unusable. The bounded repair
trusts that exact OS-service SID only for ancestor ownership/grants; receiver
and configuration file trust stays unchanged. It must not trust every service
SID, skip root inspection, resolve localized account names, or alter OS ACLs.

The next reader is a read-only context manager, not staging or activation.
Its native handles remain open during repeated bounded reads. Linux uses
descriptor-relative no-follow traversal; Windows locks every prefix against
rename/deletion and opens reparse objects without following them. File content
and permission observations are checked on the same handle before and after a
read. Links, hardlinks, nonregular objects, untrusted writers, oversized content
and identity drift refuse. No receiver invocation, installation, promotion or
live deployment is proved by this primitive.

API mechanics are documented by Microsoft's
[CreateFileW reference](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
and Python's [descriptor-relative file operations](https://docs.python.org/3/library/os.html#os.open).
The ancestor trust policy is Anvil's explicit bounded choice, not a guarantee
that an OS API alone eliminates path races.

T004.8 now recognizes only that exact SID for ancestor classification. Literal
tests keep ordinary file trust unchanged, reject one-component SID changes,
and retain independent rejection of unrelated mutation grants and malformed
ACLs. No native permissions or paths were modified. This is candidate source
pending consolidated acceptance; the anchored file reader remains separate.

T004.9 adds the read-only held-file primitive only. It retains opened ancestor
and final descriptors while verifying permissions, identity and bounded bytes;
it does not load target configuration, dispatch a receiver request, persist a
record, stage an artifact, activate a generation or alter permissions.
