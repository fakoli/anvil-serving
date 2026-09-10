# Workbench browser validation

This fixture runs the real Console, authentication, CSRF checks, operation journal,
Workbench preferences, project readers, and Playground streaming. Its owners and
models are deterministic fixtures. It never contacts production controllers,
Anvil state, accounts, or inference endpoints. The application displays **Isolated
fixture**, including before sign-in.

Start the fixture with the development Python environment:

```sh
python tests/ui/workbench/real_fixture.py /absolute/path/to/test-evidence
```

The first JSON line gives its loopback URL and fixture-only sign-in. Send
`{"command":"status"}` on stdin to read owner/model invocation counts; send
`{"command":"stop"}` to close it. Closing stdin also cleans up. OpenSSL generates
an ephemeral certificate for the loopback model fixture; only that exact fixture
certificate is trusted by the injected model client. Normal TLS hostname and
certificate verification remain enabled. Assets are frozen at startup, so restart
the fixture after editing a view.

`browser.cjs` is a reusable Playwright regression journey following the existing
Observatory test convention: `OBSERVATORY_TEST_PYTHON`, `PLAYWRIGHT_MODULE`, and
`CHROMIUM_EXECUTABLE` select development tools, then run:

```sh
node tests/ui/workbench/browser.cjs /absolute/path/to/test-evidence
```

It checks boot, an empty Workbench's Run flow, owner preview, one verified
operation, reload without replay, retained evidence, server preferences, dependent
Pi model choices, keyboard tabs, mobile navigation, and 1440/390px screenshots.
The model and project fixture seams are also available for the wider product
journeys. Browser fixture data does not establish production readiness.

The author additionally exercised these flows through the CUA browser, compared
production screenshots with approved Concept03, and corrected the mobile header
and keyboard tab behavior. The standalone Playwright harness is source for the
independent regression pass; it was not run as part of that CUA validation.

Run `pytest tests/workbench/test_javascript_modules.py` to parse every shipped
JavaScript file explicitly as ESM. This catches a syntax error in any eagerly
imported view before it can prevent the whole application from booting.
