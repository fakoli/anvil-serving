# Controlled A/B assignment

Campaign: `2026-10-17-glm53-nvidia-fp4-ab`.

The user authorized an isolated A/B using NVIDIA's recipe with slight modifications. The campaign owner alone controls managed lifecycle, quiesce/readmission and benchmark dispatch. The baseline is the retained r11 deployment; the candidate is the pinned NVIDIA FP4 model/image in `configuration.json`.

Read-only scouts verify recipe fidelity, feasibility, workload contracts and restoration scope. An independent reviewer checks the operation before load and reviews the final evidence. The evidence reporter owns only explicitly assigned publication files. No agent uses the evaluated model as its own correctness oracle.

`run-plan.json` contains the deadline, configuration-search limit, resource boundaries and stop conditions. `workload-manifest.json` defines matched inputs and independent acceptance checks. Preserve every failed attempt and distinguish host admission, harness configuration and model behavior.

Advance only on complete required observations. Stop dispatch on a correctness/runtime failure, identity drift or deadline; cancel an owned active job through its suite command. Unload through the managed recipe, restore the exact retained incumbent and verify authenticated router operation before closing. A failed gate permits only a researched, versioned correction within the authorized budget.

Promotion and changes to unrelated routes, providers, host swap, processes or private operator work are outside this assignment. Final evidence must identify completed and deferred coverage and retain native producer schemas with explicit public redactions.
