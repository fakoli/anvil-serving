# Anvil Media

Anvil Media runs image and video generation as named, versioned workflows.
Callers pick a workflow and its parameters; they never send a raw backend
graph, a file path, or a placement choice. Each run is a durable job that
survives disconnects and ends with an opaque, inspectable artifact.

<!-- product-journey:anvil-media -->

## When to use it

- Agents or apps need image or video generation with predictable inputs.
- You want generation to fail closed when a pinned model asset is missing.
- Long jobs must keep running, and stay cancellable, after the caller leaves.

## Go deeper

- [Operator video workflows](../VIDEO-MCP-WORKFLOWS.md)
- [ComfyUI migration runbook](../COMFYUI-MIGRATION-RUNBOOK.md)
- Command reference: [media](../cli/media.md)
