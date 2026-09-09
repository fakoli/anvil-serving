# Observatory migration through Anvil Connect

The migration command produces a reviewable plan for an existing Observatory
canonical origin and one declared browser resource. It does not edit the
application, rewrite HTML, change DNS, stop Tailscale or activate services.

```sh
anvil-serving connect migration --manifest /etc/anvil-connect/deployment.json --observatory-config /etc/anvil-observatory/config.json --resource dashboard --json
```

## Preconditions

The selected rule must use browser access and native authentication passthrough.
Its HTTPS hostname must equal Observatory's existing canonical origin. Its
canonical path prefix must exactly map the application's base path, without
prefix stripping. GET and POST must be admitted. The matching connector envelope
names one fixed loopback HTTP origin and independently enforces the same rule.

A hostname change requires an explicit application-origin and re-login migration;
the preview refuses to promise unchanged cookies at two origins. User-supplied
proxy identity headers never become native application authorization. Native
login, session, CSRF, resource/action grants and controller credentials retain
their existing authority.

## Proposed cutover

1. Qualify the application-side session, origin, CSRF and action controls at the
   chosen canonical origin.
2. Render and validate the gateway and selected connector configuration with the
   pinned native and edge binaries and the dedicated service identity.
3. Initialize the gateway, provision exact human issuer/subject grants, redeem
   an expiring connector invitation and independently approve its fingerprint.
4. Start the managed edge/gateway, then the selected connector. Confirm explicit
   origin readiness and the relevant browser/API negative controls.
5. Change the routing for that one canonical origin to the Connect edge. Retire
   the prior direct public route before claiming the outer gate is required.
6. Repeat denied-origin, session, CSRF/action, logout and stream-cancellation
   probes against the selected external deployment before calling migration done.

The command's output specifies the fixed origin and ordered actions. A concrete
deployment still needs an external host, DNS/certificate arrangement, operator
secret references, exact private origin binding and cutover window. None is
inferred from a currently open browser tab or an old infrastructure note.

## Rollback

Restore the prior routing for the same canonical origin, then stop only the
selected Connect role and restore the prior verified public configuration.
This restores the previous access boundary, so its native authorization must
still pass the recorded controls. Preserve Observatory session state, action
grants and credentials, as well as all current Connect revocations.

Configuration rollback must never restore an older authority database. Disaster
recovery uses the separate fenced `connect restore` operation, followed by
explicit grant review and connector reenrollment.

## Evidence boundary

The app-side migration fixture keeps the real Observatory Console/session alive
while changing a test front's fixed forwarding route and rolling it back. It
checks native authorization failures in each phase. It does not stand in for
the separate Connect browser/transport gate, public DNS operation, or a real
systemd migration. The [implementation record](ANVIL-CONNECT-IMPLEMENTATION.md)
tracks those qualification levels separately.
