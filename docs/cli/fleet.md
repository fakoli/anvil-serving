# Fleet

[CLI overview](../CLI.md) · [Router commands](router.md)

The `fleet` family answers questions that span more than one declared host.
`fleet version` is the first verb: cross-host `anvil-serving` version skew.

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

## Related references

- [Router fleet-status](router.md#fleet-status)
- [Strategy: make divergence loud](../STRATEGY-MAKE-DIVERGENCE-LOUD.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
