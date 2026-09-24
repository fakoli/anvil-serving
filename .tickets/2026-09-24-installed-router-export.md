# Installed router configuration export

Whole-home operator inventory correctly refuses symlinks, including a credential
link unrelated to a selected router file. This blocks exact promotion baselines
without a narrower installed-artifact operation. Keep the whole-home guard.

Add `router export-config`: resolve the exact running router's read-only bind,
require an expected SHA-256, validate a bounded regular source without following
links, apply existing secret checks, reject dependency bundles, and require the
source bytes to match the installed file. This command is read-only and grants
no route change authority. Use existing preview/confirmed install lifecycle.

Related operational gaps retained: missing Serving MCP wrappers in the client;
controller DNS transport refusal and Windows Python certificate-chain mismatch;
unregistered Compose-owned UI services omitted by model-only discovery.
