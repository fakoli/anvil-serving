# Model runtime telemetry investigation — 2026-08-30

## Conclusion

The inspected local GLM-5.3 model snapshots did not contain executable model
repository code, and the live serve did not enable remote-code loading. No evidence
showed the Chinese-origin model weights sending prompts, completions, or model data.

The serving stack nevertheless was **not telemetry-free**. Its third-party
vLLM-derived runtime had the default usage reporter enabled. A bounded read-only
inspection found locally retained usage records and observed the runtime establish a
TLS connection to `stats.vllm.ai` at the next reporter heartbeat. The inspected
records contained an instance UUID plus hardware, platform, engine,
model-architecture, and launch-configuration metadata. None of the 93 retained
records contained prompt text, completion text, request bodies, or generated model
content.

The supported conclusion is therefore narrow:

- **Disproved:** “Nothing leaves a local model-serving container.”
- **Not proved:** “The model weights exfiltrate user content.”
- **Observed cause:** engine-level default usage telemetry, independent of model
  nationality.

The sanitized machine-readable observation is
[2026-08-30-model-runtime-telemetry-investigation.json](2026-08-30-model-runtime-telemetry-investigation.json).

## Inspected workload

| Item | Immutable identity |
| --- | --- |
| Target | `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1@319d66a8b53092b491f698440ecea781e4ddd4e4` |
| Draft | `incoai/GLM-5.3-Flash-DFlash2@dc77ff1c99eeb2df044ee3d4f0094eb033fee410` |
| Runtime image | `ghcr.io/tpurtell/glm-5.3-flash-exl3-4bpw-2x-rtx@sha256:001a45bd71bcf908a8c07459570bdb8c5e0a205d085f29ac7f3201529fa3eb75` |
| Public recipe | [`configs/glm53-flash-purtell-k3-dflash2-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml) |
| Anvil Serving source | `c592e0b7f32acd2e99919a8fa72fc7e860e23ceb` (`1.0.0`) |

The exact cached target and draft snapshots were inventoried by file type. They
contained weights and model/tokenizer/configuration assets, with no tracked Python,
native executable, `auto_map`, or equivalent remote-code hook. The runtime launch
configuration set Hugging Face offline mode and did not pass
`--trust-remote-code`. This narrows attribution: model-repository executable code
was not active, while third-party runtime code was.

## Method

The investigation was deliberately non-generating and read-only:

1. record the running serve identity and immutable recipe inputs;
2. inspect the target and draft snapshot file inventories and configuration hooks;
3. inspect the exact runtime's usage reporter source and its locally retained JSONL;
4. enumerate payload field classes without retaining UUIDs, host addresses, GPU
   identifiers, prompts, or response bodies in public evidence; and
5. observe the owning process's outbound connections across one documented
   heartbeat boundary.

No prompt was sent, no model was loaded or unloaded, no container was restarted,
and no route or deployment state changed.

## Attribution evidence

The current upstream vLLM implementation says usage statistics are enabled by
default, names `VLLM_NO_USAGE_STATS`, `VLLM_DO_NOT_TRACK`, `DO_NOT_TRACK`, and a
`do_not_track` file as opt-outs, posts to its configured usage server, and emits a
continuous heartbeat every 600 seconds. Its default server is
`https://stats.vllm.ai`. See the upstream
[usage reporter source](https://github.com/vllm-project/vllm/blob/main/vllm/usage/usage_lib.py),
[environment defaults](https://github.com/vllm-project/vllm/blob/main/vllm/envs.py),
and [usage-stat documentation](https://docs.vllm.ai/en/latest/serving/usage_stats.html).

The locally inspected reporter matched that behavior. The initial payload class
included CPU/GPU/platform and vLLM configuration metadata; heartbeat records reused
the instance UUID with timestamps and registered runtime counters. The destination,
interval, and local-record shape aligned with the reporter source. This is direct
local evidence of runtime telemetry and source-level attribution, not an inference
from the model publisher's country.

## Product response

[ADR-0043](../adr/0043-model-workloads-deny-network-egress-by-default.md)
makes long-running model workloads deny egress by default:

- managed recipes attach to a freshly verified Anvil-owned internal Docker bridge;
- Compose model services must resolve only to `internal: true` networks before
  lifecycle mutation;
- opaque launch scripts cannot claim default-deny;
- any allow exception requires a durable reason; and
- pre-policy stopped or paused workloads are recreated rather than blindly resumed.

The reference router remains dual-homed so it can reach model services on the
internal network and serve its explicit gateway role on a separate network. Model
services do not join the router's egress network.

## Limits

- Absence of prompt/completion fields in these 93 records does not prove that every
  runtime version, plugin, image, or future code path is safe.
- TLS destination and the locally mirrored reporter payload establish this reporter's
  behavior; they are not a general packet-content audit of all software in the image.
- An internal Docker network is a network egress control, not a malware sandbox. It
  does not constrain filesystem writes, GPU behavior, host processes, Docker-daemon
  image pulls, or workloads launched outside Anvil Serving.
- The source change does not retrofit a running container. Live adoption requires a
  separately authorized recreate and post-deployment verification.

## Publication safety

The public observation removes the runtime instance UUID, connection timestamps,
host network identities, container identity, GPU identifiers, and machine-local
paths. Raw working evidence remains outside Git and is not used to expose a
capability-bearing endpoint or private topology.
