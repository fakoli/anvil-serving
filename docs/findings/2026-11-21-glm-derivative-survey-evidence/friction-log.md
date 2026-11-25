# Friction log

| Area | Observation | Disposition |
|---|---|---|
| Qwen QAD R1 | All eight responses failed the opening-position canary. | Preserve as 0/8 eligible; do not calculate performance metrics. |
| Qwen QAD R2 and replay | R2 repeated the nonleading-marker boundary; the exact replay began with two newlines, putting the expected marker at offset 2. | Preserve 0/16 eligible; R3 functional passes do not repair the timing population. |
| AZ64 native image template | Image parts were converted to text reminders and all twelve image attempts returned pre-generation HTTP 500. | Preserve as a separate failed profile; no vision claim. |
| AZ327 image template | The exact Mia image-built template passed all twelve bounded image cases. | Keep distinct from AZ64; this is not broad vision qualification. |
| 327K/C4 comparison | AZ first-visible latency was slower, median end-to-end latency was near tied, and AZ aggregate output was lower. | Do not infer a broad concurrent-speed improvement from the summed C1 request-time result. |
| External comparability | The local pair uses a 16,384-token cap and a different reasoning-control surface from the publisher demonstration. | Treat the local pair as internally matched only. |
