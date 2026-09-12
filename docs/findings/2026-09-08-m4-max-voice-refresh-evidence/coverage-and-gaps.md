# Request-to-evidence coverage

| Requested outcome | Required gate | Retained evidence | Status | Claim allowed | Gap or next action |
| --- | --- | --- | --- | --- | --- |
| 4B baseline behavior | preflight and spoken suite | `sanitized-preflight.json`, `sanitized-quality-and-capacity.json` | covered | 0/3 shared tools; 36/36 spoken strict | none |
| 9B functional compatibility | six preflight groups | `sanitized-preflight.json` | covered | 6/6 groups pass | no promotion implied |
| 9B voice suitability | spoken, diagnostic quality, strict capacity | `sanitized-quality-and-capacity.json` | rejected | 33/36, patchformat 0/3, capacity 0/10 | no performance claim |
| Voice path | bounded audio round trip and Realtime fence | `sanitized-voice-summary.json`, `native/kokoro082-audio-roundtrip.json`, `native/updated-live-final.session.json` | partial | one-sample WER and accepted final Realtime result | corpus/perceptual review and speed distribution absent |
| 27B local candidate | functional, capacity, quality | `sanitized-preflight.json` | partial | preflight 5/6; tools 1/3 | no capacity; candidate unload |
| 35B-A3B local candidate | physical and policy feasibility plus full gates | `feasibility-summary.json`, `sanitized-preflight.json`, `sanitized-quality-and-capacity.json`, `sanitized-voice-summary.json` | rejected | spoken 36/36 and one accepted SDK session, with strict JSON failure | no fully qualified replacement; policy envelope unresolved |
| Final restoration and audio update | post-run LLM restoration plus deployed TTS proof | `restoration.json`, `native/final-deployment-receipt.json` | covered | candidates unloaded, both candidate ports closed, Kokoro 0.8.2 and all four endpoints verified | no pre-campaign live byte digest; reboot, physical microphone/OpenClaw, and actual rollback execution untested |
