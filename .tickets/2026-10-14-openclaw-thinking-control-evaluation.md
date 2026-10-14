# Preserve routed OpenClaw thinking controls in evaluation

Status: open.

Routed OpenClaw thinking-off failed twice with reasoning visible, while high
passed 9 of 9 and max was rejected locally before query. Unified evaluation
hardcodes thinking-off, so it cannot be reported as blanket green. The
failure's exact cause remains unproven without a retained wire capture.

Make the evaluation request declare the selected thinking control and verify
the route preserves it, or reject unsupported controls before query. Keep
thinking-off, high, and local validation outcomes separate; do not change
defaults, providers, routes, or sampler settings as part of this fix.

Acceptance: focused request fixtures cover off, high, max rejection, and an
unsupported control; route metadata and outbound request agree; and the
unified evaluator reports the selected control and marks the aggregate
incomplete when a required control has no passing evidence.
