---
hide:
  - toc
---

# Operator cheat sheet

Every journey's commands, in order, on one page. Values in `<ANGLE_BRACKETS>`
are yours to fill in. Commands with `--dry-run` only preview; repeat them with
`--confirm` to act. Print this page or keep it open next to your terminal.

<!-- product-journey:cheatsheet -->

## Rules that hold everywhere

- **Preview first.** Lifecycle commands that change state preview with
  `--dry-run` and act only with `--confirm`.
- **Exact aliases.** A request names one capability alias. An unknown alias
  returns 404; an unavailable tier returns an error, never another model.
- **Evidence is separate.** Readiness means an endpoint can receive traffic.
  Only `eval preflight` and benchmark artifacts qualify it, and neither
  promotes a model.
- **Loopback by default.** Services bind `127.0.0.1` on the host that owns
  them. Use private or tailnet addresses for cross-host traffic.

## Take the rest with you

- [Serve recipes](recipes/index.md) — every published recipe as a file, or all
  of them as one zip.
- [Benchmark results](benchmarks/results.md) — every record, with the full
  index as JSON.
- [Command index](CLI.md) — every command family, option, and policy.
- `anvil-serving product journey <FAMILY>` prints any journey above from the
  CLI.
