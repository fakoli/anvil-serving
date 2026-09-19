# Bounded runtime source inspection for recipe feasibility

Status: open

The recipe status/log surfaces expose identity and memory observations but do
not expose a bounded way to inspect a pinned engine's APC compatibility guards.
The existing controller container-exec surface accepts only predeclared commands;
the installed binding also identifies an older container. Do not broaden it to
arbitrary execution. During the APC investigation, narrow read-only Docker
inspection of the exact recipe container is allowed to locate runtime source
and configuration validation. No lifecycle mutation uses this diagnostic path.

Proposed resolution: a bounded recipe diagnostic for selected engine capability
checks, with explicit immutable container identity and output limits. Retain
source findings in the campaign evidence; managed lifecycle remains mandatory.
