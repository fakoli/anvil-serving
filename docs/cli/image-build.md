# Build a local image with resource limits

`anvil-serving init` installs the matching media build and serve declarations.
Preview the build, confirm it, then start the declared serve:

```bash
anvil-serving host docker-image build media-worker
anvil-serving host docker-image build media-worker --confirm
anvil-serving serves up media-worker --confirm
```

The build command loads `image-builds.toml` from the private operator home.
Paths resolve relative to that file. The installed media serve uses
`up -d --no-build` and the same image tag. Existing operator homes must add the
build declaration and change their media startup command to `--no-build`.
If overriding `COMFYUI_IMAGE`, update the build declaration to the same tag.

```toml
schema = "anvil-serving.image-builds/v1"

[builds.media-worker]
context = "."
dockerfile = "Dockerfile.comfyui"
image = "anvil-comfyui:0.35.2-cu130-ltx25-20260915"
cpus = 2
memory_mib = 8192
platform = "linux/amd64"
timeout_seconds = 7200
```

Builds preview by default. Confirmation builds and loads the local image;
it leaves running services in place. Failures retain a private build log and
return a failure status. Logs retain at most 16 MiB in an owner-protected run
directory; excess output is drained and reported as truncated. Successful
builds return the immutable image ID. The validated Dockerfile is sent as a
fixed byte stream, so changes to its path cannot expand the planned build.

Docker Buildx and its BuildKit daemon must support Linux per-step resource
limits. The command supplies CPU, memory and memory-plus-swap ceilings and
refuses an older CLI without `--resource`. Only single-stage Dockerfiles are
accepted so parallel stages cannot multiply the declared step budget. Build
daemon bookkeeping and image export are outside those execution-step limits.
Inference CPU and memory limits are separate from build limits.

The timeout terminates the local Docker client process tree, including its
Buildx plugin. A timeout or incomplete output drain is a failed build and
reports daemon cancellation as unverified; inspect the owning builder before
retrying. The command does not claim to prove remote BuildKit cancellation.

This is a local CLI operation; there is currently no controller/MCP build
wrapper. Model availability and promotion gates still apply after a build.
