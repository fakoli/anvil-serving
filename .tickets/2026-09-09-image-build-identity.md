# Managed image build identity for runtime qualification

Status: implemented; focused validation and live identity capture passed.

Runtime qualification needed the cached image platform and selected build
labels. `host docker-image` exposed only removal audit, while model-cache
inventory and recipe status did not expose these build fields. An operator
would otherwise need a raw Docker inspection.

Add `host docker-image inspect` for one immutable local image, with selected
labels, verified digest resolution, bounded evidence, and no lifecycle mutation.
Qualification must return to this managed command before candidate startup.

Follow-up hardening: the retained-result size check runs after subprocess output
capture. Replace capture with bounded streaming if a hard memory ceiling against
a malfunctioning Docker process becomes part of the tool contract. Current
documentation states this limit explicitly.
