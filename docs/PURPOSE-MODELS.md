# Embeddings and reranking

Embeddings and reranking are **purpose models**: dedicated local serves reached through their
own gateway endpoints, routed by exact model name rather than by a chat alias. They never enter
the chat pipeline, and they never fall back to another model.

| Endpoint | Purpose kind | Request shape |
| --- | --- | --- |
| `POST /v1/embeddings` | `embedding` | OpenAI Embeddings, relayed to the named serve |
| `POST /v1/rerank` | `rerank` | Jina/Cohere-style rerank, relayed likewise |

Both routes require the same bearer token as the chat routes. When no purpose models are
configured, both paths return 404 — the surface simply does not exist rather than degrading to
chat routing.

## Configure a purpose model

Each entry is one `[[router.purpose_models]]` table:

```toml
[[router.purpose_models]]
id = "embeddings-local"
kind = "embedding"
model = "qwen3-embedding-0.6b"
base_url = "http://127.0.0.1:30005/v1"

[[router.purpose_models]]
id = "reranker-local"
kind = "rerank"
model = "qwen3-reranker-0.6b"
base_url = "http://127.0.0.1:30006/v1"
```

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Operator-facing route name. Lets you identify a route without disclosing a host to callers. |
| `kind` | yes | `embedding` or `rerank`. Selects which endpoint serves this entry. |
| `model` | yes | The serve's `--served-model-name`. **This is the routing key** — the exact string callers put in the request `model` field. |
| `base_url` | yes | OpenAI-style base of the local serve. |
| `auth_env` | no | Name of an environment variable holding the upstream secret, never the secret itself. Local pooling serves usually need none. |
| `timeout` | no | Per-model transport timeout override. |

`model` is the routing key, not `id`. Two entries of the same `kind` are distinguished by the
model name the caller sends, so each `model` value must be unique.

## Route a request

Callers name the served model directly. There is no alias indirection on this surface:

```bash
curl -sS http://127.0.0.1:8000/v1/embeddings \
  -H "Authorization: Bearer $ANVIL_ROUTER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding-0.6b","input":["first passage","second passage"]}'
```

```bash
curl -sS http://127.0.0.1:8000/v1/rerank \
  -H "Authorization: Bearer $ANVIL_ROUTER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-reranker-0.6b","query":"which passage answers the question","documents":["first passage","second passage"]}'
```

A model name with no matching configured entry is an error on that endpoint. It is never
retried against a chat tier and never substituted with another purpose model.

## Why this is a separate surface

Chat aliases in `[router.model_routes]` exist so a caller can ask for a *capability* without
naming a checkpoint. Embeddings and reranking are the opposite case: the exact model **is** the
contract, because vectors from two different embedding models are not interchangeable and a
silent substitution would corrupt an index rather than degrade a response. Routing by model
name keeps that substitution impossible by construction.

This is also why there is no fallback chain here. A rerank request that cannot reach its serve
fails loudly instead of returning a differently-ordered result the caller cannot detect.

## Operate the serves

Purpose models are ordinary local serves — they are declared in a serves manifest and follow
the normal lifecycle. They are separate from any chat tier and hold their own GPU reservation:

```bash
anvil-serving serves status --manifest <serves.toml>
anvil-serving serves up <embedding-serve> --manifest <serves.toml> --dry-run
anvil-serving serves up <embedding-serve> --manifest <serves.toml> --confirm
```

Because these serves take VRAM alongside the chat tiers, plan them into the residency budget
rather than starting them ad hoc. See [Device topologies](DEVICE-TOPOLOGIES.md) for how
reservations are declared and [Operator playbooks](OPERATOR-PLAYBOOKS.md) for the confirm-first
lifecycle pattern.

## Qualification boundary

A configured route only proves the router will relay to that endpoint. It does not establish
that the model is fit for your corpus. Retrieval quality depends on the embedding model, the
chunking, and the domain, none of which readiness can observe.

Qualify a purpose model the same way as any other capability — with a recorded result, not a
config change. See [Evaluation & benchmark commands](cli/eval.md) and
[Methodology & evidence](benchmarks/methodology.md).

## Related

- [Configuration reference](CONFIGURATION.md) — full router configuration contract.
- [Thin capability gateway](THIN-CAPABILITY-GATEWAY.md) — endpoint boundary and error behavior.
- [Router commands](cli/router.md) — running and reloading the router.
- [Terminology](TERMINOLOGY.md) — tier, alias, serve, and evidence vocabulary.
