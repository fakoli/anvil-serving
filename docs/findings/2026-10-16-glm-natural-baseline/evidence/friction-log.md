# Friction log

| Stage | Observation | Disposition | Status |
|---|---|---|---|
| timing preparation | Fixed-context unique prompts originally did not vary with the seed when canaries were disabled. | Seed and request-index namespaces were bound before this matrix. | closed; source review retained separately |
| timing observation | Router workload snapshot was unavailable. | Retain shared-router limitation; do not claim traffic isolation. | open limitation |
| timing evidence | Successful timing rows omit finish reason, terminal form, raw usage objects, and visible character count. | Record [`errata.md`](errata.md); limit claims to visible output plus terminal observation. | open schema limitation |
| coding preparation | Fresh ten-task images are uncached while available storage margin is constrained. | Use the existing five pinned tasks twice; retain the fresh set as deferred. | open capacity limitation |
| post-run storage | 88,034,181,120 bytes free is below the 107,374,182,400-byte reserve; preflight had passed at 109,701,644,288 bytes. | Future trials are blocked pending separately owned storage work; no cleanup or pruning was performed. | open limitation; no attribution to SWE outcomes |
