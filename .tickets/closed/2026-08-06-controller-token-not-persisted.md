# Controller token exists only in the container environment, so CLI probes fail closed

**Status:** Resolved 2026-08-07 (ADR-0033 §1)

## Resolution

Token resolution now shares the CLI's file-backed dotenv chain (extracted to
`anvil_serving/envfile.py`): shell environment first, then
`$ANVIL_SERVING_HOME/.env`, then `~/.env`. Wired into the controller server
bind check and `resolve_auth_token`, `controller status`, the router's
`[server].auth_env` resolution, and `ControllerTransport` dispatch. Refusals
now enumerate every location checked. Injected environments stay hermetic
(explicit `env=` never falls back to files). The operator step that remains:
add the token line to the gitignored `%ANVIL_SERVING_HOME%\.env` on each host
(values never enter Git; see docs/CONFIGURATION.md "Token persistence
contract").

---

Original report follows.

## Problem

`anvil-serving controller status` refuses to probe when
`ANVIL_CONTROLLER_TOKEN` is unset in the calling shell:

```text
controller status: token environment variable ANVIL_CONTROLLER_TOKEN is unset or empty
```

On Fakoli Dark the token is present inside the running
`anvil-serving-controller` container (injected at creation time from a shell
environment that no longer exists), but it is not persisted in
`%ANVIL_SERVING_HOME%\.env` or any file-backed reference the CLI reads. After a
host restart, a fresh operator session cannot run the authenticated controller
probe without manually re-deriving the token, and any managed flow that relies
on `controller status` (for example `--transport controller` dispatch) fails
closed on an otherwise healthy controller.

This is the same fragility class previously recorded for the router token
("token + publish only in shell env"), now observed on the controller path
during the 2026-08-06 overnight stack restart: the container's Docker
healthcheck proved liveness, but the typed CLI probe was unusable.

## Required behavior

1. The controller token should be resolvable from a durable, gitignored,
   file-backed reference in the operator home (consistent with the
   secrets-as-references rule), not only from the launching shell.
2. `controller status` should say where it looked (env, env-file) when it
   refuses, so the operator can fix the reference instead of guessing.
3. Documentation for controller bring-up should state where the token persists
   and how a fresh session re-acquires probe access after reboot.

## Acceptance

- CLI resolves `ANVIL_CONTROLLER_TOKEN` from the operator-home `.env` (or an
  equivalent documented reference) when the shell env is empty.
- A hermetic test covers the env-file fallback and the improved refusal
  message.
- Operator docs (`docs/CONFIGURATION.md` or controller docs) describe the
  persistence contract.

## Temporary operator guard

Trust the container healthcheck (`docker ps` shows `healthy`) for liveness
until the token reference is persisted; do not paste the token into shell
profiles or commit it anywhere.
