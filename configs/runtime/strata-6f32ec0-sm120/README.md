# Strata Flash-Next IQ4_XS feasibility artifact

**Built and preparation-verified, not runtime-qualified or promoted.** This is the initial RTX 5090 / 96 GB
Windows + Docker Desktop WSL candidate observed on 2026-10-05. The current
configuration is 32,768 tokens, one request, int8 KV, 36 GiB resident expert budget,
requested speculation 4, vision, and a 4,096 MiB GPU reserve. CPU preparation and
derived-image revalidation both exited zero: 51 outputs totaling 8,423,482,344
bytes, including 31 MTP tensor files. These checks establish artifact integrity,
not model quality, GPU memory fit, throughput or readiness.

`build-provenance.json` records the actual built image
`sha256:5f944f89efe58a127f934703449f6871583ffcda62d1fae4e4d58556fb5f3cea`,
Strata `6f32ec070f23ced9f50e704d854d775da52591ab` (0.1.39), llama.cpp
`3cf03257f219afbe7334045ff7c6a06ac68c627d`, the CUDA base and exact custom-file
hashes. The source files are byte-identical to the private build inputs. The
Dockerfile retains upstream comments for that identity; its old raw-Docker and
automatic-download examples describe the upstream entrypoint, not this artifact's
managed operational path. Use the Anvil commands below. This entrypoint downloads
nothing and does not invoke upstream setup at container startup.

The inherited OCI version label `24.04` identifies Ubuntu, not the engine.
Apt packages and all transitive build dependencies were not independently locked
in the historical build. A rebuild must retain and qualify its new immutable
image identity; it must not claim byte-identical output from source pins alone.
The image retains `anvil/python-packages.txt` for the installed Python inventory.

## Reproduce through Anvil Serving

Use a private operator build directory. Clone the official source and verify the
exact commit before copying this directory's `Dockerfile` as `Dockerfile.anvil`
and its `anvil/` directory into that checkout. Do not copy model weights into the
build context. Put `image-builds.toml` beside the checkout named `strata-checkout`.
For example, the source acquisition is:

```text
git clone --no-checkout https://github.com/Niko1221/Strata strata-checkout
git -C strata-checkout checkout --detach 6f32ec070f23ced9f50e704d854d775da52591ab
git -C strata-checkout rev-parse HEAD
```

The operator topology must explicitly resolve this host, command runtime, GPU,
model-catalog resource and model-serve resource. Supply the corresponding private
`--topology`, `--command-host`, `--command-runtime`, `--target` and `--transport`
arguments to the commands below. Paths and GPU selectors remain private. The
build declaration bounds execution steps to 4 CPU / 16 GiB with eight compiler
jobs; build export and daemon overhead remain outside those per-step bounds.

```text
anvil-serving host docker-image build strata-flashnext-iq4xs --config image-builds.toml --dry-run
anvil-serving host docker-image build strata-flashnext-iq4xs --config image-builds.toml --confirm
anvil-serving host docker-image inspect <resulting-immutable-image-id>
```

The build preview requires `--dry-run`; omitting both it and `--confirm` returns
`confirmation_required`.

Copy `serve-recipes.toml` into the private operator home and use the verified
resulting image ID if rebuilding. The published ID is a retained local image,
not a promise that an image registry hosts it.

Download into `vllm-hfcache` using `models pull` with an immutable downloader image
that supplies the `hf` executable. The observed downloader was
`sha256:b18ca8c30803f2de1ddfb2d7ace116161aa11c315018c9e09da283ad71ec8ead`;
it is distinct from the Strata runtime image, which does not supply `hf` on PATH.
Preview, then confirm each exact pull. Allow only one cache writer at a time.

| Repository | Revision | Include | Expected bytes |
| --- | --- | --- | ---: |
| `unsloth/Qwen3.8-Flash-Next-GGUF` | `38bb39ee97821de2c9009abb7e93950eec396e66` | `UD-IQ4_XS/*` | 93,682,584,224 |
| `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` | `ed59f92082b1e93c0e96d60a8b11aab089b52f09` | `mmproj-Qwen3.8-Flash-Next-BF16.gguf` | 907,543,008 |
| `Qwen/Qwen3.8-Flash-Next` | `de4b8e4d43b917e7706784d8bb445c9af86a3540` | Each of the 28 exact `mtp.files[].rfilename` entries | 55,167,376,024 total |

`anvil/artifact-manifest.json` contains every individual size/SHA256. Use each
MTP filename and size separately, not a wildcard for the whole checkpoint.
Retain at least 30 GiB storage headroom beyond remaining downloads/preparation.
The existing `models cache inventory` gate and each pull's `--expected-bytes`
check must pass. The main command shape is:

```text
anvil-serving models pull unsloth/Qwen3.8-Flash-Next-GGUF --revision 38bb39ee97821de2c9009abb7e93950eec396e66 --include "UD-IQ4_XS/*" --volume vllm-hfcache --image <pinned-hf-image> --no-token --expected-bytes 93682584224 --headroom-gib 30 --dry-run
```

For execution replace `--dry-run` with `--confirm`; preserve every other pin.
Wait for completion and exact snapshot verification before the next pull.

