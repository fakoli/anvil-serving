# Setuptools license metadata deprecation

## Status

Open; non-blocking for Anvil Serving 0.20.0.

## Observed

The clean release build succeeds, but current setuptools warns that the
`project.license` TOML table and the `License :: OSI Approved :: MIT License`
classifier are deprecated. The warning states that the table form will no
longer be supported after 2027-02-18.

## Impact

There is no runtime, wheel-metadata, or publication failure today. A future
setuptools upgrade can turn the deprecated metadata into a release-build
failure if the project does not migrate first.

## Proposed fix

- Replace the license table with an SPDX expression accepted by the project's
  supported setuptools floor.
- Remove the redundant license classifier once the SPDX metadata is canonical.
- Rebuild the sdist and wheel, run `twine check`, and repeat the clean-install
  wheel smoke on Windows and Linux CI.

## Evidence

Anvil Serving 0.20.0 still built successfully; both artifacts passed `twine
check`, and the wheel passed the isolated package-data and CLI-entrypoint smoke
test. This ticket records future compatibility debt rather than changing
packaging semantics during the DeepSeek qualification release.
