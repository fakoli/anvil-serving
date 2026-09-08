# Observatory browser checks

These are isolated development fixtures. They never call a real resource owner
or inference endpoint. The production shell contains no fixture fallback.

Supply a dev-only Playwright installation and Chromium executable, then run:

```sh
PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
CHROMIUM_EXECUTABLE=/absolute/path/to/chromium \
node tests/ui/observatory/browser.cjs /absolute/path/to/test-evidence

OBSERVATORY_TEST_PYTHON=/absolute/path/to/dev-python \
PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
CHROMIUM_EXECUTABLE=/absolute/path/to/chromium \
node tests/ui/observatory/facade_browser.cjs /absolute/path/to/facade-evidence

PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
CHROMIUM_EXECUTABLE=/absolute/path/to/chromium \
node tests/ui/observatory/security_browser.cjs /absolute/path/to/security-evidence
```

The Python environment must have the product and its existing development test
dependencies installed. The second harness starts the actual dashboard server,
Console, Access, and IntentStore with the independent FakeOwner from the Python
operation regression tests. Its journal is temporary. It verifies authentication,
private draft validation, exact impact, one accepted owner mutation, independent
observed state, secure cookie attributes, and refresh without replay.
It then exercises another complete configuration journey using only Tab, typing,
and Enter, including explicit confirmation and a verified result. The confirmation
dialog and its primary control are checked at 320px. Each journey accepts exactly
one owner mutation, for two independent synthetic operations in total.

The security harness injects hostile labels, logs, errors and evidence into all
eight screens and the retained evidence dialog. It asserts literal text, no
executable injected nodes, no canary request, and no script execution. A hostile
canonical workload label is rejected by the existing closed schema. It also
checks an unavailable owner's action explanation through the actual Chromium AX
description and visible linked text. Expiring the synthetic session with a preview
open accepts no operation, closes the modal, clears the private draft, and requires
authentication, validation and a new preview before another confirmation.

The first harness covers all eight screens, a root deployment and a nested base
path, canonical workload omissions and invalid evidence, current versus historical
sources, missing TTFT, null chart gaps, data alternatives, proposed versus observed
configuration, draft survival, numeric constraints, viewer access, exact previews,
modal focus, refresh recovery, verification failure, inconsistent success claims,
and ambiguous transport. A transport retry with the same key must not create a
second accepted owner operation; the fixture models durable deduplication.
A delayed response for a previously selected host must never replace the chart
for the newly selected host.
A synthetic document visibility event verifies that the refresh handler pauses
scheduled reads and resumes current evidence. This is explicitly separate from
native background-tab visibility behavior measured by the live harness.

Screenshots cover every screen at 1440×900 and 390×844, plus 320px reflow,
768×1024, 1920×1080, 2560×1080, reduced motion, and forced colors. Mobile
navigation is inert while closed and traps focus while open. A 720×450 viewport
checks reflow equivalent to 200% zoom of a 1440×900 viewport; this is not a claim
that native browser zoom or a screen-reader pairing was tested. Real deployment
and owner acceptance evidence must be recorded separately.

Validation used Playwright 1.62.1 and Chrome 152.0.7977.82. These remain development
tools, outside the Python runtime and wheel dependencies. The new source files
are formatted with Prettier 3.6.2; the legacy shell and workload script are intact.

`live_browser.cjs` accepts an exact HTTPS deployment base URL and an evidence
directory. It requires explicit authorization for a read-only deployment and
in-process credentials supplied through `OBSERVATORY_USERNAME` and
`OBSERVATORY_PASSWORD_FILE`. It asserts read-only mode before traversing all
eight screens, enumerating the approved historical chart catalog, and capturing
desktop/mobile screenshots and accessibility snapshots. It records the installed
build and asset hashes, but never credentials or a cookie value.

`performance_browser.cjs` uses the same arguments and credentials for one bounded
idle browser. It samples 25-second open and closed windows, and a hidden window
only when switching to another real tab actually makes `document.hidden` true.
An unavailable headless visibility transition is reported as a limitation.
Optional `OBSERVATORY_WEB_CONTAINER` names the exact container for read-only
Docker CPU/memory samples. It also records per-file raw/gzip asset sizes for the
installed build and current source. Run this harness by itself, without other
browser tests, after the deployment is stable. It never sends model requests.
