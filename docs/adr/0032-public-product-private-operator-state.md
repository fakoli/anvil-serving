# ADR-0032 — Public product, private operator state

- **Status:** Accepted
- **Date:** 2026-08-02
- **Relates to:** ADR-0020; ADR-0027; `docs/OPERATOR-PRIVACY.md`

## Context

Anvil Serving is becoming a public, production-facing product. The same source
tree has also carried workstation-specific topology, active deployment choices,
personal paths, and raw operator evidence. A rule that protects only `docs/`
does not work: repository files, examples, tests, agent instructions, and Git
history can all become public or be copied into a release artifact.

The product already has the correct runtime seam. `anvil-serving init` writes an
operational configuration set under `ANVIL_SERVING_HOME` (default
`~/.anvil-serving`), and commands resolve operator configuration there. We can
use that seam without redesigning the router, recipe format, or CLI.

## Considered options

1. Keep personal deployment state in the product repository but rely on path
   conventions. This is convenient locally, but one mistaken add or broad Git
   operation can publish private state.
2. Remove all workstation examples and evidence. This reduces exposure but
   also removes useful product templates and independently auditable results.
3. Treat the product repository as public and keep operator state in a private
   companion repository selected through `ANVIL_SERVING_HOME`.

## Decision

Every tracked file in `anvil-serving` is public by default. The public product
repository contains source, schemas, tests, generic examples, portable recipes,
documentation, and sanitized bounded evidence. Public recipes may document
qualification, but they do not establish an operator's current promoted route.

Real operator state belongs in a separate private repository or an untracked
operator home. This includes:

- reachable network identities and bindings;
- real GPU UUIDs and machine-local paths;
- active/promoted model assignments and deployment overlays;
- private runbooks, session traces, and unsanitized working evidence.

Credentials are never committed, including to the private repository. Private
configuration refers to environment variables or file-backed secrets.

The private repository can be used directly as `ANVIL_SERVING_HOME`, or can
contain an `operator-home/` directory selected by that variable. Public
`examples/` and packaged scaffold files remain generic and byte-synchronized.
Host detection may insert real values only while `init` writes into the private
operator home.

Publishing evidence is an explicit promotion step: select the bounded material
that supports a public claim, sanitize it, and publish it under ADR-0027. Raw
working evidence is not copied into the public tree.

The current-snapshot gate requires:

1. no credential literals, capability URLs, credential-bearing remotes, real
   tailnet/MagicDNS identities, or personal home paths in tracked files;
2. generic examples and package scaffold copies remain synchronized;
3. runtime resolution continues to honor `ANVIL_SERVING_HOME` without using
   repository examples as a live fallback; and
4. CI runs both the semantic boundary scan and a pinned Gitleaks snapshot scan.

Existing Git history is a separate security surface. This ADR does not
authorize deleting the repository, rewriting history, force-pushing, rotating
credentials, or changing a live deployment. Those operations require a frozen,
backed-up, separately approved migration.

## Consequences

- Product development stays in one public repository without hiding core code,
  portable recipes, tests, or sanitized benchmark evidence.
- Operator changes remain reviewable and reversible in a private Git history,
  while secret material stays outside Git.
- Skills and commands keep one stable runtime contract:
  `ANVIL_SERVING_HOME` identifies the operator configuration root.
- Contributors must assume that any tracked path can be published; there is no
  special "repo-internal but safe for personal data" directory.
- The first migration can sanitize high-confidence private identifiers without
  a broad rewrite of historical logs. Full-history cleanup remains a separate
  controlled operation.
