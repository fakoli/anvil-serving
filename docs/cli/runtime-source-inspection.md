# Managed runtime source inspection

Inspect the fixed accounting-source profile on an already running managed recipe:

```bash
anvil-serving eval runtime-source-inspect --registry recipes.toml --container example --plan runtime-source-plan.json
```

The JSON plan requires exactly these fields: `schema` is `anvil-serving.runtime-source-inspection/v1`; `profile` is `vllm-accounting`; `model` and `revision` select the exact recipe; `recipe_digest` is its semantic SHA-256; `image_id` is the full `sha256:` local image ID; `container_id` is its full 64-hex ID; and `started_at` is its exact UTC container start timestamp. Obtain fresh owner identity through the managed recipe status and bind the reviewed plan to that generation before inspection.

The command checks the recipe and generation before and after a fixed program. It uses the fixed `/opt/venv/bin/python3` interpreter in isolated, no-site mode and hashes five accounting source files from each existing fixed location: `/opt/vllm/vllm` and `/opt/venv/lib/python<major>.<minor>/site-packages/vllm`. A missing location is reported by its absence; if neither location exists or either present location is incomplete, inspection fails closed. It does not discover or identify the active import path, load a tokenizer, run inference, or access a GPU. Independent source comparison is required: static file parity does not attest imported or executed code, package version, or the complete image.

The capture sets a ten-second internal deadline before walking files. A parent timeout stops the local Docker CLI but does not prove the daemon-owned exec has terminated; its custody remains unknown. The internal deadline does not guarantee termination during uninterruptible kernel I/O. Missing or changed files, identity drift and malformed capture fail closed.
