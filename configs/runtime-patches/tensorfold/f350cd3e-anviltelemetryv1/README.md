# TensorFold telemetry v1 patch catalog

Upstream: [ashhart/TensorFold](https://github.com/ashhart/TensorFold), pinned to
[`f350cd3e`](https://github.com/ashhart/TensorFold/commit/f350cd3ea253a042e205746928271f9d8f71a82d).
See the [catalog policy](../../README.md) for the patch-only upgrade workflow.

After installation configures the private `image-builds.toml` declaration,
preview and build through the existing managed command:

```sh
anvil-serving host docker-image build tensorfold-telemetry-v1
anvil-serving host docker-image build tensorfold-telemetry-v1 --confirm
```

Installation sets the declaration's `context` to this directory, `dockerfile`
to `Dockerfile`, and `base_image` to the reviewed author/toolchain image's
qualified repository reference with its immutable `@sha256` digest. Keep that
image reference, the output image tag, local paths and CPU/memory/time budgets
in private operator configuration. The base image must provide the TensorFold
Git checkout at `/src/tensorfold`, the original author patches at
`/opt/tf-patches/engine`, Zig 0.17.0, CUDA and xgrammar. No upstream source,
author patches, model files or deployment settings are vendored here.

Preview validates the declaration, base-image reference, limits and Dockerfile.
It does not apply patches or seal mutable `COPY` inputs. The confirmed build
verifies the original 101 author patch hashes, the exact preimage tree, this
manifest and all five owned patches before compiling. It builds and loads an
image without replacing a running serve. Candidate qualification and a reviewed
live cutover remain separate gates.

`manifest.toml` is the application order and raw-file SHA-256 authority. Its
bytes and all five patch files are preserved from the qualified series:

- TensorFold upstream commit: `f350cd3ea253a042e205746928271f9d8f71a82d`.
- Author recipe prerequisite: `0a5297c912e118490b8bb3734d9e20107df7f6f2`,
  with all 101 original patches unchanged.
- Post-author Git tree: `9edb171eee69a8b8c3138d39755c52b1380c23e4`.
- Author hash-map SHA-256: `544dec546163a679ab4a62ccbd62c68a5a27608669b01d2739191ec09416378c`.
  This hashes a UTF-8 JSON object mapping recipe-relative patch filenames to
  raw-file SHA-256, with sorted keys, compact separators and no trailing newline.
- Manifest SHA-256: `fbf6b5b9a919e772c3cd9b8e369419aae5c94c2828a212424c4b3a83d7604e1f`.
- Final Git tree: `e7acc47a4a42ba8ac5fbaf74fff20ed4e7856c17`.

The fifth patch includes required production boundary/publication wiring and
regression tests; it is not optional test scaffolding. The series changes
telemetry, not scheduling, model selection or cache policy. See
[CONTRACT.md](CONTRACT.md) for metric units, populations, completeness and
unavailable derived bounds.

Run the catalog's static checks without Docker, network or a GPU:

```sh
python3 -B configs/runtime-patches/tensorfold/f350cd3e-anviltelemetryv1/test_apply_anvil_patches.py
```

The same developer check accepts `--engine-source <local-engine-repository>`
and `--recipe-source <local-author-recipe-repository>` together. It creates a
temporary clone, checks out the pinned engine revision, reads the 101 patches
from the exact recipe commit, and verifies both source trees. It also retains
the applicator's order, bad-hash, wrong-version and source-drift regressions.
Optional `--zig <zig-0.17.0-executable>` runs the three CPU telemetry suites in
that reconstructed source. Original repositories remain unchanged.

An upstream or author-series upgrade requires a new reviewed catalog revision:
inspect upstream capabilities, deliberately rebase only the owned patches,
update prerequisite pins/hashes, run CPU checks, build a fresh candidate and
requalify before any live adoption. Keep historical pins and evidence intact.
Preparing a community submission is a separate change; this catalog neither
submits upstream nor silently rewrites the qualified five-patch series.
