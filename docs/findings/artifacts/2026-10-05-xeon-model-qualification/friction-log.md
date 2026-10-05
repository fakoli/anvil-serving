# Campaign friction log

| Time | Stage | Category | Evidence | Disposition | Status |
|---|---|---|---|---|---|
| 2026-10-05 | controlled output | Strict baseline and GLM C4 | Strict artifacts retain 0/4 performance-eligible requests. | Exclude timing from performance. | diagnostic retained |
| 2026-10-05 | matched A1 SWE | Quality gate | A1 has five attempts, four official grades, two resolutions, and one LimitsExceeded empty/ungraded submission. | Fail the 4/5 gate; preserve all evidence; do not retry. | closed no-promotion |
| 2026-10-05 | matched comparison | Early stop | [matched-swe-early-stop.json](matched-swe-early-stop.json) predates result review. | Do not run B1/B2/A2 for a speed rescue. | closed |
| 2026-10-05 | public evidence | Runtime-instance metadata | [matched-a1-source-status.json](matched-a1-source-status.json) originally carried a raw runtime-instance identifier. | Removed the identifier while retaining measured status fields; final inventory rehashed the sanitized receipt. | closed |
| 2026-10-05 | restoration | Direct/routed/Pi verification | [restoration.json](restoration.json) and linked receipts. | Exact r11 restored/readmitted; direct 25/25, routed 7/7, fresh Pi read-tool exact fixture, ready/admitting tiers, and unchanged router/model identity. | closed |
