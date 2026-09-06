# Fleet

[CLI overview](../CLI.md) · [Router commands](router.md)

The `fleet` family answers questions that span more than one declared host.
`fleet version` is cross-host `anvil-serving` version skew; `fleet drift` is
live-home-vs-repo-snapshot skew.

## Workloads

Read a bounded canonical fleet snapshot through one explicit controller
aggregator:

```bash
anvil-serving fleet workloads --controller-url http://127.0.0.1:8765 --auth-env ANVIL_WORKLOAD_TOKEN --expected-node controller-a --recent-seconds 3600 --limit 200
anvil-serving fleet workloads --controller-url http://127.0.0.1:8765 --auth-env ANVIL_WORKLOAD_TOKEN --expected-node controller-a --owner media --active-only --json
```

This command does not collect topology locally, use SSH, or treat an
unreachable node as idle. The controller identity must match
`--expected-node`, and the client credential must carry `workloads:read`.
`--host` filters returned records rather than selecting the aggregator. See
[workload visibility](../WORKLOAD-VISIBILITY.md) for query bounds, node/fleet
envelopes, source provenance, and honest partiality.

## Fleet version

```bash
anvil-serving fleet version
```

Without `--host`, hosts come from the operator topology (default
`$ANVIL_SERVING_HOME/operator-topology.toml`): every declared `[[hosts]]` id
except the local one, determined by a case-insensitive prefix match against
`socket.gethostname()`. Pass `--host NAME` (repeatable) to name hosts
explicitly instead, overriding the topology-derived list.

For each remote host it runs `ssh -n -o BatchMode=yes <host> anvil-serving
--version` (per-host timeout `--timeout`, default 10s) and reports:

| State | Meaning |
| --- | --- |
| `ok` | Version read successfully; compared against the local version. |
| `unreachable` | SSH failed to connect or the command otherwise errored. |
| `not-installed` | SSH connected but `anvil-serving` is not on that host's `PATH`. |
| `timeout` | The probe did not complete within `--timeout`. |

`--json` emits the same report structurally.

This exists because on 2026-08-08 a fleet host ran a release two minors
behind the operator host; the older code resolved transports differently and
produced an error naming the wrong cause. See
[Strategy: make divergence loud](../STRATEGY-MAKE-DIVERGENCE-LOUD.md) and the
[fleet operator persona](../PRODUCT-DISCOVERY-PERSONAS.md).

### Exit codes: unreachable is not skew

`fleet version` exits `1` when any reachable host reports a **different**
version, or any host is reachable but missing the CLI (`not-installed`). It
exits `0` when every discrepancy is `unreachable` or `timeout` — a sleeping
laptop is an availability gap, not proof of divergent code (the strategy
doc's availability-class reasoning: detect divergence, don't conflate it with
downtime). With no remote hosts declared at all, it prints the local version
and exits `0`.

## Drift

```bash
anvil-serving fleet drift --repo /path/to/anvil-serving-notes
```

`--repo` is required — the private operator repository root (ADR-0032). The
tool never guesses it. Without `--host`, hosts are every directory under
`PATH/hosts/` that contains an `operator-home/`; pass `--host NAME`
(repeatable) to compare only named hosts, each of which must exist as a repo
snapshot or the command exits `2`.

For each host, `fleet drift` compares `PATH/hosts/<host>/operator-home/`
against that host's **live** operator home, file by file, content-based
(sha256):

| Status | Meaning |
| --- | --- |
| `identical` | Live bytes match the repo snapshot (newline-normalized). |
| `differs` | Live bytes differ from the repo snapshot. |
| `missing-live` | The repo-tracked file does not exist in the live home. |

The LOCAL host (hostname prefix-match, same rule `fleet version` uses) is
read directly: `--home` if given, else `$ANVIL_SERVING_HOME`, else the
platform default config home (`paths.config_home()`). Every other declared
host is REMOTE and is probed with one `ssh -n -o BatchMode=yes <host>
python3 -c "..."` call per host (per-host timeout `--timeout`, default 10s):
a small embedded script hashes exactly the repo-tracked filenames it is
given and prints `{name: sha256|null}` as JSON — one round trip per host,
not one per file. An unreachable or timed-out host reports that state, not
drift, and does not fail the gate (same availability-class reasoning as
`fleet version`: a sleeping host is not evidence of divergent config).

Comparison is newline-normalized (`\r\n` treated the same as `\n`) because a
Windows checkout of the repo snapshot must not read as "differs" against a
POSIX live home, or vice versa, when the underlying content is identical.

**Only repo-tracked files are ever compared, listed, or read.** A live home
routinely holds files the repo snapshot never will — `.env`, backups, lock
files — and those are expected, not findings; `fleet drift` never lists the
live directory and never opens a file whose name isn't already in the repo
snapshot, so a credential file such as `.env` is never read, locally or
over SSH, under any circumstance.

`--json` emits the same report structurally: per host, per-file `path` /
`status`, plus `compared` / `differs` / `missing` counts.

### Exit codes

`fleet drift` exits `1` when any compared file is `differs` or
`missing-live` on a **reachable** host. It exits `0` when every host is
either fully identical or unreachable.

This exists because on 2026-08-08 a host's live operator home was found six
commits behind its repository snapshot while serving production, and a
second host's live home was a wholesale byte-copy of the wrong host's home
— both undetected until someone happened to SSH in and look. Feature 7 of
[Strategy: make divergence loud](../STRATEGY-MAKE-DIVERGENCE-LOUD.md), ADR-0034
§9 ("Operator home: git as record, materialized for deployment").

## Related references

- [Workload visibility](../WORKLOAD-VISIBILITY.md)
- [Router fleet-status](router.md#fleet-status)
- [Strategy: make divergence loud](../STRATEGY-MAKE-DIVERGENCE-LOUD.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
