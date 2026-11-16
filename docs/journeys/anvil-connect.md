# Anvil Connect

Anvil Connect publishes local browser apps — dashboards, notebooks, chat UIs —
through one self-hosted, authenticated edge. You declare each resource once;
Connect renders the TLS edge, single sign-on, and reverse tunnel for it. The
apps themselves need no changes, and identity stays on infrastructure you own.

<!-- product-journey:anvil-connect -->

## When to use it

- You want to reach local web tools from anywhere without opening ports.
- Several people need their own logins and per-app access.
- You want sign-in, sessions, and access changes to be explicit and auditable.

## Go deeper

**Understand it**

- [Overview](../CONNECT-OVERVIEW.md)
- [Architecture](../ANVIL-CONNECT.md)
- [Transport](../ANVIL-CONNECT-TRANSPORT.md)
- [Identity](../ANVIL-CONNECT-IDENTITY.md)

**Set it up**

- [Install Connect](../ANVIL-CONNECT-INSTALL.md)
- [Invite a developer](../ANVIL-CONNECT-ONBOARDING.md)
- [Device login](../ANVIL-CONNECT-DEVICE-LOGIN.md)
- [Migrate an existing setup](../ANVIL-CONNECT-MIGRATION.md)

**Run it**

- [Manage access in Workbench](../ANVIL-CONNECT-ACCESS.md)
- [Router keys](../ANVIL-CONNECT-ROUTER-KEYS.md)
- [Password setup and reset link lifetime](../CONNECT-RESET-LINK-LIFETIME.md)
- Command reference: [connect](../cli/connect.md)
