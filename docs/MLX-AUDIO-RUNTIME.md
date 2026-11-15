# Managed native MLX Audio runtime

Anvil Serving's native audio launcher runs one exact local Hugging Face snapshot
with the installed MLX Audio engine. The operator CLI remains stdlib-only. The
external audio environment owns MLX, MLX Audio, FastAPI, uvicorn, and their
dependencies; importing `anvil_serving.voice.audio_runtime` does not import them.

The supported upstream pin is MLX Audio **0.5.8**, Git commit
`70f4add32911bab6f869b824864ad9f1e24dcb97`, from
[Blaizzy/mlx-audio](https://github.com/Blaizzy/mlx-audio/tree/70f4add32911bab6f869b824864ad9f1e24dcb97).
Startup requires both that distribution version and matching Git provenance in
its installed `direct_url.json`. A version-only wheel or another source commit
fails closed. `--mlx-audio-version` and `--mlx-audio-commit` expose this reviewed
pair explicitly; other pins require a reviewed adapter update.

Acquire the exact artifact through
[`models pull --cache-dir`](cli/models.md#artifact-pull), retaining its per-file
hash verification and immutable `snapshot_path`. Bind that absolute path as the
service's model identity and as the audio client's `model` value. The launcher
requires `models--OWNER--REPO/snapshots/40_HEX_COMMIT`, readable model config,
nonempty local safetensors or NPZ weights, and all shards named by a weight
index. It refuses directory symlinks, unsafe file links, broken blobs, mutable
refs, and incomplete files. Startup validation complements the pull's hash
evidence; it does not replace artifact acquisition or endpoint qualification.

Declare the external Python and launcher file in the operator-owned LaunchAgent's
`ProgramArguments`. The launcher is a standalone module file, so the external
environment does not need Anvil installed merely to run it:

```xml
<key>ProgramArguments</key>
<array>
  <string>/operator/audio-runtime/bin/python</string>
  <string>/operator/anvil-serving/anvil_serving/voice/audio_runtime.py</string>
  <string>--model</string>
  <string>/operator/model-cache/hub/models--OWNER--REPO/snapshots/40_HEX_COMMIT</string>
  <string>--host</string><string>127.0.0.1</string>
  <string>--port</string><string>30101</string>
  <string>--mlx-audio-version</string><string>0.5.8</string>
  <string>--mlx-audio-commit</string><string>70f4add32911bab6f869b824864ad9f1e24dcb97</string>
</array>
```

Use [Host-supervised services](HOST-SERVICES.md) for the complete pinned
LaunchAgent and `services.toml` declaration. Its native binding uses
`engine = "mlx-audio"`, the same absolute snapshot as `model`, a positive
`memory_mib` budget, and the declared loopback endpoint. Readiness and model
inventory use `/v1/models`. Commands belong in the pinned supervisor definition,
not in the service binding's fields.

Start, inspect and stop that declaration with the supported host service verbs:

```bash
anvil-serving host services up SERVICE --dry-run
anvil-serving host services up SERVICE --no-dry-run --confirm
anvil-serving host services status SERVICE
anvil-serving host services logs SERVICE --tail 100
anvil-serving host services down SERVICE --no-dry-run --confirm
```

The runtime binds only `127.0.0.1`, uses one uvicorn worker with reload disabled,
and runs the upstream application in the same process. During its composed
startup lifespan it submits `model-load` through the real upstream
`InferenceBroker`; the broker loads on its inference worker's MLX streams.
The launcher waits for actual completion and requires the real provider cache
to advertise exactly the configured snapshot. Only then does startup yield and
uvicorn accept traffic. `GET /v1/models` therefore reports the model that was
actually loaded, rather than synthetic readiness metadata.

The provider retains upstream caching and object semantics while allowing only
the exact configured snapshot identity. Requests for remote repositories,
another local model, or removal of the pinned model are refused. Runtime Hub
and Transformers access is offline, inherited HF token variables are removed,
and implicit saved-token use is disabled. Any required ancillary model assets
must be acquired and recorded before starting the service.

Uvicorn owns SIGTERM/SIGINT handling. Shutdown requested during preload cancels
the queued handle; startup failure and ordinary shutdown both unwind the original
upstream lifespan, which stops and joins the inference broker. The startup
deadline defaults to 600 seconds; `--startup-timeout SECONDS` permits a positive
bound through 3600 seconds. A failed preload never becomes a ready service and
exits unsuccessfully. The uvicorn graceful shutdown bound is 30 seconds.

Use supervisor-owned stdout/stderr logs for normal operation. Optional
`--log-dir /operator/audio-logs` appends runtime and uvicorn logs to
`mlx-audio-runtime.log`; existing logs remain available. Runtime launch and
readiness do not qualify audio quality or promote a voice route. Run the
independent endpoint preflight and retained voice benchmark next.

The launcher records policy version `mlx-audio-launcher/v2`. Retain its file
SHA-256 and complete supervisor arguments alongside the upstream and model pins
when comparing runs. An unset allocation option preserves MLX's defaults.

For an explicit free-buffer cache policy, add `--mlx-cache-limit-mib 256` to the
supervisor arguments. `--mlx-cache-limit-mib 0` disables retained free buffers;
the accepted range is 0 through 1,048,576 MiB. This option requires reviewed MLX
**0.32.3** and is applied once on the inference worker before model preload.
The startup JSON policy record includes the selected limit, previous limit in
bytes, tracing state, and application boundary. MLX reclaims excess cached free
buffers on subsequent allocation. The policy does not limit live model weights,
KV caches, Python/NumPy allocations, or total process/system memory. See
[MLX cache-limit semantics](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_cache_limit.html).

Add `--mlx-memory-trace` for JSON `mlx_generation_memory` records around each
serial TTS or STT `model.generate` call. Records identify the local request
index, start/end phase, completion/error/close outcome, and active, free-cache,
and peak bytes. Generation results and signatures retain upstream semantics.
Measurements run on the generation's worker thread; finalization on another
thread records an unavailable measurement instead of calling MLX there. Tracing
is bound to the actual preload worker. Calls from another thread bypass tracing
and record an unavailable measurement, preserving the original generation call.
Tracing does not cover a model's separate `batch_generate` path or realtime sessions.
It records no input text, reference audio, voice parameters, or credentials.

Tracing resets the allocator peak immediately before each serial generation;
the end record therefore reports that request's MLX peak, while the start record
retains the prior high-water mark. This explicit diagnostic policy changes the
scope of upstream `GenerationResult.peak_memory_usage`; record it as a separate
configuration when comparing cold and warm runs. These are allocator metrics,
not process RSS or host swap. Pair them with timestamped process RSS/physical
footprint and host memory-pressure observations. See
[MLX active memory](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.get_active_memory.html)
and [MLX peak memory](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.get_peak_memory.html).

The native service binding's `memory_mib` remains an admission budget. The
launcher does not convert it to a hard allocator cap: MLX 0.32.3's
[`set_memory_limit`](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_memory_limit.html)
is a guideline that can continue using available RAM and swap. Cache changes
also do not change generation length. Preserve and record request-side token
limits independently, and treat cap-hit audio as incomplete until independently
checked for truncation or repetition.
