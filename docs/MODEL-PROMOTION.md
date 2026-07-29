# Promote and roll back a model

Promotion is the one operation that changes what callers actually get. Everything else in
anvil-serving — readiness, preflight, benchmarks — produces evidence; promotion acts on it.
It is therefore a guarded transaction with an explicit human gate, not a config edit.

**A benchmark never promotes a model.** A recorded result can recommend a change; a person
authorizes it. This separation is the reason the evidence layer is trustworthy: nothing in the
measurement path can quietly alter what is serving.

## The transaction

`serves promote` takes the name of a `[[promotion]]` plan declared in your manifest — not a role
or a model name. It stages the candidate recipe, qualifies it, swaps the role, and keeps a
rollback path throughout. Preview first, then repeat with explicit confirmation:

```bash
anvil-serving serves promote <promotion-plan> --dry-run
```

```bash
anvil-serving serves promote <promotion-plan> --confirm
```

The dry run is not a formality — it is where you confirm the resolved recipe, target role, and
rollback target are the ones you intend, before anything mutates.

If the candidate fails its gate, the transaction restores the previous state rather than
leaving a half-promoted role. Roll back deliberately with the same command surface:

```bash
anvil-serving serves promote <promotion-plan> --rollback --confirm
```

## Draining before a swap

A role swap must not sever in-flight work. The router exposes the three transition states
separately so each one is observable rather than implied:

```bash
anvil-serving router quiesce --tier <tier> --confirm
anvil-serving router drain --tier <tier> --timeout 120
anvil-serving router readmit --tier <tier> --confirm
anvil-serving router transition-status
```

`--tier` is a required option on all three, and `drain` additionally requires `--timeout` in
seconds so a wait can never hang unbounded. `quiesce` and `readmit` mutate and therefore take
`--confirm`; `drain` only waits and reports. Keeping them distinct means
"stopped accepting work", "finished outstanding work", and "back in service" can never be
confused for one another — the failure mode ADR-0018 exists to prevent.

`serves promote` performs this sequence as part of its transaction. Run the commands directly
when you are staging something by hand or recovering from an interrupted transition.

## Switching a role by recipe

`serves switch` points a deployment role at an activation-ready recipe:

```bash
anvil-serving serves switch <role> <model> --registry <registry.toml> --dry-run
anvil-serving serves switch <role> <model> --registry <registry.toml> --confirm
```

`<model>` is the recipe's model id or an unambiguous basename, for example
`Laguna-S-2.1-NVFP4`. (`--recipe MODEL` is accepted as a compatibility spelling of the same
selector.) A recipe existing in a registry is deliberately **not** sufficient to alter a live
routing tier. The switch requires a reviewed activation mapping, so an experiment recorded during a
bakeoff cannot become production by being written down.

## Installing a router configuration

`router install-config` validates a configuration, writes it atomically, restarts the router,
and succeeds only when the exact desired tier set is present:

```bash
anvil-serving router install-config --config <router.toml> --dry-run
anvil-serving router install-config --config <router.toml> --confirm
```

Tiers that are configured but currently unavailable are reported rather than treated as a
failed install. Installing a configuration is a *configuration* operation: it never promotes a
model and never implies the tier is qualified to serve.

## Order of operations

1. Stage the candidate serve and confirm it is healthy.
2. Run `eval preflight` against the exact endpoint and served-model name.
3. Record benchmark evidence, and publish a dated finding if the outcome changes a
   recommendation or reference deployment.
4. Review the evidence and decide. This step is a person, not a command.
5. `serves promote --dry-run`, then `--confirm`.
6. Verify the promoted role serves, and keep the rollback target pinned and reachable.

Steps 2 and 3 are what turn a reachable endpoint into a qualified capability. Skipping them
does not make promotion faster; it makes the resulting claim unsupported.

## Keep a real rollback

A rollback target is only real if it can actually be started right now — the checkpoint present,
the engine image pinned, the recipe recorded. A "rollback" that needs a fresh download or an
unpinned image is a plan, not a rollback.

Stop and get direction rather than proceeding when the target role, tier, topology owner, or
promotion authority is ambiguous.

## Related

- [Operator playbooks](OPERATOR-PLAYBOOKS.md) — the surrounding serve → preflight → publish loop.
- [Serve lifecycle](cli/serves.md) — full `serves` command reference.
- [Router commands](cli/router.md) — transitions, config install, and status.
- [ADR-0018: Router transition safety](adr/0018-router-transition-safety.md) — why the states are distinct.
- [ADR-0016: Runtime tier readiness](adr/0016-runtime-tier-readiness.md) — readiness without config rewrites.
- [Benchmark portal](benchmarks/index.md) — current occupants and the evidence behind them.
