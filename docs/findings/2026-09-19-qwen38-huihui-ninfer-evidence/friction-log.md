# Campaign friction log

| Stage | Evidence and disposition | Status |
|---|---|---|
| Protocol | [Initial smoke](preflight-nospec-smoke.json) rejected `chat_template_kwargs` with `chat_template_option_not_supported`; omit unsupported field and use declared server default. [Retry](preflight-nospec-server-control.json) passed. | Resolved for scout |
| Quality | [Off](quality-nospec-8k.json) and [requested reasoning-low](quality-nospec-reasoning-low.json) both fail string ZIP tools0/3; [incumbent](quality-incumbent-control.json) passes3/3. Reject exact profile. | Rejected |
| Diagnosis | Invalid raw arguments are discarded; parser explanation remains a hypothesis. Boundary probes beyond this ZIP test were not run. | Deferred; new ticket |
| Vision | [Initial](multimodal-nospec-8k.json) says `vision_disabled`; explicit `--vision` recipe reload [passes12/12](multimodal-nospec-8k-vision.json). | Fixed configuration |
| Identity | Initial runtime cache trusted a marker. Current recipe verifies locally observed executable SHA; native image artifacts still have null engine_build_ref. Companion configuration/logs bind reload. | Scout identity improved; immutable rebuild unresolved |
| Product gap | Managed provenance cannot read compiled executable hash. One bounded read-only container sha256sum was used; provenance ticket records it. No lifecycle bypass. | Open |
| Host diagnostics | Default WSL distro is unavailable, and Docker distro lacks expected nvidia-smi path. Host GPU query used; no WSL reset or repair. | Outside qualification |
| Infrastructure | Live ai-infra importer already uses registry sources. NInfer has no qualified telemetry adapter. Keep live mappings unchanged after rejection. | No deployment |
| Restoration | Both media services healthy200; original stopped incumbent preserved. | Verified |
