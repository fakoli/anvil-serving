# Request-to-evidence coverage

| Requested outcome | Evidence | Status | Remaining condition |
|---|---|---|---|
| Check newer Ormandj v0.4.2 | source-registry.json, candidate/image-identity.json | Covered | Exact release/image/source verified |
| Validate and compare | comparison.json, candidate native artifacts | Partial qualification | Faster eligible workloads; strict turnover fails in both candidate runs |
| Promote if better or on par | summary.json | Concluded | Strict turnover did not meet the frozen gate; user selected baseline retention |
| PR and merge qualified release | publication bundle in isolated publication worktree | Not applicable | Candidate is not qualified for production promotion |
| Retain working serving | restoration.json | Covered | Exact old image recreated; direct and authenticated routed smoke/JSON passed; primary-local readmitted |

| Final decision | User declined comparative exception | human-decision.json | Covered | Retain baseline/no-promotion; candidate remains unpromoted; other reports unverified |
