# Router status omits the published bind address

## Status

Open; the remote benchmark smoke used a bounded read-only Docker inspection to
diagnose the gap.

## Symptom

`anvil-serving router status --json` reported the router container as running
and healthy while a tailnet worker could not connect. Managed router logs also
showed a healthy front door. Neither surface reported that Docker had
published port 8000 only on `127.0.0.1`.

## Impact

An operator cannot distinguish a healthy loopback-only router from a healthy
router published on the declared remote endpoint. Remote benchmark preflight
therefore fails as a generic connection error, and root-cause diagnosis falls
outside the managed product surface.

## Proposed fix

- Include the effective published host address and port in human and JSON
  router status.
- Compare that binding with the configured topology endpoint when one exists.
- Report a clear mismatch without mutating or recreating the router.
- Add unit coverage for loopback-only, tailnet-published, and absent port
  bindings without requiring a live Docker daemon.

## Acceptance

The remote-worker failure can be diagnosed from `router status --json` and
managed logs alone. Raw Docker inspection is no longer necessary.
