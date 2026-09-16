# Request-to-evidence coverage

| Requested outcome | Retained evidence | Status | Claim allowed |
|---|---|---|---|
| Image smoke and replay | `image-job.json`, `image-replay.json`, review v1 | covered | one fixed image completed and replay reused it |
| Artifact ownership boundary | `artifact-owner-refusal.json` | covered | unrelated principal was refused |
| Wan v1 video | native v1 result and review v1 | rejected | decodable functional output; `quality_failed`/unavailable |
| Wan v2 small repair | native v2 result and review v2 | partial | recognizable content recovered; `quality_unverified`/unavailable |
| Wan v2 standard diagnostic | native standard result and standard review | partial | no major collapse, but preview did not pass full quality |
| Final worker state | `final-status.json`, `restoration.json` | covered | worker running/200; human-approved retained running state |