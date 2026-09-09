# Request-to-evidence coverage

| Requested outcome | Evidence | Status | Remaining condition |
|---|---|---|---|
| Check newer Ormandj v0.4.2 | source-registry.json, candidate/image-identity.json | Covered | Exact release/image/source verified |
| Validate and compare | comparison.json, candidate native artifacts | Partial qualification | Faster eligible workloads; strict turnover fails in both candidate runs |
| Promote if better or on par | summary.json | Held | Frozen strict gate requires all assertions; no human exception recorded |
| PR and merge qualified release | publication bundle in isolated publication worktree | Held | No candidate deployment PR/merge until promotion criterion resolved |
| Retain working serving | restoration.json | Covered | Exact old image recreated; direct and authenticated routed smoke/JSON passed; primary-local readmitted |
