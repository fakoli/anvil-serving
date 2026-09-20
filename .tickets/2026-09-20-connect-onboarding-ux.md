# Connect onboarding: browser acceptance and upstream recovery

Status: In progress. Keep implementation, fixture validation and live acceptance separate.

## Implementation disposition

| Item | Disposition |
| --- | --- |
| Account/resource inspection | Existing pending CLI feature ported to the current declaration schema; read-only account lookup verified against a deployment. |
| Preview and SMTP request receipts | Implemented and covered by no-side-effect preview checks. |
| Readable setup/recovery email | Managed HTML/text templates implemented; pinned Authelia accepts the configuration; synthetic HTML rendered and inspected in a real browser. Shared reset subject/page terminology remains upstream. |
| Expired-session cached document | Reproduced in a browser; gateway cache prevention implemented with native tests. Live deployment and fresh-navigation acceptance remain pending. |
| Legacy username input | Native attributes added and tested. This is not the hosted Authelia field. |
| Hosted username capitalization | Confirmed missing native-input attributes in the deployed browser. Requires an upstream native-input fix/rebuilt provider; not resolved by notification templates. |
| Password-page focus and token recovery | Replacement-link guidance added to email. Original field issue and upstream consumed-token page behavior remain unresolved. |
| Post-enrollment service home | Existing declared destination retained; guidance distinguishes enrollment from authentication. Full password-manager journey remains to be validated. |
| Open WebUI interactive requests | Pinned implementation researched. Authenticated question/location reproduction remains open; no blanket proxy or permission workaround applied. |

The current changes are not a deployment receipt. Do not close this ticket until
the unresolved browser and upstream items have their own acceptance evidence.

## Reported journey

An operator creates an account, the developer follows an emailed link, creates a
password, signs in, verifies an emailed enrollment code, and registers a passkey
with a password manager. Authentication should finish at the service chooser.

Observed friction: reset terminology on first setup; long invitation links;
password fields unavailable during setup; refreshing a consumed link strands the
user; mobile username capitalization; an unconfirmed CLI preview mistaken for an
email send; return to the authentication page after passkey enrollment; missing
Open WebUI question and location prompts.

## Acceptance

- Installed `users list/show` can inspect the current deployment declaration,
  without exposing password hashes, factors or claiming groups are service grants.
- Preview receipts state no changes or email send occurred. Applied SMTP receipts
  distinguish an accepted reset request from verified mailbox delivery.
- Password mail has an understandable action link and accurate setup/recovery
  wording. An ordinary reset must not falsely claim a new invitation.
- Actual hosted username inputs preserve native password-manager autofill and
  disable capitalization, correction and spellcheck on the input itself.
- Setup validation errors are visible; consumed/expired links have a recovery
  path. Single-use tokens and ownership verification remain enforced.
- Passkey enrollment and authentication are distinguished; after authentication,
  onboarding reaches the authorized chooser and service deep links still work.
- Question prompts accept an answer or cancellation; location denial is explicit.
  Tests must distinguish a missing model tool call, unsupported tool integration,
  disconnected browser transport and a frontend rendering defect.

## Investigation evidence, 2026-09-20

- CLI account inspection was pending in source while the installed manager lacked
  it. The older checkout also rejected the newer `portal_host` declaration. Port
  the existing feature onto current source; do not weaken manifest validation.
- Missing reset email was explained by a preview without `--confirm`. A confirmed
  reset produced a provider delivery event. This does not establish deliverability
  for every request. Provider recipient strings can include display names; compare
  parsed addresses, not raw strings.
- Pinned Authelia is v4.39.20. Its reset finish POST consumes the verification
  record before password submission. Consumed and expired link errors were observed;
  the original unavailable-field symptom still requires a controlled reproduction.
- Live browser inspection found `autocomplete="username"` on the hosted username
  input but no `autocapitalize`, `autocorrect` or `spellcheck` attributes. A fix to
  the separate legacy Observatory input does not prove the hosted input is fixed.
- Open WebUI v0.11.3 opened its frontend error page when a cached app shell loaded
  with an expired Connect session. Subsequent protected configuration/asset requests
  returned 401. This is separate from the reported interactive-prompt symptom.

## Open WebUI research

Pinned upstream sources:

- [Built-in ask_user](https://github.com/open-webui/open-webui/blob/v0.11.3/backend/open_webui/tools/builtin.py)
  validates one to three questions and uses `request:user_input` with a browser
  callback. Absence of a callback returns an explicit error.
- [Chat event handling](https://github.com/open-webui/open-webui/blob/v0.11.3/src/lib/components/chat/Chat.svelte)
  handles socket prompts for the active chat/message and also restores persisted
  pending `ask_user` calls. Inspect both paths; a WebSocket hypothesis alone does
  not explain every missing prompt.
- [Question card](https://github.com/open-webui/open-webui/blob/v0.11.3/src/lib/components/chat/AskUserCard.svelte)
  implements answer/cancel and a timeout. Check the composer/card, not only modal
  dialogs, when reproducing the report.
- [Location helper](https://github.com/open-webui/open-webui/blob/v0.11.3/src/lib/utils/index.ts)
  calls browser geolocation. Browser permission state and the actual invoking
  feature must be identified before attributing a missing permission prompt to
  Connect. Do not grant real location access as a diagnostic shortcut.
- [External tool event limitations](https://docs.openwebui.com/features/extensibility/plugin/development/events/)
  explain why external tools cannot use interactive callbacks. This is a separate
  contract from the built-in `ask_user` implementation.

## Diagnostic tooling gap

The active WebUI container was not a named entry in the selected model-serve
manifest: `serves logs` reported no matching serve. Bounded read-only container
logs were used to inspect the owning component. A managed auxiliary-application
log path must be selected or added before making this the repeatable operator
procedure; do not invent a model serve or replace its lifecycle owner.

## Boundaries

Do not introduce password hints or security questions as reset authorization.
Do not disable authentication, enable public signup, expose real location, reuse
consumed tokens, or log setup links to make an acceptance test pass. Keep identities,
provider records, authentication state and operational evidence outside this public
ticket. Unresolved live browser gates stay open even when unit tests pass.
