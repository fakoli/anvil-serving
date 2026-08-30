# OpenClaw harness restart does not resolve the service executable path

**Status:** Open

## Problem

`anvil-serving harness restart openclaw --confirm` reaches the managed restart
adapter but fails with `No such file or directory: 'openclaw'` when the
OpenClaw executable is installed through the service's Node/npm environment
rather than the non-interactive shell PATH.

This blocks the product-owned lifecycle surface even though the existing
launchd job can be restarted safely and the gateway is otherwise healthy.

## Evidence

The 2026-08-30 GLM-5.3-Flash promotion reproduced the defect on the model-free
Mini client host. A bounded restart of the exact installed launchd label then
succeeded, OpenClaw health returned `ok`, and the running-gateway dynamic-image
acceptance turn passed. The workaround proves the service was valid; it does
not make the managed command functional.

## Required behavior

1. Resolve the executable from the installed service definition or the
   service's declared Node/npm PATH instead of assuming the caller's PATH.
2. Keep the existing bounded confirmation, host selection, and health-check
   contract.
3. Do not discover or restart an unrelated executable with the same basename.
4. Return the exact service label, resolved executable identity, restart
   outcome, and post-restart health without exposing credentials.

## Acceptance

- Add hermetic coverage for an OpenClaw executable absent from the caller PATH
  but present in the service environment.
- Add a negative test for an unresolved or mismatched service executable.
- Pass the existing harness restart, CLI reference, Ruff, and full test gates.
- Run one bounded live regression through the managed command on Mini.
