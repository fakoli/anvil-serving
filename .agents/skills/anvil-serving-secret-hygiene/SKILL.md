---
name: anvil-serving-secret-hygiene
description: Audit and remediate secret exposure in the Anvil Serving repository and its deployment artifacts. Use for credential reviews, public-evidence redaction, env-only configuration checks, Gitleaks setup or triage, pre-publish security gates, suspected token leaks, multi-host OpenClaw credential rotation, and decisions about credential rotation or Git-history cleanup.
---

# Anvil Serving Secret Hygiene

Protect the current tree without overstating what has been proven about Git history or live credentials. Never print, quote, copy, or commit a secret while investigating it.

## Safety invariants

- Preserve unrelated dirty work. Record `git status --short` before editing.
- Keep credentials in environment variables or an operator-local ignored env file. Repository files may contain variable names and placeholders, never values.
- Treat everything under `docs/` as public. Apply the repository's published-topology policy as well as credential redaction.
- Report findings as kind, path, line, and count only. Never include the matched value, a credential-bearing URL, or surrounding text that reveals it.
- A clean current snapshot does not imply clean Git history, and neither proves a leaked live credential was rotated.
- Do not commit, push, rotate/revoke credentials, rewrite history, or force-push unless the user authorized that distinct action.

## Workflow

### 1. Orient and inventory

Read `AGENTS.md`, `README.md`, `CLAUDE.md`, `.gitignore`, and every file to be changed. Then inspect, without exposing values:

1. Tracked and untracked files from Git, including sensitive filenames.
2. Whether local env files are ignored and untracked.
3. Whether any Git remote URL contains embedded credentials; report only a boolean/count.
4. The current diff and existing secret-scanner configuration.

Run the bundled semantic check from the repository root:

```powershell
python .agents/skills/anvil-serving-secret-hygiene/scripts/semantic_secret_scan.py
```

It scans tracked and non-ignored untracked files and emits metadata-only JSON. Use `--scope tracked` or `--scope untracked` to isolate a surface.
Tracked text files that are unreadable, symlinked, binary despite a text
extension, or larger than the bounded scan limit are findings; the semantic
gate must never silently skip them.

### 2. Run independent signature scans

Use the repository's pinned Gitleaks container digest and `.gitleaks.toml`. Keep reports in an ignored scratch directory and do not display raw reports because they may contain secret material.

Scan these surfaces separately:

1. A clean candidate snapshot containing tracked files plus the intended change.
2. Non-ignored untracked files.
3. Full Git history with `gitleaks git`.

Use `--redact` and summarize the resulting report programmatically by rule, path, and count. Do not run a mutable floating image tag when a repository-pinned digest exists.

### 3. Triage findings

Classify each finding as an actual credential, an intentionally synthetic fixture, or a false positive. Inspect the smallest possible masked context.

- Prefer exact-value or narrowly scoped path-and-field allowlists for verified fixtures.
- Never add a broad directory, extension, or generic-token allowlist merely to make a scan pass.
- Treat capability-bearing URLs, session tokens, device tokens, and private-key material as secrets even when a generic scanner misses them.
- Record separate states for current tracked snapshot, untracked files, Git history, and live credential rotation.

### 4. Remediate the current snapshot

Use `apply_patch` when the secret is not present in the patch text. If a patch would echo an actual secret, use a bounded redaction operation that does not print command input or output, then immediately rescan.

- Replace exposed values with `<redacted>` or an env reference as appropriate.
- Replace capability-bearing URLs with a non-capability placeholder.
- For published docs, also replace real network identities with the generic values required by `AGENTS.md`.
- Keep `.env.example` limited to names and safe placeholders.

Treat an exposed credential as compromised even after current-file redaction. Identify its owning system and record rotation as incomplete until independently confirmed.

### 5. Handle destructive or external actions

Credential rotation/revocation can terminate sessions or services. Confirm the exact credential owner and user authorization before performing it when the target or impact is uncertain.

Git-history rewriting is a separate destructive operation. Before proceeding, require explicit authorization and prepare a coordinated plan covering backup, affected refs, collaborator notification, force-push, fresh-clone verification, and credential rotation. Never claim the repository is fully clean while historical findings remain.

### 6. Rotate live OpenClaw credentials

Treat every OpenClaw host as an independent credential owner until live inventory proves otherwise. A machine may run its own gateway, consume another gateway, run a node service, or combine those roles. Respect an explicit host exclusion even when that host appeared in an earlier rotation.

#### Inventory without values

On each in-scope host, record only:

1. Host identity and installed OpenClaw version.
2. Gateway mode, bind mode, service state, and RPC readiness.
3. Whether `gateway.auth.token` is plaintext, an env SecretRef, or a file SecretRef; for a reference, record only `source`, `provider`, and `id`.
4. Secret-provider type, config/secret-store modes, and whether the expected env variable name is present. Never emit its value.
5. Paired device IDs, display names, roles, scopes, and pending count with all token fields removed.
6. Local `identity/device-auth.json` device ID, token roles, metadata timestamps, and file mode without token values.

Classify credentials before mutation:

