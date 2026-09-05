# Remote tailnet model endpoints

Anvil Serving can prepare a portable endpoint bundle for a GPU VM or host that
runs Docker Compose. The bundle puts an OpenAI-compatible inference container in
the Tailscale container's network namespace and exposes only `/v1` through
Tailscale Serve. It does not publish a host port or enable Funnel.

This surface is an offline preview. It does not rent hardware, pull images,
start containers, create an auth key, join a tailnet, change grants, or update a
router. A valid render is not proof of provider compatibility or a working
deployment.

## Validate and render

Start with `examples/remote-tailnet/compose-endpoint.json`.
The `registry.example` image references and synthetic model revision are inert
placeholders. Replace each image with the intended image tag **and registry
digest**, and replace the model revision with an immutable revision accepted by
the selected server command.

```console
anvil-serving edge bundle validate --manifest examples/remote-tailnet/compose-endpoint.json
anvil-serving edge bundle render --manifest examples/remote-tailnet/compose-endpoint.json
```

Both commands print JSON and write nothing. The global `--json` envelope labels
these operations by command name without echoing the manifest operand.
`render` returns three file
payloads:

- `compose.json` is a Docker Compose document for a Compose-capable GPU VM or
  host. The inference service uses `network_mode: service:tailscale`, binds its
  server to `127.0.0.1`, and publishes no ports.
- `tailscale-config/serve.json` is the file referenced by `TS_SERVE_CONFIG`.
  Its only handler is `/v1`, its backend is
  `http://127.0.0.1:<port>/v1`, and it contains no `AllowFunnel` entry.
- `router-tier.toml` is an engine-agnostic tier and alias fragment. Replace the
  synthetic tailnet DNS placeholder after enrollment and review it before
  merging it into an operator-owned router configuration.

Tailscale Serve removes the matched mount from the incoming path before it
joins the remainder to the proxy URL. The proxy URL therefore retains `/v1`:
an incoming `/v1/models` becomes the backend's `/v1/models`, rather than
`/models`. The current behavior is visible in Tailscale's
[`serveWebHandler` and reverse-proxy source at revision 5201273a](https://github.com/tailscale/tailscale/blob/5201273aec737d6372ab7423c31c04ca3ca2a0c2/ipn/ipnlocal/serve.go).

The manifest is closed: unknown, missing, or duplicate fields fail validation. The first
adapter is deliberately narrow: `inference.runtime` must be `vllm-openai`.
The renderer constructs vLLM's model and tokenizer revisions, served name,
`127.0.0.1` host, port, context limit, and cache directory from typed fields.
`extra_args` accepts only separate canonical tuning flags: `--tensor-parallel-size`,
`--gpu-memory-utilization`, `--dtype`, `--enable-auto-tool-choice`,
`--tool-call-parser`, and `--enable-request-id-headers`. Duplicate flags,
abbreviations, underscore aliases, configuration files, and interpolation fail
validation. `tool_support: true` requires both auto-tool-choice and a declared
tool parser; the example keeps it false. Tool correctness still requires
qualification with the pinned engine and model. These restrictions constrain
the generated launch arguments; a trusted image must actually honor them.
Engine knowledge remains in this adapter, outside the router tier. Image
references must end in `@sha256:` plus a 64-character lowercase digest. The
repository name uses Docker's component syntax; empty and traversal segments
fail validation. Registry ports and IPv6 literals are outside this first
adapter's supported image-reference subset. See the
[distribution reference grammar](https://github.com/distribution/reference/blob/main/regexp.go).
The model revision must be a 40- or 64-character lowercase hexadecimal object ID.
`served_model` must be a canonical remote repository ID, `organization/model`;
local filesystem models are outside this adapter's revision contract.
The adapter accepts up to 96 ASCII characters in each segment, requires an
alphanumeric start, forbids trailing `-` or `.`, repeated `--` or `..`, and a
`.git` suffix. The hub also validates repository syntax; existence, revision
availability, and access rights require deployment-time checks. See the
[official repository validator](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/utils/_validators.py).
Environment fields contain names only. Tailnet auth and inference auth must use
different names, and tailnet state cannot share the model cache volume. At
Compose evaluation time, the
rendered document requires those variables to exist without recording their
values.

The vLLM adapter requires `api_key_container_env: "VLLM_API_KEY"` and
`cache_mount: "/root/.cache/huggingface"`. A different environment name would
not enable vLLM authentication; an arbitrary mount could hide runtime files.

The tailnet state volume is persistent because a Tailscale container without a
persistent `TS_STATE_DIR` registers as a new node after restart. Keep the auth
key outside the repository. Use a tagged, ephemeral auth key and limit the tag
with tailnet grants to only the clients and ports that need inference access.
See Tailscale's [Docker parameters](https://tailscale.com/docs/features/containers/docker/docker-params),
[auth-key guidance](https://tailscale.com/docs/features/access-control/auth-keys),
and [inference-server guide](https://tailscale.com/docs/use-cases/ai-infrastructure-access/connect-inference-servers).

The example command uses vLLM's OpenAI-compatible server shape. vLLM documents
the `vllm/vllm-openai` container and `--api-key`/`VLLM_API_KEY`, but its security
guide warns that several non-OpenAI endpoints are not protected by that API
key. Exposing only `/v1` is therefore a required boundary, not a convenience.
Review the current [vLLM container instructions](https://docs.vllm.ai/en/latest/deployment/docker/)
and [security guidance](https://docs.vllm.ai/en/stable/usage/security/) before
pinning an image.

## Vast.ai boundary

A standard Vast.ai rental is one Docker container. Vast's current technical FAQ
states that Docker-in-Docker is disabled and recommends a multi-service image or
separate instances when several services are needed. That shape cannot run the
two-service Compose bundle, so a manifest with `target: "vast-container"`
returns the typed `unsupported-target` result.

A Vast-specific deployment needs a separately designed and qualified custom
image that supervises both tailscaled/containerboot and the inference server in
one container, retains state across the provider's lifecycle, handles signals
and failures correctly, and still exposes only the `/v1` Serve path. Vast's
template settings, GPU runtime, storage, and secret injection also need
provider-side qualification. This repository has not performed that deployment
test. See Vast's [technical FAQ](https://docs.vast.ai/guides/reference/faq/technical),
[networking guide](https://docs.vast.ai/guides/instances/connect/networking),
and [template settings](https://docs.vast.ai/guides/templates/template-settings).

## Promotion gate

Readiness sends the configured upstream bearer credential to `/v1/models` when
it is available. Existing anonymous local tiers remain supported. For a remote
endpoint, verify that the same path rejects an unauthenticated request and
accepts the intended credential; an anonymous health success does not prove
authentication is enforced.

After rendering, an operator still needs to:

1. Review and materialize the three payloads outside the public repository.
2. Create the tagged ephemeral auth key and tailnet grant without placing either
   secret in a tracked file.
3. Start the Compose stack on a qualified host and confirm its exact image and
   model revisions.
4. Verify authenticated `GET /v1/models` over the tailnet, then use the normal
   router preflight and benchmark gates before adding the tier to a live route.

Provider launch, tailnet enrollment, router configuration, and route promotion
remain separate operator actions.

The Serve document declares only a `/v1` handler. The cited Tailscale source
rejects paths that clean outside that mount. Provider/backend behavior across
the complete path, including additional URL decoding, remains a deployment
acceptance check.
