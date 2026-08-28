# Wan2.2 graph must satisfy the pinned VideoHelperSuite contract

**Observed:** 2026-08-28

## Problem

The exact Wan2.2 qualification passed worker, node, model, and feature
compatibility but ComfyUI rejected the graph before execution. Managed logs
identified the missing required `save_output` input on the pinned
`VHS_VideoCombine` node.

## Resolution

Set the immutable graph input to `true`, recompute the canonical graph digest,
and synchronize the source and packaged workflow bundles. The field is not a
caller parameter and does not expand the public request surface.

## Verification

- Ten focused graph, descriptor, packaging, and video integration tests passed.
- Repeated live validation reported no missing feature, node, or model.
- The corrected workflow produced a decodable 17-frame H.264 MP4 from a clean
  worker baseline.
- The full repository gate remains part of the enclosing change.
