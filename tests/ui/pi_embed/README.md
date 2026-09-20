# Pinned Pi Web integration fixture

Run `python3 tests/ui/pi_embed/run.py /absolute/new-evidence-directory` with
`PI_WEB_PACKAGE` pointing to the installed `@agegr/pi-web` 0.9.0 package.
Supply the dev-only browser tooling through `PLAYWRIGHT_MODULE` and
`CHROMIUM_EXECUTABLE`, as for the other UI fixtures. Node and Go are required.

The fixture copies the installed app, uses an empty Pi home and synthetic model
provider, and frames it through the production Connect browser/session/transport
seams. It checks retained native sessions, extension confirmation, origin checks,
and foreign, revoked and expired identities. The Pi resource grant belongs only to
the OS owner: the separately registered dashboard operator has no Pi grant.
Sharing that grant would share all host sessions and tool authority. The fixture
hashes the complete installed dependency tree before and after execution, and
checks active streaming and pending confirmation across reconnects. Model
requests never leave loopback.
The parent is a minimal integration fixture; this does not certify the shipped
Workbench shell, production edge configuration, or managed-task capabilities.
Screenshots and logs must stay outside the public repository.

Set `PI_FIXTURE_WORKBENCH=1` to exercise the shipped Playground shell instead
of the minimal parent. Its catalog/session responses are deterministic fixtures;
the child still uses real Connect and pinned Pi Web. The real Workbench HTTP
owner-subject/resource checks are separately covered by `test_host_pi.py`.
This mode also checks retained Model test drafts and an unavailable host owner.
