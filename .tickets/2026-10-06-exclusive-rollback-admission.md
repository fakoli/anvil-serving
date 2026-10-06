# Restore a declared exclusive rollback owner

Status: resolved in source; independent review accepted and full-suite verification passed.

A router-detached TP2 qualification candidate could drain an existing exclusive baseline, but mode leave or failed entry tried to restore that baseline through ordinary serve admission. Ordinary admission correctly rejects exclusive starts, so the declared rollback could not recover.

The managed rollback helper now grants exclusive admission only when the declared rollback group resolves to one exclusive serve. Existing state, competing-owner, target-count and resource checks still apply. Ordinary `serves up` remains unchanged.

Regression coverage proves successful leave and failed candidate entry restore and readmit the exact baseline without installing a router profile. Focused serve-management and reservation tests passed 110 cases. The full suite at commit `03b2c14e4b1dfc4eb4b0c2b3a72ffd76b564b502` passed 10,350 tests with 62 skips. Temporary directories used a short trusted path outside Git to satisfy custody checks and the Unix socket path limit.
