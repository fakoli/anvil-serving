# Bound Docker process and image inventory

Status: open.

The campaign needed process and image identity for a managed candidate, but the
available inspection can traverse an unbounded Docker inventory. Do not make a
broad Docker listing the supported diagnostic path.

Add a managed, read-only inventory that accepts an explicit owner or candidate
scope, returns only bounded identity and lifecycle fields, and reports omitted
or out-of-scope records. It must not disclose unrelated containers,
environment, mounts, or command arguments.

Acceptance: fixtures prove owner filtering, fixed result and time limits,
redaction of sensitive fields, and an explicit incomplete result when the
limit is reached. A focused command test proves it uses no unrestricted
process/image traversal.
