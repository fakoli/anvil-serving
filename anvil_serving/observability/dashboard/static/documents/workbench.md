# Anvil Workbench portal

Anvil Workbench is the authenticated workspace inside the existing dashboard.
It uses the existing Serving controllers, Connect access boundary, and Anvil
State authority. It does not create a second identity service or a browser host
shell.

## Pi runner

Keep the private policy outside this repository. After installation, the short
operator workflow is:

```bash
anvil-serving workbench build --runner pi --confirm
anvil-serving workbench pi-storage --config /absolute/private/workbench-bootstrap.json --dry-run
sudo anvil-serving workbench pi-storage --config /absolute/private/workbench-bootstrap.json --confirm
anvil-serving workbench pi-egress --config /absolute/private/workbench-bootstrap.json --provider example-provider --confirm
anvil-serving dashboard serve
```

The storage command creates only the declared new loop image. It proves an
existing image instead of reformatting it, mounts the fixed-size ext4 pool with
`nodev,nosuid`, and installs the matching systemd mount unit after verifying the
mount. Pi runner checkouts and native agent/session directories live below that
pool. The server journal, Workbench database, and retained evidence stay outside
it. Run the dry-run before the root-confirmed command; use a full Workbench
configuration, not a Pi-only fragment.

`anvil-serving dashboard serve` uses the installed private policy and binds to
the configured loopback address, commonly `127.0.0.1`. It does not require a
separate public authentication service. A missing Workbench policy leaves the
legacy dashboard behavior available.

## Private policy shape

The following is the `workbench` section of the existing authenticated
Observatory policy. Keep the policy's identity, inventory, controller, logs and
Prometheus sections, add this section, and install the complete policy as
`workbench.json` in the selected Anvil Serving operator home. The setup commands
accept either the complete policy or this section in a separate bootstrap file.
Every path below is illustrative and every model identity is explicit.

```json
{
  "state_path": "/var/lib/anvil-workbench/server/workbench.sqlite",
  "connectors": [{
    "id": "local-research", "label": "Local research",
    "resource_id": "serve-local-research",
    "base_url": "https://127.0.0.1:9443/v1",
    "token_ref": "file:/absolute/private/example-provider.token",
    "models": ["example-model-v1"]
  }],
  "presets": [{"id": "precise", "label": "Precise", "temperature": 0, "max_tokens": 1024}],
  "projects": [{
    "id": "project-a", "label": "Project A", "resource_id": "project-a",
    "checkout": "/srv/project-a", "anvil_binary": "/opt/anvil/bin/anvil",
    "runner_root": "/var/lib/anvil-workbench/pool/tasks/project-a"
  }],
  "pi_storage": {
    "image_path": "/var/lib/anvil-workbench/pi-storage.ext4",
    "pool_path": "/var/lib/anvil-workbench/pool", "size_bytes": 8589934592
  },
  "pi": {
    "id": "pi", "state_root": "/var/lib/anvil-workbench/server/pi",
    "runner_storage_root": "/var/lib/anvil-workbench/pool/pi",
    "engine_binary": "/usr/bin/docker", "image": "example/pi@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "uid": 10001, "gid": 10001,
    "models": {"example-provider": ["example-model-v1"]},
    "thinking_levels": ["off", "low", "high"],
    "provider_secret_refs": {
      "example-provider": {"PI_GATEWAY_TOKEN": "file:/absolute/private/example-provider.token"}
    },
    "provider_egress": {"example-provider": ["https://api.example.invalid"]},
    "provider_endpoints": {
      "example-provider": {
        "base_url": "https://api.example.invalid/v1", "api": "openai-responses",
        "credential_env": "PI_GATEWAY_TOKEN", "context_window": 32768,
        "max_tokens": 4096, "reasoning": true
      }
    }
  }
}
```

The image digest must be exactly 64 lowercase hexadecimal digits. The provider endpoint must use HTTPS and match one declared egress origin. Its
credential selector must name an entry for that provider and that entry must be
a protected `file:` reference. The runner receives no provider secret: a
per-session reverse provider gateway holds the credential outside the agent.

The `state_path` and `pi.state_root` are server journals and must be disjoint
from the pool. `runner_storage_root` and every project `runner_root` must be
inside the pool. The pool validator rejects symlink escapes, nested mounts,
wrong loop backing files, wrong size or ownership, and missing `nodev,nosuid`.

## Workspace journeys

**Workbench** presents declared evaluations. Review the exact target, limits,
phases, retained evidence and comparison before asking the owner to run an
experiment. A result does not promote a model.

**Playground** sends a real request only to the selected configured connector,
model and preset. It keeps a user-scoped conversation, effective parameters,
stream state and cancellation; provider credentials do not enter browser state.

**Models** displays owner-managed recipes and revisions. Edit supported fields,
review the exact diff, and use the existing owner lifecycle. Stale revisions
must be reviewed again. Loading also requires an exact manifest serve whose canonical
recipe command, model, container, port and GPU UUIDs match the selected recipe.
The manifest reserves each selected card in full; occupied or unknown capacity
blocks loading before a start. Unconfigured standalone recipes remain readable
and editable, with explicit guidance to declare their resource owner.

**Anvil work** reads configured projects through the Anvil CLI. Open the PRD,
task and frozen packet, then start only a ready task with an exclusive Anvil
lease. Pi is a thread-folder chat inside that task: it uses the official
`@earendil-works/pi-coding-agent` RPC agent for new, resume, branch, prompt,
steer, stop, tool events and supported extension replies. It does not embed Pi
Web's separate session or account authority.

Workbench claims an Anvil worktree, creates isolated full runner and verifier
clones, and binds them to the frozen packet and lease. The Pi runner uses the
pinned unprivileged image with CPU, memory, process, read-only-root and network
policy limits. Browser recovery reads retained cursors and never resends a
command. A stopped or lost lease prevents further task work.

Review captures the baseline-relative patch, including committed and untracked
changes, in a separate networkless sandbox. It validates paths, modes, declared
scope, the exact patch digest and frozen verification commands. Verification
output is retained with exit codes and timing. Only a passing reviewed patch can
transfer to a pristine claimed worktree. Workbench submits actual evidence via
Anvil State, but independent acceptance remains a human/State gate.

**Observability** shows source-labelled fleet, model, GPU, host, benchmark,
container-log and monitoring measurements. Missing values stay unknown.
**Compute** shows declared workload identities, bounded logs and owner controls;
managed container execution requires a reviewed catalog command and cannot open
a host shell. **Settings** saves bounded preferences and exposes configured
connections, storage ownership and existing Connect administration.

## Scope and verification

The [implementation PRD](design/workbench/IMPLEMENTATION.md) identifies the
user story behind each workspace. The implementation validation record maps each
visible journey to its backend and focused tests. It records test evidence, not
a release approval, merge, or live deployment claim.
