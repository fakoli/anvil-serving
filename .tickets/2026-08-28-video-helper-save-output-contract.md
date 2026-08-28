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

Pending graph/descriptor tests, repeated live validation and qualification,
and the full repository gate.
