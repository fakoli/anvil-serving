# Resume a started current-stop successor

During a 2.0.1 image-only upgrade, the completed current-stop receipt authorized
startup and the successor became healthy and durably quiesced. Host custody
finalization returned a generic HOLD. The predecessor proof had transferred,
but the successor had no pending or finalized container anchor.

The original exception was not retained. Subsequent read-only host, native
producer, live-run, proposal-validation and directory-custody checks passed.
An isolated Docker pending-retry qualification passed on the pre-resume base;
it did not exercise this command-level resume path. The
original failure is not yet reproduced; do not label it a lock deadlock or
claim that its root cause has been fixed.

The operational defect is deterministic: retrying `router up` with the completed
receipt requires the stopped predecessor, which startup has already replaced.
Add a custody-only resume for the exact running successor, preserving the
receipt, predecessor death and transfer proof, current physical run, closed
generation, configuration, environment, image and storage bindings. Resume must
not call Compose activation, stop/recreate a container, or open admission.

Validate absent, pending and already-finalized successor records, including
host-observation failure and changed-identity refusals. Preserve all accounting
and the separate acknowledged readmission gate. Report bounded stage names on
failure without returning raw child stderr or configuration secrets.
