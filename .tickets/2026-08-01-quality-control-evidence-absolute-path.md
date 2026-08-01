# Quality evidence leaked absolute control-proof paths

Status: fixed locally

## Symptom

`eval benchmark quality --control-evidence` resolved the supplied path before
both reading it and serializing the protocol metadata. A portable repository
relative reference therefore became an absolute operator-workstation path in
otherwise publishable raw JSON.

## Fix

Resolve and expand the path only for bounded file I/O. Preserve the exact
operator-supplied reference in evidence, normalizing path separators to `/`.
A regression test proves that reading and hashing still use the resolved file
while the retained reference stays repository-relative.
