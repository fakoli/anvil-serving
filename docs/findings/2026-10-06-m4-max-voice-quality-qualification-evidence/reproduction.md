# Reconstructing the exact campaign

This is a public reconstruction contract, not a one-command replay or authority to change services. Private native service definitions, endpoint routing, the operator-name prompt, loaded control checkpoint commits, and the private LLM cache0 fixture are not fully portable public inputs. Original hashes and gaps remain explicit. No current live-state or byte-identical rerun is claimed.

## Model and runtime identity

Use the exact repository/revision rows in the dated finding; never substitute an unpinned latest conversion. The candidate audio lane is MLX Audio0.5.8 from source70f4add32911bab6f869b824864ad9f1e24dcb97 with MLX0.32.3. The reviewed launcher file SHA is e7149c09668713835e1cd70725f2af78608ac023c72541f96462c61d7c924661; that is a file identity, not a Git commit. Stock LLM candidates use MLX-LM0.32.0 / MLX0.32.3; the retained 4B control is a separate existing0.31.3 configuration with an unattested loaded model revision.

For supported native acquisition, preview an exact immutable revision before applying:

```bash
anvil-serving models pull OWNER/REPO --revision 40_HEX_COMMIT --cache-dir /operator/model-cache/hub --no-token --dry-run
anvil-serving models pull OWNER/REPO --revision 40_HEX_COMMIT --cache-dir /operator/model-cache/hub --no-token --evidence-out /operator/evidence/native-pull.json --confirm --json
```

The native pull path verifies selected-file inventory/storage headroom, serializes cache writers, resumes existing bytes and verifies hashes. It never prunes cache or starts a model service. Reconstruct a managed definition with explicit immutable snapshot, source/interpreter identity and memory admission; use the documented preview/apply lifecycle gates. Admission is not a hard process memory cap. Local endpoint/PID bindings must be freshly attested before any measurement.

The supported audio module is run by the pinned external interpreter with exact local immutable snapshot and explicit loopback bind. Retry arms use `--mlx-cache-limit-mib 0 --mlx-memory-trace`; preserved default applies when omitted. Consult the product MLX Audio runtime documentation for source/distribution gates. MLX cache zero controls free buffers, not active memory. LLM cache0 experiments used a separately reviewed private fixture and cannot be recreated by inventing a supported product flag; durable packaged integration and new qualification are required before promotion.

## Workloads and schedules

`projections/corpus-canonical-manifest.jsonl` and `projections/corpus-supplemental-manifest.jsonl` retain dataset provenance, reference text, per-media hashes, duration and selection. Reconstruct licensed LibriSpeech inputs from their public dataset references and check original hashes; private audio paths are symbolic only. Canonical C1 is30cases×3, primary-human24×3; C4 is30×1, primary-human24×1. Supplemental120 human holdout cases run once. Keep language-conditioning and synthetic/human populations separate. Cold request is separate from warm summaries.

`projections/tts-corpus-60.jsonl` retains all60case IDs and categories, with one private operator name redacted. Its original SHA binds original text, not the sanitized placeholder. TTS uses one separate cold request and one warm request per distinct prompt; native voice, stream/buffer, sample-rate and token controls are configuration-specific. Normalize response PCM to16kHz mono for independent Parakeet fidelity transcription, retain returned PCM hashes and failure/EOF boundaries, and do not call ASR text human listening judgement. Blind pack is research-only with no votes; winner gates are at least60%non-tied preference and Wilson95lower>50%.

The semantic suite and its pre-inference freeze retain48cases×3 and complete deterministic oracles. The historical exact suite is separate12×3; punctuation is not silently excused. LLMtemperature0.2,C1; off native thinking-disabled source/request binding; low/medium global native preset and request merge source-verified. Strict capacity uses actual>=8192prompttokens, unique-cache canaries,128requested lowercase codewords,512total output tokens(off) or4608total(low/medium). Calibrationv1 was invalid; v2 fixes token accounting before candidate inference. The urllib timeout120 is socket inactivity, not overall wall deadline; total-work guard bounds are separate. Validation capture8192chars can make full adherence unobservable. Preserve canary-present-but-not-at-start and truncated-capture distinctions.

## Resource custody, restoration, and missing acceptance

Guards bind exact service/PID/model/source/spec and original before-up swap baseline, one resource writer,512MiBgrowth/free15%gates and separately bounded work. Cache0 low used one1800s arm; medium uses two separately closed1800s phases with the same original baseline/PID and a verified no-inference handover. Work closure does not assert tests passed or shut down the service; final managed down is separately evidenced. Native sample traces and audit bindings preserve resource scope and unknown startup/aborted peaks.

Final starting/ending service/config identity, candidate unload, one audio loop and authenticated two-turn Realtime smoke are independently bound in restoration.json and the final native audit projections. Cache inventory retains two unsafe Kokoro snapshots and unverified completeness for all12; no whole-cache safety or deletion claim. These point observations do not establish continuous protected endpoint uptime, microphone/accent quality, actual playback interruption<=250ms, blind naturalness preference, or100warmturns/1200scombined soak. No replacement passed the whole contract; no serve/route/catalog change is implied.

## Deterministic documents

The retained graph manifest uses matched STT native-schema derivatives only. Run the repository benchmark-docs plotting helper twice against the same frozen files and compare SVG and graph-data bytes; graph data hashes the public projections, and the projection ledger resolves original private hashes. Finalize the artifact-manifest source twice, compare bytes, and verify closed inventory. Tables declare separate unsupported TTS/LLM evidence classes; never rewrite their schemas or rank failed capacity. Product full tests were not green; the retained macOS triage records31failures rather than a release gate pass.
