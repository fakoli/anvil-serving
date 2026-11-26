# Anvil Scribe

Anvil Scribe is record-first dictation. It saves the raw transcript of every
recording, then rewrites it only when you ask, with a saved profile on a model
you choose, and keeps the raw text and every rewrite in a private history. It
consumes Anvil Voice speech-to-text and the gateway's chat aliases through one
route-bound key.

<!-- product-journey:anvil-scribe -->

!!! info "Coming soon on MemoMind glasses"
    The MemoMind web plugin for this family is not published yet. See
    [MemoMind glasses](../MEMOMIND-GLASSES.md) for how to try it today.

## When to use it

- You dictate from a phone, glasses or browser and want the words kept exactly.
- You want the same memo as an email, a task list or minutes, on demand.
- Recordings and transcripts must stay on your own hosts and out of evaluation.

## Go deeper

- Command reference: [scribe](../cli/scribe.md)
- [ADR-0048](../adr/0048-anvil-scribe-record-first-dictation.md): why Scribe is a gateway client
- The gateway's audio grants: [router keys](../cli/router.md)
