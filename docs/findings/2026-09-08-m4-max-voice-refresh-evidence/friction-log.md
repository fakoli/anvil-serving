# Friction log

Publication note (2026-09-12): this is a retrospective record of September 8
failures and recovery. The native-dispatch and SDK-harness fixes belonged to
the historical working branch; this PR publishes documentation only and does
not establish that those fixes are released. They require separate current-code
review before operational reuse. Native evidence is retained without regrading.

- **Strict controlled output:** Qwen3.5-9B returned 28 instead of the requested
  32 code words in all ten unique-cache capacity requests. The canaries passed,
  but the requests are performance-ineligible. This is retained as a negative
  result; no speed claim is published.
- **Exact punctuation:** Qwen3.5-9B answered `Tea.` to the spoken-suite memory
  item, while the exact validator expected `tea`. It is semantically correct,
  but the native artifact remains 33/36 strict and is not regraded.
- **Realtime cancellation fence:** the original SDK capture saw one inflight
  transcript after a client cancel request. The historical dirty-worktree harness fenced cancellation
  at the standard `response.done` cancelled acknowledgement. Current main uses
  the client-send boundary; the change is excluded from this publication. Subsequent baseline,
  Qwen3.6, and deployed-TTS captures passed their own configurations; they do
  not constitute a same-model Qwen3.5 repeat. The original negative observation
  remains part of the campaign.
- **Managed proxy and readiness:** the stopped proxy was repaired through the
  managed surface; all four jobs were adopted into the private service inventory,
  dependencies were ordered, and STT readiness uses `/health`. No public host
  identity, service label, PID, or command line is retained.
- **Native dispatch (historical code change, release status unverified):** the
  working branch modified the native-recipe dispatcher to admit the Apple MLX
  lane without an NVIDIA GPU role while retaining the Docker GPU gate. Its
  historical tests are not validation of the current release. Operational
  reuse remains deferred pending separate code review and the native lifecycle
  regression gate.
- **Kokoro candidate startup:** the first 0.8.2 candidate definition resolved
  relative assets from the repository root and failed warmup. A pinned
  definition with absolute paths passed; the failure and the deployed 0.8.2
  receipt remain in native evidence. The update retained model/voice asset
  hashes and versioned rollback definitions.
- **Download and readiness discipline:** native candidate downloads remain
  managed recipe inputs, and only declared service dependencies/readiness
  checks may establish a voice-path claim. The proxy dependency receipt and
  STT `/health` correction are retained privately; public evidence records the
  bounded post-fix outcome rather than an operator command transcript.

Open limitations include current-code validation of those historical fixes,
corpus/perceptual audio coverage, and untested reboot, physical
microphone/OpenClaw, and actual rollback execution. A successful historical
retry is recovery evidence, not proof that an operational defect is closed in
the current product.
