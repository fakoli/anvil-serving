# Capability Gateway

The Capability Gateway gives every client one authenticated endpoint. Callers
ask for a capability alias such as `llm.primary`; your configuration maps each
alias to exactly one local tier. The gateway speaks Anthropic Messages, OpenAI
Chat Completions, and a stateless Responses subset, and streams responses
straight through.

It never guesses. An unknown alias returns 404, and a tier that cannot serve
returns an error instead of quietly switching to a different model.

<!-- product-journey:capability-gateway -->

## When to use it

- Several agents or apps should share local models through one front door.
- You want to swap the model behind `llm.primary` without editing every client.
- You need admission control, readiness, and an audit trail of routing decisions.

## Go deeper

- [Gateway overview](../META-ROUTER.md) — the authority model and invariants
- [Request path](../THIN-CAPABILITY-GATEWAY.md) — what happens to one request
- [Configuration](../CONFIGURATION.md) — aliases, tiers, and qualified replicas
- [Embeddings and reranking](../PURPOSE-MODELS.md)
- [Reliability controls](../ROUTER-RELIABILITY.md)
- [Diagnose a request](../ROUTER-DIAGNOSTICS.md)
- [Remote tailnet endpoints](../REMOTE-TAILNET-ENDPOINTS.md)
- Command reference: [router](../cli/router.md), [routed evaluation](../cli/eval.md#routed-client-evaluation)
