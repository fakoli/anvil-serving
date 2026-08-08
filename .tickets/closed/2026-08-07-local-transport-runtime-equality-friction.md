# Local transport requires command runtime == resource runtime, breaking same-host docker operations

**Status:** Resolved 2026-08-07 (ADR-0033 §3)

## Resolution

`_select_transport` now classifies a plan as local when the hosts match and
either the runtimes match or the command identity is `native` and the
execution runtime role is one a native shell genuinely operates (today:
`docker` — `_NATIVE_OPERATES_RUNTIME_ROLES` in `anvil_serving/targets.py`).
The asymmetry is deliberate: a docker-runtime identity does not operate native
execution. The loopback-controller alternative was considered and rejected as
the primary fix (it adds a running process per host and still fails when the
controller is down); `controller serve --allow-unauthenticated-loopback` now
exists as the documented development opt-in.

The related `resource_owner()` friction is also fixed: roles are unique per
host, and with multiple global owners and no `--target`, resolution prefers
the command identity's own host (never guessing among remote hosts; the error
now enumerates owning hosts). Regression coverage:
`tests/test_targets.py::test_voice_host_shape_resolves_local_without_command_runtime_override`
reproduces this ticket's exact shape.

---

Original report follows.

## Problem

`anvil_serving/targets.py::_select_transport` classifies a plan as local only
when the command identity matches the execution target on **both** host and
runtime:

```python
local = identity.host.id == execution_host.id and identity.runtime.id == execution_runtime.id
```

The CLI process always runs on the host's `native` runtime (and `init` writes
`command_runtime = "runtime:<host>-native"`), while containerized serves
declare `runtime = "<host>-docker"`. So every docker-runtime resource on the
operator's *own* host resolves non-local, `auto` selects `controller`, and —
because no scaffold declares a controller transport for the host's own docker
runtime — resolution fails with the unhelpful:

```text
controller transport for operation 'voice-status' on '<host>'/'<host>-docker' has 0 declared owners
```

## Reproduction (2026-08-07, fakoli-mid-mod voice bring-up)

Every `voice audio {up|down|status|logs}` and `voice up|down` invocation on the
host that owns the serves needs an explicit override:

```bash
anvil-serving voice audio status --command-runtime runtime:mid-mod-docker
```

which asserts the operator shell *is* the docker runtime — declaratively false,
but the only way to reach `local`.

## Impact

- The common case — operating your own host's containers from its own shell —
  requires a per-invocation workaround that misdeclares the command identity.
- Related friction: `Topology.resource_owner()` requires a globally unique
  resource role, so a fleet where two hosts declare `realtime-proxy` forces
  `--target` on every invocation even when the topology's command host owns
  one of them.

## Expected / fix direction

Either a "same host, compatible runtime role" locality rule (host equality +
`execution_runtime_roles` compatibility), or scaffolds that declare a loopback
controller transport for the host's own docker runtime
(`allow_unauthenticated_loopback` exists for exactly this). Belongs to the
planned controller/node multi-host maturation (transport decision: controller
RPC, not SSH).
