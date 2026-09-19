# Native Metal memory probe

The bounded Mac probe tests shared Metal allocation accounting without loading
model weights. It compiles a packaged, SHA-256-recorded Objective-C source with
Xcode's selected `xcrun clang`, then runs one unprivileged child. Foundation and
Metal are system frameworks; the Python package gains no runtime dependency.

```bash
anvil-serving host native-memory-probe --dry-run
anvil-serving host native-memory-probe --output /private/evidence/metal-memory.json --confirm
```

Preview executes no processes. Execution touches exactly 32 MiB in one shared
Metal buffer, completes a GPU blit into it, captures the child's physical footprint and device allocation
before and after, records the footprint peak, and measures host swap before and after. These endpoint samples do not prove swap stayed zero between samples. Compilation has a
60-second timeout; the child has a 10-second timeout. On timeout, the process group is terminated and the direct child is reaped.
The temporary source and binary are removed when the operation exits. Retain output privately because
platform evidence belongs to the operator.

The child also requests a 2048 MiB physical-footprint limit on **itself** using
`task_set_phys_footprint_limit`. No elevation is requested. Root execution is
refused before any subprocess and again by the child. Permission denial is expected; unexpected success requires
review and never authorizes a larger trial. The child exits regardless, leaving
no persistent limit, service, route, or system policy change.

`accounting_observed` requires independently checked footprint and Metal-device
increases of at least the buffer size, expected limit-permission denial, and
unchanged zero swap. Missing measurements, a timeout, nonzero initial swap,
swap changes, and unexpected permissions do not pass. A successful small probe
is **not proof of a fatal memory ceiling, loader fit, zero-swap behavior at model
scale, or model qualification**. `hard_memory_containment_proven` and
`target_model_trial_authorized` remain false in every result.

Observe protected service health and process identities separately before and
after the probe. Their preservation is a campaign gate, not inferred from this
child's successful exit. macOS virtual-address limits and Metal recommended
working-set sizes must not be presented as hard physical-memory containment.

See the [Apple feasibility finding](../findings/2026-09-19-swift-qwen38-apple-feasibility.md) for the current trial boundary.
