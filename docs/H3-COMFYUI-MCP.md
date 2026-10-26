# Cobijada H3 through ComfyUI MCP

This bundle exposes the retained generation and guided upscale recipes as two
named Anvil Media workflows. Graph and packaging tests pass; deployment and an
actual cold-start MCP qualification are still pending. Public templates remain
unavailable until their operator deployment passes its own gates.

## Input contract

| Workflow, version `v1` | Caller input with profile `standard` | Locked profile |
| --- | --- | --- |
| `video.h3-cobijada-t2v-v1` | `prompt`, `seed` | 864x480, 124 frames, 24 fps, 20 res_multistep/simple steps, BasicGuider |
| `video.h3-cobijada-upscale-v1` | `source_video`, an owned MP4 artifact ID | 1.58203125 megapixels in 1024-based units, denoise 0.1, two Euler refinement steps, guide strength 1, 25,000-token tiles |

The upscale profile produces 1728x960 from the tested 864x480 source. Its fixed
pixel-area target preserves aspect ratio; arbitrary inputs do not necessarily
receive a 2x enlargement. The upstream node uses seed 444 and 192-pixel overlap.
Its guide LoRA is an upscale adapter, not a character-identity LoRA.

`registry.h3.json` selects this bundle separately from the default image/Wan
registry. `bundle.h3.lock.json` pins four generation assets and two additional
upscale assets by revision, byte length and SHA-256. Canonical JSON files are in
`configs/media/workflows/`, with byte-identical packaged copies under
`anvil_serving/_media_workflows/`.

## Run a shot and upscale its result

Call `media_workflow_run`:

```json
{
  "workflow_id": "video.h3-cobijada-t2v-v1",
  "version": "v1",
  "quality_profile": "standard",
  "parameters": {"prompt": "Two hosts talk in a quiet studio.", "seed": 42},
  "idempotency_key": "episode-01-shot-001"
}
```

If the worker is cold, retain its exact `resumeBundle` and lifecycle preview.
The operator connection applies the returned `media_worker_prepare` action
under existing human authorization. Retry the identical request and
idempotency key after readiness. Ordinary media-submit scope does not silently
start a worker.

Poll `media_job_status` until completion. Submit the returned MP4 artifact ID:

```json
{
  "workflow_id": "video.h3-cobijada-upscale-v1",
  "version": "v1",
  "quality_profile": "standard",
  "parameters": {"source_video": "art_REPLACE_WITH_OWNED_ARTIFACT_ID"},
  "idempotency_key": "episode-01-shot-001-upscale"
}
```

Inputs must be unexpired, caller-owned MP4 artifacts of at most 32 MiB. Their
content hash is verified before upload. Only the private execution graph
receives a worker filename; callers keep opaque IDs. Inspect results with
`media_artifact_inspect` and export required media before seven-day retention
expires. Cancellation uses `media_job_cancel` and its existing exclusive-slot
safety rules. After dependent jobs terminate, release a job-owned worker using
the existing operator `media_worker_teardown` transaction.

## Runtime and installation boundary

The lock identifies ComfyUI 0.42.0, PyTorch 2.13.0+cu130 and the retained local
image ID. That image ID is not a public registry pull reference. Git metadata
was excluded from the image, so a later source checkout or image tag does not
establish its exact source commit. Preserve the image or qualify a reconstructed
runtime independently.

Installation uses the pinned
[H3 Video Upsampler](https://github.com/sandpies/ComfyUI-H3-Video-Upsampler/tree/3403ef7a81742fb76eb1633bdd5bb748ad85fd53),
[Cobijada checkpoint](https://huggingface.co/Winnougan/Cobijada_Minimax-H3_Hybrid_Pruned_ComfyUI)
and [Comfy H3 components](https://huggingface.co/Comfy-Org/MiniMax-H3).
The underlying [MiniMax H3 terms](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)
still apply, including territorial restrictions; a community merge does not
remove them.

Private installation owns storage, exact runtime identity, custom-node
allowlist, GPU selection, endpoints, secret references, Windows safeguards and
resource reservations. Weights and credentials are outside this public bundle.
Start the worker through managed lifecycle commands. Derive its declared and
engine budgets from measured evidence; the older generic worker's 4-GiB reserve
must not be assumed to fit H3.

## Podcast integration limits

Generation creates fresh joint video/audio. This graph does not ingest an
external recorded voice, train/apply character LoRAs, or establish three-host
identity continuity across shots. Those require separate workflow qualification.

The upscale node passes the original decoded soundtrack and source FPS forward.
SaveVideo re-encodes audio, so compressed AAC bitstream identity is not promised.
Keep the podcast audio master in the editing application and remux it during
final assembly when exact preservation is required. External recordings must
first become supported bounded inputs through the owning application; arbitrary
filesystem paths are never MCP parameters.
