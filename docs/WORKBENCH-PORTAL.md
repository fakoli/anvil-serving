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

## Pi Web

Pi Web is the separately owned session UI for the operator's existing local Pi
sessions: it reads the same agent directory, session files, and model settings
as the terminal Pi, and adds browser-side conversation control. It is the
reuse candidate studied in [the Pi workspace reuse decision](design/workbench/PI-REUSE.md);
this managed service installs the pinned third-party release
(`@agegr/pi-web`) as one systemd unit instead of a hand-run process.

Pi Web must be able to read and write the agent data directory and the working
directories its sessions reference, so the service runs as one declared POSIX
account and binds only `127.0.0.1`. A reverse proxy is the supported way to
reach it from a browser; the exact external host names must be declared in the
configuration so Pi Web's own host allow-list accepts them. Keep the private
declaration in the operator home as `workbench/pi-web.json`:

```json
{
  "version": "0.9.0",
  "port": 30141,
  "hostname": "127.0.0.1",
  "allowed_hosts": ["pi.example.test"],
  "idle_timeout_ms": 600000,
  "service_user": "operator"
}
```

`service_user` is the account whose Pi sessions the UI serves. `password_env_file`
is an optional protected environment file (`PI_WEB_PASSWORD=...`, mode 0600)
for a second, direct-to-service factor; the managed Connect exposure below is
Authelia-gated and does not require it. The lifecycle:

```bash
anvil-serving workbench pi-web-install --dry-run
sudo anvil-serving workbench pi-web-install --confirm
anvil-serving workbench pi-web-status
anvil-serving workbench pi-web-logs --tail 100
sudo anvil-serving workbench pi-web-down --confirm
sudo anvil-serving workbench pi-web-up --confirm
```

The install command pins the exact declared release below the operator home,
installs the npm tree as the declared service user (the package postinstall
never runs as root), writes the reviewed `anvil-pi-web.service` unit, enables
and starts it, and then probes `127.0.0.1` until the UI answers. A repeated
install converges without an unnecessary restart when the unit is unchanged.

### Pi Web through Anvil Connect

