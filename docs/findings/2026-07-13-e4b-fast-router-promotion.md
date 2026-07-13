# Gemma 4 E4B Fast router promotion + harness lockstep (gpu-reservations:T007)

**Point-in-time record, 2026-07-13.** The live fakoli-dark router config was promoted to the
Gemma 4 E4B fast tier prepared by gpu-reservations:T006, the quality profile was reseeded for
the new serve identity (chat-fast rows added, calibration pending), and the OpenClaw harness
config on the fakoli-mini gateway was synced in the same change. Heavy and Voice were not
changed.

## What was promoted

| Field | Value |
|---|---|
| Fast tier model | `gemma4-e4b-it` on `http://host.docker.internal:30003/v1` (RTX 5090, T006 serve) |
| Router config | `examples/fakoli-dark/anvil-router.live.toml`, `mapping_version = 2026-07-13.fakoli-dark-gemma4-e4b-fast` |
| Profile | [promotion-profile.json](2026-07-13-e4b-fast-router-promotion-evidence/promotion-profile.json), mode `operator-promotion-e4b-fast` |
| fast-local serve fingerprint | `586942fa32804069e645db5bde98ae323f41c49a44997f7d016cfd7019881272` (computed via `anvil_serving.router.fingerprint.serve_fingerprint` from the promoted tier) |
| Promotion path | `anvil-serving router promote --profile ... --config ... --confirm` — image-loader validate, volume backup, atomic write, reload, crash-loop verify (all passed; `--validate-only` passed first) |

The pre-promotion drift this closes: the T006 serve swap put `gemma4-e4b-it` on :30003, but the
deployed router volume still requested `qwen36-35b-a3b-nvfp4` from that endpoint
(`mapping_version = 2026-07-12.fakoli-dark-thinkingcap-heavy`), so fast-tier requests could not
be served by the resident model.

## Quality profile: seeded for calibration, not measured

Every fast-local row is pre-stamped with the NEW serve fingerprint and carries
`sample_n = 0` / `last_measured = null` / `quality_score = null` — explicitly **uncalibrated**
seed verdicts pending measurement (gpu-reservations:T008 runs the voice-consult benchmark
against E4B; `anvil-serving eval calibrate` is the measured write-back path). Verdicts keep the
prior operator posture: fast-local `deny` for planning / multi-file-refactor / long-context /
review, `allow` for bounded-edit / chat, plus new explicit `chat-fast` rows (`allow` on
fast-local, `allow-with-verify` on heavy-local — previously chat-fast was reachable only via the
built-in seed merge). Heavy rows are byte-identical to the prior promotion. Because the
fingerprint matches the live tier identity, startup re-stamping marks nothing stale.

## Live verification (decision log)

A live `chat-fast` request through the tailnet front door (`http://100.87.34.66:8000/v1`,
healthz 200 post-reload) returned the expected completion from the fast tier. The router
decision log ([chat-fast-decision-log.json](2026-07-13-e4b-fast-router-promotion-evidence/chat-fast-decision-log.json))
records: `work_class = chat-fast`, `requested_tiers = [fast-local, heavy-local]`,
`served_tier = fast-local`, `verifier_passed = true`, `fell_back = false`. The :30003 endpoint
serves only `gemma4-e4b-it` (`/v1/models`), so `served = fast-local` is the E4B serve.

## Harness lockstep (OpenClaw on fakoli-mini)

`anvil-serving harness sync openclaw --config examples/fakoli-dark/anvil-router.live.toml
--base-url http://100.87.34.66:8000/v1 --gateway-host fakoli-mini --confirm` merged the rendered
provider into `~/.openclaw/openclaw.json` (backup taken). Rendered preset `contextWindow` values
remain the LARGEST routed tier window per the clamp gotcha — 131072 for every preset, because
each preset (including chat-fast, fast-first) still routes to the 131072-token heavy tier; the
fast window stays 32768 inside the router. The merged gateway config was verified byte-identical
to the pre-sync backup (the E4B swap changes no preset topology or window), so a gateway restart
was intentionally skipped: OpenClaw reads config at startup and there was no delta to pick up.
Dry-run render: [harness-sync-dryrun.json](2026-07-13-e4b-fast-router-promotion-evidence/harness-sync-dryrun.json).

## Rollback

The promote path backed up the prior profile/config inside the `anvil-router-cfg` volume
(`config.toml.bak` / `profile.json.bak`); the serve-side rollback is the compose-profile
`fast-qwen36-rollback` service documented in `examples/fakoli-dark/serves.toml` (T006).

Raw artifact hashes: [SHA256SUMS](2026-07-13-e4b-fast-router-promotion-evidence/SHA256SUMS).
