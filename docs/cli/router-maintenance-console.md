# Local router maintenance console

Run a prepared, protected cutover plan from a POSIX terminal:

```bash
anvil-serving router maintenance-console --config /protected/router-cutover.json
```

The console displays a fresh native preview for STOP and a separate preview for
OPEN. For each action, review its container, configuration, expiry, and protected
preview file, then type **YES** and press Enter. Each acknowledgement accepts
that remote memory completion remains **UNKNOWN**; reopening is not proof that
remote work drained. The native 60-second preview limit and ownership guards
remain in force. Piped input and answers queued before a preview are rejected.

Installation must prepare an owner-only plan and the existing native STOP
configuration, including its authorized successor. The console does not select
a model or grant maintenance authority. Its plan has exactly these fields:

```json
{
  "schema": "router-maintenance-console/v1",
  "stop_config": "/protected/current-stop.json",
  "state_dir": "/protected/router-cutover-state"
}
```

All paths must be absolute. Keep the plan, authorization, and operation records
outside Git. See [acknowledged router maintenance](router.md#acknowledged-router-maintenance)
for the native configuration and authorization contract.

An expired or declined preview leaves the operation held. If STOP completed but
OPEN was never attempted, rerunning the same command verifies the retained STOP
receipt and starts only its bound successor before requesting a fresh OPEN
acknowledgement. An uncertain STOP attempt or any attempted OPEN requires
independent reconciliation; the console refuses to replay it or infer success
from a saved result. Preserve the protected state directory for diagnosis.

Successful OPEN preserves individual tier holds. Verify live state and use the
existing managed tier-readmission operation separately when authorized. This
command does not change model servers or GPU placement.
