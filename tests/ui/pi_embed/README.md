# Pinned Pi Web integration fixture

Run `python3 tests/ui/pi_embed/run.py /absolute/new-evidence-directory` with
`PI_WEB_PACKAGE` pointing to the installed `@agegr/pi-web` 0.9.0 package.
Supply the dev-only browser tooling through `PLAYWRIGHT_MODULE` and
`CHROMIUM_EXECUTABLE`, as for the other UI fixtures. Node and Go are required.

The fixture copies the installed app, uses an empty Pi home and synthetic model
provider, and frames it through the production Connect browser/session/transport
seams. It checks retained native sessions, extension confirmation, origin checks,
and foreign, revoked and expired identities. Model requests never leave loopback.
The parent is a minimal integration fixture; this does not certify the shipped
Workbench shell, production edge configuration, or managed-task capabilities.
Screenshots and logs must stay outside the public repository.
