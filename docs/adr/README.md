# Architecture Decision Records (ADRs)

This directory records the **significant architecture and design decisions** for anvil-serving —
the context, the options weighed, the decision, and its consequences — so the *why* survives the
people and the chat logs.

## Convention

- **One file per decision:** `NNNN-short-kebab-title.md` (zero-padded, sequential — `0001`, `0002`, …).
- **Format:** Context → Considered options → Decision → Consequences. Start from [`template.md`](template.md).
- **Status:** `Proposed` · `Accepted` · `Deferred` · `Superseded by ADR-NNNN`.
- **Never delete an ADR — supersede it.** A reversed decision is itself history; write a new ADR that
  supersedes the old one and mark the old one `Superseded`.
- **When to write one:** any non-trivial, hard-to-reverse, or cross-cutting decision — a product
  contract, a routing/auth model, a dependency, a protocol or wire-format choice, a security posture.
- **New ADR:** copy `template.md` → next number, fill it in, link related ADRs/issues, and add it to
  the index below.

## Index

| # | Title | Status |
|---|-------|--------|
| [0001](0001-cloud-cost-and-subscription-auth.md) | Cloud cost & subscription auth — why anvil should not relay cloud | Accepted |
