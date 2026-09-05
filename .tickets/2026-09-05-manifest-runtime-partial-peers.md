# Preserve valid manifest runtime peers when Compose inspection fails

Status: open; producer consistency correction identified during projection.

manifest_workloads.py::capture_manifest_workload_snapshot can construct an
UNAVAILABLE runtime component containing valid unsupported native/generic
observations when the Compose capture fails or yields no valid rows. The
canonical component contract requires unavailable components to have no rows.
The strict T011 projector therefore rejects that component instead of retaining
the trustworthy unsupported peers.

Add T011.2: return PARTIAL with a fixed unavailable error and unknown omission
when valid peer rows survive, reserving UNAVAILABLE for an empty failed source.
Exercise completed nonzero capture, thrown capture exceptions, and malformed
or empty Compose output with a native peer; prove valid peers survive the
canonical projection without becoming running, healthy, or idle.

Keep this correction within the producer and its hermetic tests. No Docker or
live workload operation is required.