The supported browser path is an Anvil Connect browser resource: one dedicated
resource host whose origin is the loopback Pi Web port. The public template is
[connect/examples/pi-web-resource.json](https://github.com/fakoli/anvil-serving/blob/main/connect/examples/pi-web-resource.json).
Merge its resource into the deployment manifest's connector, render, and apply:

```bash
anvil-serving connect validate --manifest /path/private/deployment.json
sudo anvil-serving connect render --manifest /path/private/deployment.json --confirm
sudo anvil-serving connect up --manifest /path/private/deployment.json --services gateway,caddy,connector-<id> --confirm
```

Connect's classic-upgrade and SSE forwarding carry Pi Web's event streams;
the declared limits allow long agent turns (one-hour request duration) and
file uploads (25 MB request ceiling). Browser access is Authelia-gated at the
Connect edge; Pi Web's own session, cookie, and CSRF state stay separate from
the Connect session. Do not bind Pi Web wider than loopback and do not disable
Connect admission for it.

To show that native UI in Playground, add the following `host_pi` fragment to
the Observatory policy's `workbench` section. It names the dedicated Connect resource, its HTTPS origin,
the exact owner subject, and the inspected runtime pin:

```json
{
  "host_pi": {
    "id": "host-pi", "resource_id": "host-pi",
    "origin": "https://pi.example.test", "owner_subject": "example-owner-subject",
    "version": "0.9.0",
    "runtime_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
}
```

Replace the illustrative digest with the inspected installed runtime digest.
The signed Connect subject must match the owner and have the explicit resource
grant. A wildcard grant alone is insufficient. Host Pi retains its native
history and filesystem authority; this declaration does not make it a confined
task runner. Its origin must differ from the dashboard origin.

### Native project navigation

For project/thread navigation and native run discovery, enable the packaged
bridge in the private `workbench/pi-web.json` declaration:

```json
{
  "version": "0.9.0",
  "service_user": "operator",
  "install_root": "/opt/anvil-pi-web",
  "allowed_hosts": ["pi.example.test"],
  "bridge_source": "/srv/sources/pi-web",
  "bridge_parent_origin": "https://workbench.example.test",
  "bridge_token_env_file": "/etc/anvil-serving/pi-web-bridge.env"
}
```

The source must be the clean pinned Pi Web commit
`0d1df12130c5069d86484051435a40d49e49db94`. Installation builds an archive of
that commit with the packaged patch. Source preparation runs in the installer;
npm installation and compilation run as the service account without runtime
credentials. It binds the resulting artifact to the exact parent origin.
Use the same `pi-web-install` preview/apply commands above. Changing the parent
origin requires rebuilding; repeating unchanged setup verifies the retained
artifact before reporting it current. The protected environment file supplies
`PI_WEB_WORKBENCH_BRIDGE_TOKEN` only to the native service.

Add `parent_origin`, `bridge_base_url` and `token_ref` together to `host_pi`:

```json
{
  "parent_origin": "https://workbench.example.test",
  "bridge_base_url": "http://127.0.0.1:30141",
  "token_ref": "file:/etc/anvil-serving/pi-web-bridge.token"
}
```

The token file contains the same bridge credential, separate from the native
service's environment-file format. Workbench reads it through its protected
secret-reference boundary; browser state contains neither credential nor bridge
endpoint. After shared token or configuration changes, reload both the managed native Pi
service and the Workbench/dashboard service to reconstruct their owner clients.

Playground keeps native Pi's composer, tools, extensions and history inside the
page. The surrounding project rail adds search, stable thread links and new
threads in declared directories. Existing native threads are owner-only until
associated with a matching directory; association cannot move a running thread.
Rename and archive apply to the Workbench view and retain native history.
Project file browsing and copied `@root:path` references do not change the
active native session's directory or grant task authority. On narrow screens,
**Project threads** collapses so the native composer remains reachable.

## Retained runs

Workbench discovers Operations from its journal. Optional benchmark and
retained-evidence owners are declared once in the top-level `runs` section of
the private Observatory policy, alongside `workbench`:

```json
{
  "runs": {
    "benchmark": {"resource_id": "benchmark-runs"},
    "evidence": {
      "resource_id": "retained-evidence", "owner_id": "evidence-catalog",
      "root": "/srv/anvil-serving/docs/findings"
    }
  }
}
```

Configured projects also expose the current user's retained task bindings and
managed Pi sessions under their existing project read grants. Pi metadata
reads are unavailable on platforms without the required safe filesystem
descriptor operations; canonical session storage continues to work.
The optional native bridge adds a separate **Native Pi sessions** source for
the exact Connect owner with explicit host and project grants. It projects
metadata from the native owner once, without copying transcripts. Its complete
inventory is bounded to 256 sessions and 128 KiB; larger or malformed inventories
report unavailable rather than silently dropping sessions. Pages retain one
60-second snapshot, and active rows already loaded from history refresh too.
Native and managed Pi rows open their exact conversation; task rows open their
evidence.

Each source requires its own read grant, distinct from controller resources and
other run sources, for the optional benchmark and evidence declarations above.
Benchmark enumeration uses the declared controller's
`benchmark_job_list`; the evidence source reads recognized JSON artifacts below
its declared local directory. New records need no per-run configuration entry.
Paths, raw payloads and transcripts are excluded from the run projection.

Visible sources refresh independently. An unavailable owner leaves its last
authorized rows marked stale; it does not change their retained outcome.
Imported evidence preserves failures, incomplete measurements and external
priors. Source cards report partial or truncated coverage explicitly. The
local catalog requires descriptor-safe filesystem reads and refuses symlinks,
special files and protected credential paths.

Use **All runs** to filter by source or outcome and load older pages. The view
keeps a current page and bounded retained history, with a maximum of 500 rows
per source. Expired cursors keep the last successful rows and offer **Restart
history**. A retained row's freshness is separate from its recorded outcome.

Use **Compare** to select two through twenty retained evidence artifacts. The
comparison reports compatible, different, unknown and invalid measurement
dimensions. A changed artifact requires fresh references; the previous result
is cleared. Compatibility does not rank models or authorize promotion.

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

Local project file reads and isolated task preparation require safe POSIX
descriptor operations for each declared project root. Unsupported hosts refuse
these actions.

The image digest must be exactly 64 lowercase hexadecimal digits. The provider endpoint must use HTTPS and match one declared egress origin. Its
credential selector must name an entry for that provider and that entry must be
a protected `file:` reference. The runner receives no provider secret: a
per-session reverse provider gateway holds the credential outside the agent.

The `state_path` and `pi.state_root` are server journals and must be disjoint
from the pool. `runner_storage_root` and every project `runner_root` must be
inside the pool. The pool validator rejects symlink escapes, nested mounts,
wrong loop backing files, wrong size or ownership, and missing `nodev,nosuid`.

## Project roots and new-session defaults

Old projects without `roots` retain one `primary` root at their checkout.
Explicit projects declare up to 16 disjoint roots with stable IDs, labels,
`owner_id`, `runtime_id`, `task_access` and absolute private `path` values.
`primary_root_id` selects the starting directory. Managed execution currently
supports `local-owner` / `local-runtime` on descriptor-safe POSIX hosts.
Root IDs must remain distinct without regard to case.

**Settings → Project roots** saves defaults only for future conversations. The
new-run preview shows the selected primary and writable secondary roots; the
canonical State root always remains writable. Read-only roots become private
snapshots. A saved default that no longer matches configuration needs review
before a new start. Existing threads keep their frozen bindings.

Writable secondary roots additionally require `repository_id`, nonempty
`expected_files` (at most 256 paths or patterns), and frozen
`verification_commands` matching their enrollment in Anvil. Multi-root
submission requires Anvil **0.6.11 / API 17** or a compatible owner build,
including `roots request-digest`, `claim`, `reconcile`, `evidence-status`, and
`submit-evidence`. These APIs coordinate one claim across the root set.
Install that owner capability before enabling writable secondary roots.

Each root has its own baseline, reviewed patch, command results and transfer
state. An unchanged root still runs its verification commands with an empty
patch. Partial transfer recovery checks each claimed worktree before proceeding.
Submission reopens retained evidence, verifies its digests, and sends the exact
root manifest to Anvil. A lost response is reconciled against that manifest;
a failed reservation release can be retried without a second submission.
Independent State acceptance remains separate.

## Workspace journeys

**Workbench** presents active and retained owner runs. Review the exact target, limits,
phases, retained evidence and comparison before asking the owner to run an
experiment. A result does not promote a model.

**Playground** opens the authorized native host Pi workspace when configured.
The separate **Model test** view sends requests to the selected connector,
model and preset, retaining user-scoped conversations, effective parameters,
stream state and cancellation. Provider credentials do not enter browser state.

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
policy limits. Browser recovery retains the original request identity. A lost start or command
requires **Reconcile original start** or **Reconcile command**; an unknown
command after reload is never reconstructed from private prompt text in browser
storage. A stopped or lost lease prevents further task work.

Managed Pi exposes queued follow-up and attachments as unavailable because its
current owner accepts text prompts only. Host Pi retains the formats and queue
behavior supported by its pinned native UI. Provider changes require a new
managed conversation; model and thinking changes stay within its allowlist.

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
