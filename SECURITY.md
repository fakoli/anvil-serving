# Security Policy

## Supported versions

anvil-serving is pre-1.0. Security fixes target the latest `0.4.x` release line.

| Version | Supported          |
| ------- | ------------------ |
| 0.4.x   | :white_check_mark: |
| < 0.4   | :x:                |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub's **private security advisories** on the repository:
**Security → Advisories → Report a vulnerability**
(<https://github.com/fakoli/anvil-serving/security/advisories/new>). If that is not available to
you, contact the maintainer at **sdoumbouya81@gmail.com**.

Please include a description, reproduction steps, affected version, and impact. We aim to
acknowledge within a few business days and will coordinate a fix and disclosure timeline with you.

## Scope and threat model

anvil-serving is a **network-facing server** that routes coding-harness traffic and can hold
**cloud-provider credentials** for its cloud fallback tier. Keep this in mind:

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
  `auth_env`, **any** caller reachable on that network can drive routing and, if you have an
  opt-in metered cloud tier configured, **consume your cloud credentials via fallback**.
  Configure `auth_env` first, always, whenever the router is reachable from anywhere other than
  the box it runs on. Treat network-level identity — a Tailscale ACL, a firewall rule, a private
  mesh — as **defense-in-depth on top of the token**, never as a substitute for it.
- **The token secret itself is never stored in a config file** — only its env-var NAME is (via
  `auth_env`), matching the convention already used for cloud-tier credentials. The value is
  redacted from logs and the decision record by the same `secrets.py` machinery.
- **In the Docker/Compose deployment** (see the README's "Run the router in Docker" section),
  the router is the **only** service published beyond loopback; the local model serves
  (SGLang/vLLM) stay on the internal Docker network / loopback, reachable **only** by the
  router (by service name) — never publish a raw serve directly.
- Cloud credentials are referenced by **env-var name** and are redacted from logs and the decision
  record. Do not paste raw keys into config files.
- The test suite is hermetic and never makes real network or LLM calls.

Out of scope: vulnerabilities in third-party inference engines (SGLang, vLLM), in the cloud
providers themselves, or in harnesses pointed at the router.
