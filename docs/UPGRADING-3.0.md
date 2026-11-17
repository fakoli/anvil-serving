# Upgrading to 3.0

3.0.0 makes unauthenticated listeners safe by default. Most of the breaking
changes only affect you if you run without a token, bind off loopback, or reach
a service as `localhost`. Authenticated routers are unchanged. The full list is
in the [3.0.0 changelog](https://github.com/fakoli/anvil-serving/blob/main/CHANGELOG.md#300---2026-11-16).

## Am I affected?

| You run | What happens on 3.0.0 | Fix |
| --- | --- | --- |
| `router run` on a non-loopback `--host` with no `[server].auth_env` | Exits with status 2 | Set `[server] auth_env = "ANVIL_ROUTER_TOKEN"`, or bind `127.0.0.1` |
| A router with no `auth_env`, called as `http://localhost:...` | 403 `forbidden_host` | Call `127.0.0.1` or `[::1]`, or configure a token |
| A fresh `anvil-serving init` | The generated router requires a token | Send the token that `init` wrote to `$ANVIL_SERVING_HOME/.env` (`ANVIL_ROUTER_TOKEN`) |
| The router container | It binds `0.0.0.0`, so it needs `auth_env` | The packaged `example-docker.toml` already sets it |
| Anvil Voice Realtime from a browser page | The WebSocket upgrade gets 403 | List the page's origin in `voice.realtime_allowed_origins` |
| `dashboard serve` with no `--auth-env`, or the model-swap multiplexer, reached as `localhost` | 403 | Use `127.0.0.1`, or configure a token |
| The controller with `--allow-unauthenticated-loopback` | A foreign `Host` or a non-loopback browser `Origin` gets 403 on every POST except `/mcp` | Use loopback origins, or run with a token |
| A readiness probe on Voice `/usage` behind `voice.realtime_token_env` | The probe now needs the bearer token | Point `ready_url` at `GET /healthz` |
| Legacy `fakoli-*` volume or compose project names, or `owner="mini"` | Still work for one release, with a `LegacyNameWarning` | See [Migrating to generic names](MIGRATION-GENERIC-NAMES.md) |

Native Voice clients that send no `Origin` header, including the OpenClaw
Gateway relay, are unaffected.

## Migration steps

1. Upgrade: `pip install -U "anvil-serving>=3.0.0"`.
2. Replace `localhost` with `127.0.0.1` in client and probe configuration.
3. If the router binds off loopback, add `[server] auth_env = "ANVIL_ROUTER_TOKEN"`,
   put the token in `$ANVIL_SERVING_HOME/.env` (mode 0600), and have clients
   send it as a bearer token.
4. For browser apps that use Voice Realtime, add each exact origin
   (`scheme://host[:port]`, no path) to `voice.realtime_allowed_origins`.
5. Move readiness probes to `GET /healthz`.
6. Run `anvil-serving doctor` and `anvil-serving topology validate`.
7. Plan the [generic-name migration](MIGRATION-GENERIC-NAMES.md) before the
   legacy names are removed.

## Keeping the old behavior

- **Open router on a public interface:** pass
  `--allow-unauthenticated-public-bind` to `router run`. The warning is still
  printed. This leaves an unauthenticated front door open; prefer a token.
- **Tokenless loopback router:** leave `auth_env` unset and address the router
  as `127.0.0.1`. There is no option to accept `Host: localhost` without a
  token.
- **Browser access to Voice Realtime:** `voice.realtime_allowed_origins` takes
  exact origins only; there is no wildcard.
- **Legacy names:** keep the old volume name, or `COMPOSE_PROJECT_NAME=fakoli-experiment`,
  for one more release.

See the [Deployment security model](SECURITY-MODEL.md) for what each listener
protects and what an unauthenticated loopback deployment does and does not
defend against.