First load the `unsloth/Qwen3.8-Flash-Next-Strata-UD-IQ4_XS-Prepare` recipe with
`models recipes load --registry <private-registry> --container strata-flashnext-prepare`
and the discovered `--gpu-device` selector. Preview with `--dry-run`, then replace
it with `--confirm`.
It exposes no model endpoint and runs only CPU hashing/conversion under a
16 GiB hard limit, zero swap and an 8 GiB guest reserve. Use `models recipes
status` and `models recipes logs`; a successful launch is not completed
preparation. Require an exit code of zero and the actual verified receipt.

The inference recipe `unsloth/Qwen3.8-Flash-Next-Strata-UD-IQ4_XS-32K-C1` uses
52 GiB hard RAM, zero swap and an 8 GiB guest reserve. Windows also enforces its
independent physical-memory reserve. Use only the managed recipe load/status/
logs/unload lifecycle; qualify it on an isolated GPU after preparation and memory
admission. No route or client assignment is part of these artifacts.

## Preparation and limits

The wrapper verifies every input hash, creates the compatibility BF16 pack,
extracts MTP tensors from verified local source shards with pinned upstream code,
quantizes MTP experts to Q2_0, then records all output sizes/hashes. It uses a
single writer lock and commits `/data/prepared` by atomic rename. Completed
preparation is revalidated on reuse. Interrupted preparation starts a new staging
directory on retry; it is not resumable and does not automatically remove partial
work. Check capacity and retain failure evidence before an explicit cleanup.

The pack references the original GGUF expert shards; do not delete those inputs.
The native IQ loader at this source pin requires MTP, so a no-MTP smoke is not a
supported alternative. The compatibility conversion rounds some Q8 projections
to BF16; upstream quality results are an external prior, not local proof. Vision
and tool correctness require independent local gates. Record requested spec4
separately from engine-effective drafting, which can include suffix lookup.

WSL keeps long-context KV in VRAM. The resident expert budget covers the CPU
complement of GPU-held experts, not a second complete expert set. Measure the
startup allocation before increasing host memory. Cache reclamation can leave
free pages inside WSL without immediately returning them to Windows; preserve
the conservative Windows admission check rather than forcing a load.

## Immutable runtime variants

`variants/` is **built and preparation-verified, but runtime-unqualified**. Its
verified image is
`sha256:d8a0d5b90db4e5a3b8770a60298c97cf73bfaaa72c5d5bcb4e1f920a4153fbc2`.
The single derived layer uses the original image by digest, leaves original
preparation code/receipt/config untouched,
and prints the verified original receipt plus selected runtime identity to stdout
for capture through managed logs. The completed CPU revalidation captured this
receipt; its SHA256 and completion summary are in `build-provenance.json`. The
original Dockerfile comment and baked config-manifest status still say draft;
they are retained byte-for-byte as actual reviewed build inputs.

`variants/serve-recipes.toml` provides all five exact-image recipes and records
each selected JSON hash. Their unqualified status grants no route or promotion.
The fixed configuration names are:

| Config | Context | Concurrent requests | Resident budget | Exact container RAM |
| --- | ---: | ---: | ---: | ---: |
| `32k-c1-r36` | 32,768 | 1 | 36 GiB | 52 GiB |
| `128k-c1-r36` | 131,072 | 1 | 36 GiB | 52 GiB |
| `128k-c4-r36` | 131,072 | 4 | 36 GiB | 52 GiB |
| `262k-c1-r36` | 262,144 | 1 | 36 GiB | 52 GiB |
| `128k-c1-r48` | 131,072 | 1 | 48 GiB | 66 GiB |

All retain int8 KV and vision; each JSON has a baked SHA256 allowlist entry.
The entrypoint accepts only `--prepare-only` and `--runtime-config <name>`;
it has no arbitrary path or server-argument passthrough. Before inference it
requires that profile's exact cgroup RAM ceiling and zero swap. CPU preparation
retains its separate managed limit. The server receives its baked port explicitly;
otherwise the upstream server CLI default would override the configuration.

To reproduce the derived image after review, use the retained base image and:

```text
anvil-serving host docker-image build strata-flashnext-variants --config variants/image-builds.toml --dry-run
anvil-serving host docker-image build strata-flashnext-variants --config variants/image-builds.toml --confirm
```

Its build steps are bounded to one CPU and 1 GiB RAM with zero additional swap.
Copy the derived recipe registry into the private operator home, retaining the
verified image/config pins. Use `models recipes load` with the exact selected
recipe, private topology/GPU arguments and an isolated container name; preview
with `--dry-run` before `--confirm`. Use managed status/logs/unload throughout.

The generic recipes use `STRATA_ALLOWED_HOSTS=models.example.invalid`. Before
reverse-proxy use, replace that reserved example only in the private registry
with the exact hostname callers send in `Host`. Do not use a wildcard or commit
an operator hostname. The pinned server reads this environment variable; without
the exact permitted name it can reject otherwise healthy proxy requests with 403.
This host allowlist does not replace authentication at the exposure boundary.

Load the 32K C1 configuration first; expanded configurations require prior
feasibility evidence and independent capacity gates. Probe 128K
C1 before 262K C1 or 128K C4. Batched decoding disables MTP and has different
penalty support; do not attribute a C4 result to the C1 speculative path. The
48 GiB resident variant requires a separately admitted host/VM memory ceiling
and measured justification; it is not compatible with the initial container
budget. No pack reconversion is needed solely to select these runtime variants.

The derived local base must resolve to the recorded digest. If the builder cannot
resolve the retained local image, stop and diagnose image transport; do not fall
back to an unpinned tag, publish an image, or rebuild the base silently.
