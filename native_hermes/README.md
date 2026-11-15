# Native Hermes owner adapter

Candidate integration for a dedicated whole-process OpenShell instance. This
adapter translates the existing Native Agents owner protocol to Hermes's
canonical gateway. Hermes owns execution, sessions, durable admissions and native
memory. The adapter stores only broker identity mappings and dispatch receipts.
It never uses the standalone TUI gateway's different creation contract.

The runtime supplies `ANVIL_HERMES_CONFIG` with exactly `directory`, `storage`
(absolute private, disjoint paths) and `port` (loopback listener). A private bearer
token is generated in `storage/owner-token`. Deployment must bind the full process
tree, storage, scoped providers, memory and current Connect authority before
exposing a workspace. This module alone does not provision or qualify tenants.

Run the deterministic checks with:

```sh
python3 -m unittest discover -s native_hermes -q
```

Inside the pinned Hermes image, `check_http.py` exercises real HTTP/SSE with a
synthetic gateway. `check_gateway.py` requires a disposable `/sandbox` workspace
and the selected Holographic plugin at `/opt/anvil-plugins/holographic`; it creates
a private synthetic home and runs an actual Hermes terminal tool against a local
test model endpoint. Its file and durable-history oracles are independent of the
model response. These checks do not measure inference-model quality or capacity.

Current pilot limits: text-only prompts; 200 adapter sessions; 10,000 receipts;
14 ordinary HTTP operations and two reserved control operations; 4096 history rows,
50 per page, 64 KiB per rendered message. Large content fails explicitly, without
truncating history. The pinned canonical client has an 8 MiB unpaged snapshot
ceiling; the gateway does not currently honor `omit_messages` on canonical resume.
A dedicated home is required because the native 200-session listing is profile-wide.

Stop fences retries immediately and returns failure while a submitted operation
or native admission remains uncertain. A lost submit is never automatically
replayed, including after adapter restart. An operator may need to inspect native
receipts and stop the whole owner. Guest revocation therefore also needs a
controller outside the sandbox that terminates the entire owner process tree.

No guest deployment or entitlement is implied by these source checks.
