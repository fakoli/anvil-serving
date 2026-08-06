# Canonical CLI drops local serves-mode confirmation

**Observed:** 2026-08-01

Status: fixed locally on 2026-08-06. `HandlerRef.forward_confirm_flag` is the
explicit handler-declaration contract; only `serves mode enter` and
`serves mode leave` declare it, and the dispatcher restores exactly one
`--confirm` in argv before any literal `--` separator. Acceptance evidence:
root-CLI tests prove enter/leave receive exactly one `--confirm` while
preview/status receive none, JSON mode still refuses without prompting, the
guarded-dispatch contract test encodes the declaration, and a hermetic
canonical-CLI test enters and leaves a synthetic exclusive mode with
`confirm=True` reaching the leaf parser on both transitions. Remote/controller
dispatch is untouched (it returns before the forwarding point). Ruff and the
full pytest suite pass on Windows.

## Problem

`python -m anvil_serving.cli serves mode enter TARGET --restore-group GROUP
--confirm` accepts and consumes the canonical dispatcher's `--confirm`, but the
local `anvil_serving.serves` handler does not receive that flag. After spending
time building the live exclusive-mode plan, the handler refuses safely with:

```text
mode transition not applied; rerun with --confirm
```

The same underlying managed command invoked as `python -m anvil_serving.serves
mode enter ... --confirm` receives the flag correctly. No container was created
or stopped during either failed canonical invocation.

## Impact

- Local exclusive TP2 mode cannot be entered or left through the documented
  canonical dispatcher.
- The failure appears only after the full live-state scan, adding roughly two
  minutes per attempt on the current large manifest set.
- Controller dispatch remains a distinct path and must not be changed by a local
  forwarding fix.

## Proposed resolution

Add an explicit handler-declaration contract for legacy local handlers that need
the consumed confirmation flag forwarded in argv. Enable it for `serves mode
enter` and `serves mode leave`; do not append `--confirm` generically to handlers
that use `confirmation_scope` instead of an argparse option.

## Acceptance

- A focused root-CLI test proves local enter/leave receive one `--confirm`.
- Preview/status receive none.
- JSON mode still never prompts and still requires explicit confirmation.
- Remote/controller arguments and idempotency behavior remain unchanged.
- The documented canonical command enters and leaves a synthetic exclusive mode
  in the existing hermetic serves tests.
