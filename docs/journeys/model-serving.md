# Model Serving

Model Serving is how you get a model running on your own GPUs and keep it
reproducible. You pick a pinned recipe, preview exactly what will start, start
it, and watch it — without the serve changing what any caller is routed to.

<!-- product-journey:model-serving -->

## When to use it

- You want to run a local model with a known-good engine, image, and flags.
- You are trying a candidate model next to the one you already serve.
- You need to see which serves own which GPUs before you change anything.

## Go deeper

- [Install and run a first serve](../GETTING-STARTED.md)
- [Catalog, artifacts, and recipes](../MODEL-LIFECYCLE.md) — how recipes, caches, and model sources fit together
- [Serve recipes](../recipes/index.md) — every published recipe, with downloads
- [Host-supervised services](../HOST-SERVICES.md) — Docker, native, and launchd lifecycles
- [Read workload activity](../WORKLOAD-VISIBILITY.md)
- [Promote and roll back](../MODEL-PROMOTION.md) — the human-reviewed path from candidate to route
- Command reference: [serves](../cli/serves.md), [models and recipes](../cli/models.md)
