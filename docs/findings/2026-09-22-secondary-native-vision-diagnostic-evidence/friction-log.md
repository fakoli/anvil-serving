# Campaign friction log

| Time | Stage | Category | Earliest actionable evidence | Immediate disposition | Durable fix-forward artifact | Independent verification | Status |
|---|---|---|---|---|---|---|---|
| 2026-09-22 | scout | ambiguous-output | `adversarial-2` contains an extra Invoice label | Preserve failed whole-answer result | Frozen corpus and exact validator remain retained | Independent Sol review | open: qualification remains unmet |
| 2026-09-22 | preflight | request-contract | Explicit thinking option was rejected before inference | Use the retained native-policy request shape | `secondary-preflight-native-policy.sanitized.json` | Separate 1024-token preflight | closed for this diagnostic only |
| 2026-09-22 | preflight | finish-length | The 512-token native-policy general-image response ended with `finish='length'` | Preserve the failed policy result; do not treat substring presence as complete output | `secondary-preflight-native-policy.sanitized.json` | Separate 1024-token native-policy preflight ended with `finish='stop'` | open: 512-token completion limit is insufficient for this probe |
| 2026-09-22 | publication | tool-gap | Generic evidence-show rejects the multimodal schema | Preserve the native schema; do not coerce it | This draft's native artifact retention | Raw schema remains unchanged | open: generic tool support gap |
