# Installed wheel cannot load bundled benchmark profiles

## Severity

P1 benchmark-worker packaging regression.

## Observed behavior

An installed controller accepted a remote `eval benchmark swe prepare` request
and then returned `profile_unavailable: benchmark profile cannot be loaded`.
The source checkout can load `smoke`, `scout`, and `deep`, but
`benchmarking/profiles.py` resolves them from repository-level
`configs/benchmarks/`. Those JSON files are not included by the current
`tool.setuptools.package-data` declaration.

## Required behavior

- Store the immutable profile JSON files in package data or resolve them via an
  `importlib.resources` package path that is present in wheels.
- Add a built-wheel smoke that imports `load_profile` and successfully loads
  all declared profile names outside a source checkout.
- Verify controller-side `context`, `agentic`, and `swe` prepare operations from
  the built artifact.

## Workaround boundary

Do not copy profile files into an installed environment by hand. That produces
an unversioned worker state which disappears on the next tool update.