- The shared gateway token authenticates gateway clients but has no device identity or administrative device scope by itself.
- Device-role tokens belong to a specific device and role.
- Capability-bearing URLs are short-lived bearer secrets; invalidate and probe them separately.
- Provider credentials such as Discord, Brave, router, controller, or model API keys are separate systems. Do not rotate them merely because they are consumed by OpenClaw.
- The device private key is identity material. Do not replace it unless compromise or explicit identity replacement is in scope.

#### Rotate a gateway token transactionally

1. Read the current token only inside the owning host process and retain it in memory for rollback and a negative probe.
2. Generate the replacement on the owning host.
3. If configuration uses a file SecretRef, atomically update only the referenced JSON pointer in the owner-only secret store and preserve mode `0600`. Keep the config reference unchanged.
4. If configuration uses an env source, stage the owner-only service env first. While the old credential source still works, refresh the installed service plan and prove the service actually loads that file/key. Only then change `gateway.auth.token` to the env reference and restart again. Switching config before launchd knows the new key can trigger an immediate hot-reload crash loop. Schema validation alone is not runtime proof.
5. If configuration uses plaintext, prefer migration to an already proven SecretRef provider. If the installed OpenClaw release validates but cannot boot with that reference, roll back and rotate through the known-working `0600` configuration; report the runtime limitation explicitly.
6. Refresh the managed service plan only when its environment inputs changed, restart the gateway, and wait through a bounded startup window.
7. Require all of: new-token health succeeds, old-token health fails, service is running, config is valid, and RPC is ready.
8. On any failure, atomically restore the original config/secret bytes and service plan, restart with the prior credential, and report whether rollback health passed.
9. After acceptance, replace the revoked value only in narrowly matched OpenClaw-generated config or secret backups. Do not broadly rewrite the home directory.

OpenClaw runtime behavior is version-sensitive. A controlled `2026.7.1-2` loopback probe proved both explicit env and file SecretRefs can boot and authenticate. The observed Mini production failure happened because config changed to an env reference before the installed launchd plan carried the new env key; config hot reload entered a restart loop and rollback restored service. Do not misclassify this as a SecretRef runtime defect. When an owner-only JSON provider already exists, prefer the simpler file reference used by the working deployment: store the value at `/gateway/authToken`, point `gateway.auth.token` to that file provider, restart, and migrate plaintext-bearing config backups.

#### Rotate a device-role token through its authorization boundary

Before calling `openclaw devices rotate`, inspect the installed gateway's authorization behavior and the caller's device ID, role, and scopes.

- Rotation must be performed through a supported authenticated device-management path. Do not edit OpenClaw's SQLite pairing tables.
- Capture rotation JSON to memory or an owner-only temporary file, extract the replacement without printing it, atomically update the matching role in `identity/device-auth.json`, preserve mode `0600`, then restart the node/client service.
- Verify the same device ID reconnects after the rotation timestamp with the expected authentication reason. Confirm unrelated paired devices and pending requests are unchanged.
- If the server token rotates but local persistence fails, immediately self-rotate once more using the returned token, persist that recovery token, and restart. Never restore the now-invalid original token.

Observed OpenClaw `2026.7.2` policy requires special care:

- A shared gateway token has no `callerDeviceId`, even though it can list devices.
- A non-admin caller may manage only an `operator` role token. Rotating a `node` token requires an authenticated `operator.admin` device.
- Self-rotation returns the replacement only when the caller device ID matches the target device ID.

When the required admin device is absent, stop after the denial, preserve the existing working node credential, and report `human_required`. Use an approved admin device or OpenClaw's supported bootstrap/re-pair flow; do not bypass the policy with direct database writes.

#### Sequence multiple hosts

For a gateway plus remote clients, rotate the gateway owner first, securely update authorized consumers, then rotate device-role tokens one client at a time. For hosts that each own a gateway, use independent transactions and independent old-token rejection probes. Never reuse one replacement across hosts.

Restarting a gateway invalidates process-bound capability URLs. Probe any known exposed capability path after restart and require a rejection such as HTTP 401/403/404/410; a connection error is not proof. End with gateway, node, MCP/controller, and paired-device checks appropriate to that host.

### 7. Add prevention

When requested or missing, maintain:

- `.gitleaks.toml` extending defaults with only evidence-backed narrow allowlists.
- A CI workflow pinned to an immutable Gitleaks image digest.
- Current-snapshot scanning that includes the proposed committed state.

Do not enable a blocking full-history CI gate until known historical findings are cleaned or intentionally baselined through a reviewed policy.

### 8. Verify and report

Run:

1. The bundled semantic scanner; require zero unexplained findings.
2. Pinned Gitleaks against the candidate current snapshot; require zero unexplained findings.
3. JSON/YAML parsing, relevant tests, and `git diff --check` for changed artifacts.
4. A full-history scan, reported independently from the current-snapshot gate.

Finish with a compact matrix showing `clean`, `findings remain`, `human_required`, or `not run` for current tracked files, untracked files, history, each host's gateway token, each device-role token, capability URLs, CI guard, and publication state. State exactly what was changed, what remains risky, whether rollback was exercised, and whether anything was committed or pushed.
