# Security Policy

## Supported versions

Security fixes target the **latest major release line** (currently `1.x`, matching
the source tree version in [CHANGELOG.md](CHANGELOG.md)); older major and pre-1.0
minor lines are not supported.

| Version              | Supported          |
| -------------------- | ------------------ |
| Latest 1.x release   | :white_check_mark: |
| Pre-1.0 releases     | :x:                |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub's **private security advisories** on the repository:
**Security → Advisories → Report a vulnerability**
(<https://github.com/fakoli/anvil-serving/security/advisories/new>). If that is not available to
you, contact the maintainer at **sdoumbouya81@gmail.com**.

Please include a description, reproduction steps, affected version, and impact. We aim to
acknowledge within a few business days and will coordinate a fix and disclosure timeline with you.

## Scope and threat model

anvil-serving is a **network-facing server** that proxies coding-harness traffic
to configured local model endpoints. Keep this in mind:

- Every tracked file is a public surface. Real topology, active deployments,
  personal paths, and unsanitized evidence belong in a private operator
  repository; credential values belong outside Git in both repositories. See
  [Public product and private operator state](docs/OPERATOR-PRIVACY.md).

- The server binds `127.0.0.1` by default. **Built-in authentication is opt-in, not automatic**:
  configure `[server].auth_env = "ANVIL_ROUTER_TOKEN"` (any env-var NAME matching
  `^[A-Z][A-Z0-9_]*$`; rejected if it looks like a secret literal rather than a NAME) in your
  router config, and the front door then requires an `Authorization: Bearer <token>` **or**
  `x-api-key: <token>` header on every route except `GET /healthz`, checked against
  `os.environ[auth_env]` with a constant-time compare (`hmac.compare_digest`). With no
  `auth_env` configured, auth is **off** — this preserves the original loopback-only default
  exactly, so upgrading does not silently lock anyone out. See
  [ADR-0004](docs/adr/0004-router-as-a-service-containerized-and-authed.md).
- **A token is required before you bind the router to a non-loopback address.** If you bind it
  to a non-loopback address (`--host 0.0.0.0`, or a LAN/tailnet IP) without configuring
  `auth_env`, **any** caller reachable on that network can drive configured model endpoints.
  Configure `auth_env` first, always, whenever the router is reachable from anywhere other than
  the box it runs on. Treat network-level identity — a Tailscale ACL, a firewall rule, a private
  mesh — as **defense-in-depth on top of the token**, never as a substitute for it.
- **The token secret itself is never stored in a config file** — only its env-var NAME is (via
  `auth_env`). Request metadata and decision records must never contain credential values.
- **In the Docker/Compose deployment** (see the README's "Run the router in Docker" section),
  the router is the **only** service published beyond loopback; the local model serves
  (SGLang/vLLM) stay on the internal Docker network / loopback, reachable **only** by the
  router (by service name) — never publish a raw serve directly.
- **Model workloads deny network egress by default.** Effective Compose services
  must attach only to `internal: true` networks; recipe loads use an Anvil-owned
  internal bridge. Model recipes cannot opt out. Only explicitly non-inference
  capability, media, or voice gateways may declare egress, with an audited role
  and durable reason.
  This blocks ordinary outbound connections without trusting engine-specific
  telemetry opt-outs, but it is not a malware sandbox and does not retrofit
  already-running containers. See
  [ADR-0043](docs/adr/0043-model-workloads-deny-network-egress-by-default.md).
- Upstream credentials are referenced by **env-var name**. Do not paste raw keys into
  config files, logs, or decision records.
- The test suite is hermetic and never makes real network or LLM calls.

Out of scope: vulnerabilities in third-party inference engines (SGLang, vLLM), in the cloud
providers themselves, or in harnesses pointed at the router.
