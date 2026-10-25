# Admission refusal and disposition

The managed resource gate refused candidate creation because the configured 52 GiB RAM limit plus 16 GiB host reserve required 68 GiB available RAM. The exact error and later memory observation are retained in [admission-refusal.json](admission-refusal.json). The candidate made zero requests and no alias transition occurred. Managed incumbent reload was still in progress at this evidence cutoff.

Disposition: preserve this frozen 48-cell attempt as unexecuted. A separately reviewed successor draft changes only RAM and RAM+swap limits to 50176 MiB (49 GiB, no swap), retaining the 16384 MiB reserve. Its inherited `serve.note` still says 52 GiB; that historical prose does not override the successor’s actual bound fields. This packet records no successor admission or execution. The successor is not a repair of this attempt; refusal, OOM, containment, readiness or preflight failure remains terminal for that one variant.

R2 after-state: exact incumbent reload completed successfully, with the original image/model/recipe/GPU order/port restored and a new container/monitoring generation. Bounded routed smoke, JSON and four tool requests passed (6/6). This closes interim recovery only; final long-context and Pi acceptance remain pending. See [interim evidence](interim-restoration-evidence.json).
