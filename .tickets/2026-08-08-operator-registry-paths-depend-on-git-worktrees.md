# Live serve registry paths point into disposable git worktrees

**Status:** Open — operator repair identified; product hardening proposed

## Problem

The Dark operator manifest `serves.tp2-campaign.toml` carries 22 `--registry`
paths that resolve into four **git worktrees** of the product checkout rather
than into the canonical clone or the operator home:

| worktree suffix | referencing entries |
| --- | --- |
| `-wt-deepseek-0731-nvfp4` | 9 |
| `-wt-deepseek-offload` | 7 |
| `-wt-tp2-model-campaign` | 5 |
| `-wt-deepseek-r16-maxseq16-igpu` | 1 |

The single reference in the last row is the recipe for
`tp2-deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k` — the **currently
promoted `llm.primary`**. Its recipe is loaded from a worktree checked out on
an unrelated feature branch (`codex/session-retro-release-readiness`).

Git worktrees are disposable by design. A routine `git worktree remove` or
`git worktree prune` — or simply finishing that branch's work — deletes the
recipe the promoted primary needs to start. Nothing in the manifest signals
that dependency, and because the `--registry` path lives inside an `up`
command string it is not validated at manifest load; it surfaces only when
`serves mode enter` has already begun its transaction. That is precisely the
failure mode recorded in
`2026-08-06-duplicate-serve-names-shadow-stale-registry-path.md`, whose repair
introduced these absolute paths.

The 2026-08-06 repair was correct in substance — it removed a broken
`{dir}/../..` relative path — but it re-anchored the live primary onto a
checkout that is even shorter-lived than the one it replaced.

## Evidence

Collected 2026-08-08 while restoring the promoted DeepSeek primary.

- All 17 distinct recipe files referenced from worktrees also exist in the
  canonical checkout's tracked `configs/`.
- Byte comparison reports every pair as differing, but that is line endings
  only. Normalizing CRLF, the promoted primary's recipe
  (`deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k-recipe.toml`) and
  `tp2-model-campaign-recipes.toml` are **identical** to their canonical
  counterparts across every worktree that holds a copy.
- `deepseek-v4-flash-0731-r16-b12x-dspark5-128k-recipe.toml` differs by four
  normalized lines and needs a content decision before repointing.

So for the promoted primary the repair is a path change with no content
change, and it is verifiable rather than assumed.

## Required behavior

1. Operator: repoint the promoted primary's `--registry` at the canonical
   product checkout's `configs/`, which is stable machine-local state, not a
   worktree. Do the same for the remaining entries whose recipes are verified
   content-identical; resolve the four-line divergence on the 128K recipe
   before moving it.
2. Product: a serve whose `up` command names a `--registry` path should have
   that path validated when the manifest loads, in the same pass that already
   validates `router_config`. A registry that cannot be read is not a runtime
   surprise, it is a manifest defect.
3. Product: consider refusing — or warning on — a registry path that resolves
   inside a linked git worktree. `git rev-parse --git-common-dir` differing
   from `--git-dir` is a cheap, exact test, and a promoted serve should never
   depend on a checkout that routine cleanup deletes.

## Note on layering

Recipes are portable product artifacts and belong in the public checkout;
route assignments and host overlays belong in the operator repository. The
current arrangement inverts that: the operator manifest reaches into whichever
feature branch happened to author the recipe. Anchoring on the canonical
checkout restores the intended direction without copying public content into
the private repository.
