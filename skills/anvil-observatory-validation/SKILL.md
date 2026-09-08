---
name: anvil-observatory-validation
description: Validate an installed Anvil Observatory console, its authentication boundary, packaged assets, and read-only data coverage with a bounded evidence receipt.
---

Use this after a console build or deployment. The helper logs in through the
console's configured identity provider, reads the fleet and operation catalog,
checks packaged modules and rejected unauthenticated requests, then logs out.
It never previews or dispatches an owner action.

```sh
python3 scripts/check_console.py --url https://console.example.test/observatory/ \
  --username operator --password-file /private/console-password \
  --output /private/evidence/console-check.json
```

Resolve script paths relative to this skill. Use only the explicitly authorized
password file; do not discover or source a shared environment file. The receipt
contains counts, statuses and timings, never cookies, passwords or CSRF tokens.
TLS verification stays enabled. An HTTP loopback fixture is accepted for tests.

A passing receipt proves the recorded reads and boundary checks. It does not
prove live lifecycle/configuration/experiment acceptance, screen-reader use,
another device, or recovery. Record those independently through the typed
owner-backed UI and its retained operation evidence. Keep real URLs, receipts,
screenshots and operator configuration outside the public product repository.
