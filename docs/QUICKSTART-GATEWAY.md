# Quickstart: a gateway in front of the server you already run

You already run a model server such as Ollama, LM Studio, llama.cpp
`llama-server`, or vLLM. This page puts the Anvil Serving gateway in front of
it, so that OpenAI and Anthropic clients both reach your model through one
token-protected endpoint on `127.0.0.1:8000`, under a stable name
(`llm.primary`) that does not change when you swap models.

You need Python 3.11+ on Linux, macOS, or Windows. You do not need Docker, a
GPU driver, or a private operator repository. See the
[support matrix](SUPPORT-MATRIX.md) for platform details.

## 1. Install

Install the package from PyPI (version 3.0.0 or later):

```bash
pip install anvil-serving
```

Use a virtual environment if your system Python refuses global installs.

## 2. Write the gateway config

Pick the line for your server. `--upstream` is the server's OpenAI-compatible
base URL, ending in `/v1`. `--model` is the exact model name your server
reports; list it with `curl -s UPSTREAM/models`.

| Server | Typical upstream | Where the model name comes from |
| --- | --- | --- |
| Ollama | `http://127.0.0.1:11434/v1` | `ollama list`, for example `llama3.2` |
| LM Studio | `http://127.0.0.1:1234/v1` | the `id` in `curl -s http://127.0.0.1:1234/v1/models` |
| llama.cpp `llama-server` | `http://127.0.0.1:8080/v1` | the `--alias` you started it with, or the `id` in its `/v1/models` |
| vLLM | `http://127.0.0.1:8001/v1` | the model you passed to `vllm serve`, or `--served-model-name` |

vLLM listens on port 8000 by default, which is also the gateway's port. Start
vLLM with `--port 8001`, or run the gateway on another port with
`router run --port 8100`.

```bash
anvil-serving init --gateway-only --upstream http://127.0.0.1:11434/v1 --model llama3.2
```

This writes one file, `router.toml`, in the config home (`~/.anvil-serving`,
or `$ANVIL_SERVING_HOME` if set). It maps the alias `llm.primary` to your
server and turns on token authentication. A random `ANVIL_ROUTER_TOKEN` is
written to `.env` in the same directory with mode 0600. The token is never
printed. Rerunning `init --gateway-only` keeps an existing token.

Optional flags: `--alias NAME` to use a different caller-facing name,
`--context-limit N` to advertise the model's real context window (default
8192). If your server needs an API key (for example `vllm serve --api-key`),
export it as `ANVIL_UPSTREAM_API_KEY` in the shell that runs the gateway.

## 3. Run the gateway

```bash
anvil-serving router run
```

It runs in the foreground on `http://127.0.0.1:8000`. Leave it running and
open a second terminal.

## 4. Call it

Load the token into your shell without printing it:

```bash
export ANVIL_ROUTER_TOKEN="$(sed -n 's/^ANVIL_ROUTER_TOKEN=//p' "${ANVIL_SERVING_HOME:-$HOME/.anvil-serving}/.env")"
```

List the alias, then send a chat request:

```bash
curl -s http://127.0.0.1:8000/v1/models -H "Authorization: Bearer $ANVIL_ROUTER_TOKEN"
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $ANVIL_ROUTER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"model":"llm.primary","messages":[{"role":"user","content":"hello"}]}'
```

The same model also answers the Anthropic Messages API:

```bash
curl -s http://127.0.0.1:8000/v1/messages \
  -H "x-api-key: $ANVIL_ROUTER_TOKEN" \
  -H 'anthropic-version: 2023-06-01' \
  -H 'content-type: application/json' \
  -d '{"model":"llm.primary","max_tokens":64,"messages":[{"role":"user","content":"hello"}]}'
```

The commands above assume bash (Linux, macOS, WSL, or Git Bash). In Windows
PowerShell, load the token and call the gateway like this; `curl.exe` avoids
PowerShell's `curl` alias, and `Invoke-RestMethod` avoids native-command JSON
quoting:

