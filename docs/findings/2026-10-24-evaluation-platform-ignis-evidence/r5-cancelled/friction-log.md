# Cancelled-attempt friction

The first Aider child logged `campaign_watchdog_unavailable` before its preflight or model request while the durable job still reported running. Managed cancellation terminated the worker and produced a cancelled child and coordinator. The native report's zero failed jobs must be read with that failure history; cancellation is not a successful harness run.

The [reviewed diagnostic](failure-diagnostic.json) retains the bounded offline reproduction: child verification succeeded in 2.544058426981792 seconds against a 2-second acknowledgement window. Original supervisor stderr was discarded, so an additional contemporaneous exception is not excluded. This reproduction supports a startup-window explanation; it is not a complete historical stack trace.

Cancellation did not supply the endpoint credential needed for activation recheck. The cancelled batch therefore retains unknown engine drain, incomplete telemetry and blocked campaign closure. The underlying activation-recheck error was not retained. The six absent canonical Aider outputs remain explicitly missing in the projection ledger. The separate later zero observation is operational context only.

No candidate load, incumbent unload, alias activation or model swap occurred. Later repairs and retries are separate evidence and cannot rewrite this attempt. No serving-comparison ranking, quality conclusion or promotion follows.
