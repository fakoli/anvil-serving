# Sign in from an SSH terminal

`anvil-connect login` lets a terminal obtain a short-lived API credential after
approval in a browser on another device. The terminal needs outbound HTTPS and
the standalone Connect client. It needs neither a browser nor an inbound
callback listener during sign-in.

## Use the client

Obtain an approved client declaration that includes `device_authorization` and
provide its `local_key_env` variable through your private secret mechanism.
Each loopback HTTP request requires that local caller key. This is not a
same-user process-isolation boundary: a compromised process running as your
user may be able to read your files, environment or process memory.
The remote credential is obtained during login; you do not need to supply the
declaration's `remote_key_env` variable for this command.

```sh
anvil-connect validate --mode client --config /absolute/client.json
anvil-connect login --config /absolute/client.json
```

1. Open the displayed verification URL in a browser you trust. It can be on
   your laptop or phone while Connect runs in an SSH session.
2. Sign in with your configured passkey or other permitted login method.
3. Enter the short code displayed by your terminal. Check the requested API,
   methods and configured application label, then approve or deny the request.
4. Connect receives the result by HTTPS polling. After approval it opens the
   loopback HTTP port declared in `client.json` and remains in the foreground.
5. Point your SDK at that local `/v1` base URL and use the local caller key as
   its API key. Stop Connect with Ctrl+C when finished.

The local port belongs to the machine **running Connect**. Connect started
inside SSH on a server listens on that server's `127.0.0.1`, not your laptop's.
Run it on the laptop when the SDK runs there. The remote side uses HTTPS even
though the SDK-to-Connect connection uses loopback HTTP.

The public API request identifies its HTTPS client as `anvil-connect/1`.
The approval protocol identifies itself as `anvil-connect-device-login/1`.
Neither presents itself as a browser, and both retain verified TLS and the
declared authorization checks.

No email code or server-side verification file is needed for this approval.
Those may still be part of initial identity-provider enrollment or account
recovery. The passkey stays with your browser's credential provider, such as
1Password; Connect does not receive the passkey or biometric data.

## Declare the permission boundary

The gateway's `device_authorizations` list binds one existing browser resource
to one existing API resource with an explicit human-to-API-principal mapping:

```json
{
  "browser_resource": "dashboard",
  "api_resource": "router",
  "methods": ["GET", "POST"],
  "label": "Router access from a terminal",
  "principals": {"human:OPAQUE_CONNECT_PRINCIPAL": "existing-api-principal"}
}
```

Use actual opaque IDs only in private operator configuration. This binding is
inside `gateway.gateway.device_authorizations` in the managed deployment. Both
resources and their grants must already exist; declaring a mapping does not
create a user, grant new methods, or bypass disabled principals.

The managed renderer derives the client block from that same binding:

```json
{
  "device_authorization": {
    "browser_host": "dash.example.test",
    "approval_path": "/observatory/_anvil-connect/device",
    "api_resource": "router",
    "methods": ["GET", "POST"]
  }
}
```

This block accompanies the client's existing schema, rule, listener and
environment references. The gateway derives the approval path from the browser
resource's path prefix. The CLI cannot request an arbitrary host, callback,
resource or wider scope. The first version requires unique browser and API
resource bindings; omission keeps the existing manually issued API-key flow.

## Lifetime and recovery

Approval expires after ten minutes. Connect polls at a bounded interval and
honors server throttling. A successful approval produces one API credential
with a maximum lifetime of one hour, capped by the approving browser session.
It is kept only in process memory. There is no refresh token or persisted
remote credential in this version; start `login` again after expiry or exit.

The short user code identifies the request; a separate high-entropy device
secret proves possession during polling. Neither is placed in the verification
URL. The browser requires an authenticated session, same-origin POST and CSRF
protection for approval. Confirm only a code from a terminal request you
initiated; the configured label does not attest to the requesting machine.

Redemption is single-use. If the successful response is lost, begin a new
login. It does not issue another credential on retry. Disabling or changing
the relevant identity or grants, revoking the credential, or logging out of
Connect invalidates the applicable access checks and closes admitted streams.
Revocation cannot promise to stop work already executing inside a model.

This follows the user experience described by
[OAuth's device authorization grant](https://www.rfc-editor.org/rfc/rfc8628.html),
with Connect-owned, fixed-resource approval endpoints. It does not require or
claim that Authelia implements the OAuth device grant.
