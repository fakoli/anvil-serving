# Managed runtime source inspection

Inspect the fixed accounting-source profile on an already running managed recipe:

```bash
anvil-serving eval runtime-source-inspect --registry recipes.toml --container example --plan runtime-source-plan.json
```

The JSON plan requires exactly these fields: `schema` is `anvil-serving.runtime-source-inspection/v1`; `profile` is `vllm-accounting`; `model` and `revision` select the exact recipe; `recipe_digest` is its semantic SHA-256; `image_id` is the full `sha256:` local image ID; `container_id` is its full 64-hex ID; and `started_at` is its exact UTC container start timestamp. Obtain fresh owner identity through the managed recipe status and bind the reviewed plan to that generation before inspection.

The command checks the recipe and generation before and after a bounded fixed program. It reads only the accounting logger, statistics, asynchronous engine, output processor and scheduler source files, and returns their sizes and SHA-256 hashes plus package version. It performs no inference, tokenizer or GPU work. The result requires independent source comparison; selected-file parity does not attest executed code or the complete image. Missing or changed files, identity drift and malformed capture fail closed.
