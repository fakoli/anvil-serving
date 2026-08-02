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
├── operator-home/       # selected by ANVIL_SERVING_HOME
├── evidence/            # private raw/working evidence
├── runbooks/            # operator-only procedures and decisions
└── README.md
```

Do not commit credentials to either repository. Private configuration should
refer to environment variables or file-backed secrets. Keep `.env`, secret
files, caches, generated locks, captures, and backup files ignored.

On PowerShell, select the private operator home for the current process:

```powershell
$env:ANVIL_SERVING_HOME = 'C:\path\to\anvil-serving-ops-private\operator-home'
anvil-serving doctor
```

On Bash:

```bash
export ANVIL_SERVING_HOME=/path/to/anvil-serving-ops-private/operator-home
anvil-serving doctor
```

Run `anvil-serving init` only after selecting the intended private root. It can
detect and write real local values there; it must never write them back into
the public examples.

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
