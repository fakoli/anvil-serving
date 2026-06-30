# Contributing to anvil-serving

Thanks for your interest in improving anvil-serving. This guide covers the local setup and the
few hard rules that keep the project shippable.

## Local setup

Requires Python >= 3.11 (the router config loader uses stdlib `tomllib`).

```bash
git clone https://github.com/fakoli/anvil-serving
cd anvil-serving
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
```

The full suite is hermetic and should pass offline on Linux, macOS, and Windows.

## The hard rules

These are non-negotiable — a PR that breaks one will not pass review:

1. **stdlib-only runtime.** The package ships with `dependencies = []` and must stay that way. Do
   not add a third-party runtime dependency. Test-only tooling belongs in the `dev` extra
   (`pytest`); nothing it pulls in may leak into the importable package.
2. **Bind `127.0.0.1`, never `localhost`.** On Windows, `localhost` can trigger a ~21s IPv6
   lookup stall. Every host default, example, doc snippet, and test must use `127.0.0.1`.
3. **Tests stay hermetic.** No real network, no real LLM endpoint, no GPU. Use fixtures and fakes;
   a test that reaches out is a broken test. This is what lets CI run the same suite on
   `ubuntu-latest` and `windows-latest`.

## Workflow

1. Branch off `main` (e.g. `fix/...`, `feat/...`, `docs/...`).
2. Make the change; add or update tests alongside it.
3. Run `python -m pytest tests/ -q` locally and make sure it is green.
4. Open a PR. CI runs the suite across `{ubuntu, windows}` x `{3.11, 3.12, 3.13}`, builds the
   wheel, and smoke-tests a clean install — it must be green before merge.

Keep PRs focused: one logical change per PR, with a clear description of the *why*. If you are
unsure whether a change fits the direction, open an issue first.
