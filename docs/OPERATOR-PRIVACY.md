# Public Product and Private Operator State

The `anvil-serving` repository is a public product surface. Assume every
tracked file can be published, even when it is outside `docs/`.

## What stays public

- source code, schemas, tests, and release automation;
- generic examples and packaged `init` templates;
- portable serve recipes and qualification methodology;
- sanitized, bounded evidence admitted under
  [ADR-0027](adr/0027-public-findings-are-durable-evidence.md).

A public recipe may say that a model was qualified on a described hardware
class. It does not reveal or control which model a particular operator has
currently promoted.

## What belongs in the private companion repository

Use a separately access-controlled repository for real topology, deployment
overlays, active route assignments, GPU UUIDs, machine-local paths, operator
runbooks, and working evidence. A small layout is sufficient:

```text
anvil-serving-ops-private/
├── hosts/
│   ├── node-a/operator-home/  # one private ANVIL_SERVING_HOME
│   └── node-b/operator-home/  # another private ANVIL_SERVING_HOME
├── evidence/            # private raw/working evidence
├── runbooks/            # operator-only procedures and decisions
├── migration/           # inventories and rollback manifests
└── README.md
```

Do not commit credentials to either repository. Private configuration should
refer to environment variables or file-backed secrets. Keep `.env`, secret
files, caches, generated locks, captures, and backup files ignored.

Portable recipe definitions remain in the public product catalog and its
packaged copy. Each host may also track an operator-owned
`serve-recipes.toml` under its private operator home for local recipes and
overrides. Recipe locks and numbered backups are runtime state and remain
ignored. Active route assignments stay in the private manifests; a recipe's
presence or historical qualification text is not a promotion claim.

On PowerShell, select one private host root for the current process:

```powershell
$env:ANVIL_SERVING_HOME = 'C:\path\to\anvil-serving-ops-private\hosts\node-a\operator-home'
anvil-serving doctor
```

On Bash:

```bash
export ANVIL_SERVING_HOME=/path/to/anvil-serving-ops-private/hosts/node-a/operator-home
anvil-serving doctor
```

Run `anvil-serving init` only after selecting the intended private root. It can
detect and write real local values there; it must never write them back into
the public examples.

## Typed inventory and private-repository handoff

Use the typed host configuration surface to discover and transfer operator
configuration. Do not recursively copy an operator home or retrieve it through
an ad hoc SSH, SCP, rsync, archive, Docker, or filesystem command.

On the host that owns the operator home, inventory first:

```bash
anvil-serving host config inventory --json
anvil-serving host config export --path router.toml --json
anvil-serving host config export --path serves.toml --json
```

Inventory is metadata-only. It reports relative paths, classifications, byte
sizes, parser types, dependency edges, installed product/protocol revisions,
and SHA-256 digests for versionable candidates. Excluded files are not opened
for hashing and report a null digest. Inventory does not return file contents
or environment values. Export returns content only for supported versionable Anvil
configuration. A selected export automatically includes its transitive dependency
closure. OpenClaw configuration is a separate, explicit input whose output is
limited to an allowlisted and redacted Anvil-owned fragment; the complete
`openclaw.json` document is never an export artifact.

The same operations are available through the authenticated controller as
`operator_config_inventory` and `operator_config_export`. Remote callers cannot
override the resource owner's operator-home or gateway paths. The owning host
resolves its configured `ANVIL_SERVING_HOME` and standard OpenClaw path, which
keeps the control plane from becoming an arbitrary remote file reader.

A lifecycle `--registry` under the declared product `/configs` mount or the
source checkout's public `configs/` directory is represented by the fixed
`<external-product-registry>` marker and is never read through this surface.
Arbitrary external registries fail closed. Portable product recipes remain in
the public repository. A private registry that must move with the operator
configuration belongs inside the operator home, where selected export includes
it in the transitive closure.

The operations fail closed on symlinks or junctions, path escapes, unreadable
or oversized files, parse failures, unresolved dependencies, unsupported YAML
exports, and credential-like or capability-bearing values. Secret material,
runtime databases and logs, backups, caches, cookie stores, and unknown files
remain excluded. A private repository is not an exception to the credential
rule: exported configuration must still use environment or file-backed secret
references.

Treat the export as a reviewed handoff, not as a remote-copy shortcut:

1. record the inventory and the explicitly selected paths;
2. review the returned dependency closure, exclusions, redaction count, and
   hashes;
3. place only the reviewed `files` entries under
   `hosts/<host>/operator-home/` in the private repository, preserving their
   relative paths;
4. run the private repository's ignore and secret-hygiene checks, then compare
   the tracked snapshot with a fresh typed inventory; and
5. commit the configuration in the private repository before planning any live
   operator-home cutover.

Inventory and export are read-only. They do not write configuration, switch
`ANVIL_SERVING_HOME`, restart a component, deploy a manifest, change a route,
or promote a model. A live cutover remains a separate reviewed operation with
an explicit rollback manifest.

## Publishing evidence

Keep raw logs and session traces private while working. To make a public claim:

1. select only the bounded evidence needed to audit the claim;
2. remove credentials, capability URLs, reachable network identities, personal
   paths, prompts, and unrelated logs;
3. record any topology redaction in the dated finding;
4. run the semantic boundary scan and Gitleaks before review; and
5. publish the sanitized artifact and narrative together under `docs/findings/`.

## Current snapshot versus Git history

Cleaning the checked-out files prevents new exposure but does not remove old
objects from Git history. Repository-history replacement is a separate
destructive migration. Follow
[the history-reset runbook](HISTORY-RESET-RUNBOOK.md) and obtain an explicit
approval at its execution gate before rewriting or recreating the repository.
