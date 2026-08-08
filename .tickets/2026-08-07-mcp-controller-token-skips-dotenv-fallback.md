# MCP controller client resolves its token from the environment only, skipping the dotenv fallback chain

**Status:** Open

## Problem

`anvil_serving/control_plane/mcp/controller_client.py:resolve_controller_token()`
looks up the controller token in `os.environ` only, while the
`transports.py` controller path (`ControllerTransport._token`) resolves
through `envfile.resolve_env_value()`, which includes the ADR-0033
`.env`-file fallback chain.

An operator whose token lives only in the durable `.env` file gets a working
controller transport but an unauthenticated (or failing) MCP controller
client, which is confusing to diagnose.

## Resolution options

Decide whether the asymmetry is intentional (MCP layer deliberately
env-only). If not, route `resolve_controller_token()` through
`envfile.resolve_env_value()` so both controller paths share one resolution
chain. Found during the 2026-08-07 production-cleanup DRY audit; not changed
then because it alters authentication behavior rather than removing
duplication.