```powershell
$envFile = if ($env:ANVIL_SERVING_HOME) { Join-Path $env:ANVIL_SERVING_HOME ".env" } else { Join-Path $HOME ".anvil-serving\.env" }
$env:ANVIL_ROUTER_TOKEN = (Select-String -Path $envFile -Pattern '^ANVIL_ROUTER_TOKEN=(.*)$').Matches[0].Groups[1].Value
curl.exe -s http://127.0.0.1:8000/v1/models -H "Authorization: Bearer $env:ANVIL_ROUTER_TOKEN"
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/chat/completions -Headers @{ Authorization = "Bearer $env:ANVIL_ROUTER_TOKEN" } -ContentType "application/json" -Body '{"model":"llm.primary","messages":[{"role":"user","content":"hello"}]}'
```

Callers always send `llm.primary`. The gateway forwards your server's own
model name upstream, so you can point the alias at a different model later
without changing any client.

## Client examples

Both examples read `ANVIL_ROUTER_TOKEN` from the environment you exported above.

OpenAI Python SDK (`pip install openai`):

```python
import os
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1",
                api_key=os.environ["ANVIL_ROUTER_TOKEN"])
reply = client.chat.completions.create(
    model="llm.primary",
    messages=[{"role": "user", "content": "hello"}],
)
print(reply.choices[0].message.content)
```

Anthropic Python SDK (`pip install anthropic`):

```python
import os
from anthropic import Anthropic

client = Anthropic(base_url="http://127.0.0.1:8000",
                   api_key=os.environ["ANVIL_ROUTER_TOKEN"])
reply = client.messages.create(
    model="llm.primary",
    max_tokens=256,
    messages=[{"role": "user", "content": "hello"}],
)
print(reply.content[0].text)
```

Tools that take a base URL and key work the same way: an OpenAI base URL of
`http://127.0.0.1:8000/v1`, an Anthropic base URL of `http://127.0.0.1:8000`,
the token as the API key, and `llm.primary` as the model.

## Troubleshooting

| Response | Cause | Fix |
| --- | --- | --- |
| `401 authentication_error` "invalid or missing API key" | The request has no token, or a different one. | Re-run the `export ANVIL_ROUTER_TOKEN=...` line in this terminal. Send it as `Authorization: Bearer ...` (OpenAI) or `x-api-key: ...` (Anthropic). |
| `404 model_not_found` "unknown model" | The request used a model name that is not a configured alias, such as the upstream name `llama3.2`. | Send `llm.primary` (or the `--alias` you chose). `GET /v1/models` lists the aliases. Unknown names are never forwarded. |
| `403 forbidden_host` "Host header is not allowed without front-door authentication" | Only happens when `[server] auth_env` was removed from `router.toml`. Without a token, the gateway accepts only a loopback IP in the `Host` header, so `http://localhost:8000` is refused. | Use `http://127.0.0.1:8000`, and keep authentication on. |
| `500 internal_error`, and the gateway log shows `RelayBackendError` | The gateway cannot reach your server. | Check that your server is running: `curl -s http://127.0.0.1:11434/v1/models` (use your upstream URL). Fix `--upstream` and rerun `init --gateway-only`. |
| `init` says to use `127.0.0.1` instead of `localhost` | `--upstream` named `localhost`. | Use the IP, for example `http://127.0.0.1:11434/v1`. |
| `router run` exits with `OSError: ... Address already in use` | Something else (often vLLM) is on port 8000. | Move that server, or run `anvil-serving router run --port 8100` and use that port in your clients. |

More diagnostics: [Troubleshooting](TROUBLESHOOTING.md) and
[Diagnose a request](ROUTER-DIAGNOSTICS.md).

## Next steps

- [Capability meta-router](META-ROUTER.md): what an alias is and what the
  gateway will never do (no fallback, no model substitution).
- [Configuration](CONFIGURATION.md): more aliases, more tiers, purpose models.
- [Getting started](GETTING-STARTED.md): let Anvil Serving manage the model
  server itself on a Linux NVIDIA host.
