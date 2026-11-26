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

## A walk through the glasses app

These screenshots come from one session in the MemoMind Plugin Studio
simulator, running against a live Scribe service. The app has three tabs:
Record, History and Profiles. The glasses display is 600x350.

### Record

Tap the mic, or press the glasses button, and speak. The transcript appears as
you talk, on the phone and on the glasses. Press again to stop and save, or
double-press to cancel, which saves nothing.

![The Record tab while recording: a red stop button, the 0:22 timer, and the live transcript of a planning update.](../assets/scribe/phone-recording.png)
![The glasses display while recording, showing "REC" and the same live transcript.](../assets/scribe/hud-recording.png)

Scribe saves the raw transcript first, exactly as you said it. Then pick a
profile, choose a model and tap Rewrite. Each rewrite is added below the
transcript with its own Copy button. The latest result also shows on the
glasses. A long result is split into pages with a footer such as "1/2 · hold:
next page". Holding the glasses button shows the next page; when no result is
showing, it brings back the latest one.

![The saved memo: the 0:27 recording, the raw transcript, and the eight rewrite profiles.](../assets/scribe/phone-saved.png)
![The rewrite panel with the model picker and two results, an email and a technical document.](../assets/scribe/phone-rewrite.png)

![Page 1 of the email rewrite on the glasses: "Subject: Docs Site Shipping Update" and the opening of the message, with the footer "1/2 · hold: next page".](../assets/scribe/hud-email.png)
![Page 2 of the email: the end of the message and "Best,", with the footer "2/2 · hold: back to start".](../assets/scribe/hud-email-page2.png)

The same memo, rewritten by each built-in profile. Each image shows the first
page:

![Eight glasses displays, one per built-in profile: Email (page 1 of 2), Technical doc (page 1 of 9), Clean note, Message, Meeting minutes, Task list, Say it better and Summary.](../assets/scribe/hud-profiles.png)

### History

Every recording is kept with its length and rewrite count. Tap one to see the
original transcript and every rewrite made from it.

![The History tab with the planning memo expanded: the original transcript, then the email rewrite.](../assets/scribe/phone-history.png)

### Profiles

The eight built-in profiles are read-only. Open one to read its prompt, or
duplicate it as a custom profile and adjust it. "New profile" takes a name, a
description, a prompt and a default model.

![The Profiles tab listing the eight built-in profiles, each with a one-line description.](../assets/scribe/phone-profiles.png)
![The read-only view of the built-in Technical doc profile, showing its prompt and a "Duplicate as custom" button.](../assets/scribe/phone-builtin.png)
![A new custom profile, "Standup update", with a prompt asking for Yesterday, Today and Blockers headings.](../assets/scribe/phone-new-profile.png)

A saved custom profile appears next to the built-ins as a rewrite option:

![The glasses display showing the memo rewritten by the custom Standup update profile, with Yesterday and Today sections and the footer "1/2 · hold: next page".](../assets/scribe/hud-custom-profile.png)

## Go deeper

- Command reference: [scribe](../cli/scribe.md)
- [ADR-0048](../adr/0048-anvil-scribe-record-first-dictation.md): why Scribe is a gateway client
- The gateway's audio grants: [router keys](../cli/router.md)
