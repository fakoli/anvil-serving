"use strict";
// Isolated adversarial metadata and accessible-action checks. No live owner.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const { createFixture } = require("./fixture_server.cjs");
const output = path.resolve(
  process.argv[2] || "test-results/observatory-security",
);
fs.mkdirSync(output, { recursive: true });
const canary =
  '<img src="/__observatory_canary" onerror="window.__xss=1"><script>window.__xss=2</script>';
const reason = "This declared owner does not support the requested action.";
(async () => {
  const fixture = await createFixture();
  fixture.state.authenticated = true;
  fixture.state.operations.push({
    id: "operation-canary",
    resource_id: "serve-a",
    host_id: "host-fixture-a",
    action_id: "configuration.apply",
    label: "Synthetic operation",
    status: "failed",
    native_state: "failed",
    actor: "fixture",
    submitted_at: "2026-09-08T12:00:00Z",
    updated_at: "2026-09-08T12:00:00Z",
    verification: { status: "failed", message: "Fixture verification" },
    recovery: { status: "not_attempted", message: "Fixture recovery" },
    events: [],
  });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
    headless: true,
    args: ["--disable-gpu"],
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  let mode = "hostile";
  const report = {
    fixture: true,
    observed_at: new Date().toISOString(),
    status: "running",
    hostile_views: [],
    canary_requests: [],
    dialogs: [],
    accessibility: {},
    session_expiry: {},
  };
  page.on("dialog", async (dialog) => {
    report.dialogs.push(dialog.type());
    await dialog.dismiss();
  });
  page.on("request", (request) => {
    if (request.url().includes("__observatory_canary"))
      report.canary_requests.push(request.url());
  });
  const operationCanary = (item) => ({
    ...item,
    label: "OPERATION " + canary,
    verification: { status: "failed", message: "ERROR " + canary },
    events: [{ message: "EVENT " + canary }],
  });
  await page.route("**/api/observatory/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const name = url.pathname.split("/api/observatory/v1/")[1];
    if (mode === "hostile" && name === "workloads") {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        json: {
          ok: false,
          error: { code: "source_unavailable", message: "ERROR " + canary },
        },
      });
      return;
    }
    const response = await route.fetch();
    const payload = await response.json();
    const data = payload.data;
    if (data && mode === "hostile") {
      if (name === "fleet") {
        data.hosts.forEach((item) => (item.display_name = "HOST " + canary));
        data.serves.forEach((item) => (item.display_name = "SERVE " + canary));
      }
      if (name === "controls")
        data.settings?.forEach((item) => {
          item.label = "SETTING " + canary;
          item.help = "HELP " + canary;
        });
      if (name === "settings")
        data.integrations?.forEach((item) => {
          item.label = "INTEGRATION " + canary;
          item.reason = "ERROR " + canary;
        });
      if (name.startsWith("serves/")) {
        data.display_name = "SERVE " + canary;
        data.logs = { lines: ["LOG " + canary], truncated: false };
        data.evidence = [{ id: "evidence-a", label: "EVIDENCE " + canary }];
      }
      if (name === "operations") data.items = data.items.map(operationCanary);
      if (name.startsWith("operations/")) payload.data = operationCanary(data);
      if (name === "evidence")
        data.items.forEach((item) => {
          item.label = "EVIDENCE " + canary;
          item.limitations = ["LIMITATION " + canary];
        });
      if (name.startsWith("evidence/")) {
        data.label = "EVIDENCE " + canary;
        data.limitations = ["LIMITATION " + canary];
      }
    }
    if (data && mode === "unavailable" && name === "controls")
      data.actions?.forEach((item) => {
        item.supported = false;
        item.permitted = false;
        item.reason = reason;
      });
    if (data && mode === "invalid-workload" && name === "workloads")
      data.nodes[0].sources[0].records[0].label = canary;
    await route.fulfill({ response, json: payload });
  });
  const inspect = async (name, selector, expected) => {
    const node = page.locator(selector);
    await node
      .getByText(expected, { exact: true })
      .filter({ visible: true })
      .first()
      .waitFor();
    assert.ok((await node.textContent()).includes(expected));
    assert.equal(
      await page
        .locator('img[src*="__observatory_canary"],script:not([src]),[onerror]')
        .count(),
      0,
    );
    assert.equal(await page.evaluate(() => window.__xss || 0), 0);
    report.hostile_views.push({
      name,
      literal_text: true,
      executable_nodes: 0,
      script_execution: false,
    });
  };
  try {
    for (const [route, expected] of [
      ["overview", "HOST " + canary],
      ["workstations", "HOST " + canary],
      ["serves", "SERVE " + canary],
      ["workloads", "ERROR " + canary],
      ["configuration/serve-a", "SETTING " + canary],
      ["experiments/experiment-a", "EVIDENCE " + canary],
      ["operations", "OPERATION " + canary],
      ["settings", "INTEGRATION " + canary],
    ]) {
      await page.goto(fixture.url + "#/" + route);
      await inspect(route.split("/")[0], "main", expected);
      fs.writeFileSync(
        path.join(output, "hostile-" + route.split("/")[0] + ".aria.yml"),
        await page.locator("main").ariaSnapshot(),
      );
    }
    await page.goto(fixture.url + "#/serves/serve-a/logs");
    await inspect("serve logs", "main", "LOG " + canary);
    await page.goto(fixture.url + "#/serves/serve-a/evidence");
    await page
      .getByRole("button", { name: "EVIDENCE " + canary, exact: true })
      .click();
    await page.getByText("Evidence metadata", { exact: true }).click();
    assert.ok(
      (await page.getByRole("dialog").textContent()).includes(
        JSON.stringify(canary).slice(1, -1),
      ),
    );
    assert.equal(
      await page
        .locator('img[src*="__observatory_canary"],script:not([src]),[onerror]')
        .count(),
      0,
    );
    report.hostile_views.push({
      name: "evidence metadata dialog",
      literal_text: true,
      executable_nodes: 0,
      script_execution: false,
    });
    assert.equal(await page.evaluate(() => window.__xss || 0), 0);
    await page.keyboard.press("Escape");
    mode = "invalid-workload";
    await page.goto(fixture.url + "#/workloads");
    await page.getByText(/Invalid canonical workload evidence/).waitFor();
    report.canonical_hostile_label =
      "Rejected by canonical schema; no workload state rendered.";
    mode = "unavailable";
    await page.goto(fixture.url + "#/serves/serve-a");
    const disabled = page.getByRole("button", {
      name: "Stop serve",
      exact: true,
    });
    await disabled.waitFor();
    assert.equal(await disabled.isDisabled(), true);
    const description = await disabled.getAttribute("aria-describedby");
    assert.ok(description);
    assert.equal(
      await page.locator('[id="' + description + '"]').textContent(),
      reason,
    );
    const cdp = await context.newCDPSession(page);
    const tree = await cdp.send("Accessibility.getFullAXTree");
    const action = tree.nodes.find(
      (node) =>
        node.role?.value === "button" && node.name?.value === "Stop serve",
    );
    assert.equal(action.description?.value, reason);
    assert.ok(
      action.properties.some(
        (item) => item.name === "disabled" && item.value.value === true,
      ),
    );
    report.accessibility.unavailable_action = {
      name: action.name.value,
      description: action.description.value,
      disabled: true,
      linked_visible_text: true,
    };
    fs.writeFileSync(
      path.join(output, "unavailable-action.aria.yml"),
      await page.locator("main").ariaSnapshot(),
    );
    await page.screenshot({
      path: path.join(output, "unavailable-action.png"),
      fullPage: true,
    });
    mode = "normal";
    await page.goto(fixture.url + "#/configuration/serve-a");
    await page.locator("#setting-max_output_tokens").fill("42");
    await page.getByRole("button", { name: "Validate", exact: true }).click();
    await page
      .getByRole("button", { name: "Review impact", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Apply change", exact: true })
      .waitFor();
    const acceptedBefore = fixture.state.operations.length;
    fixture.state.authenticated = false;
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Apply change", exact: true })
      .click();
    await page.getByLabel("Username", { exact: true }).waitFor();
    assert.equal(await page.getByRole("dialog").count(), 0);
    assert.equal(fixture.state.operations.length, acceptedBefore);
    await page.getByLabel("Username", { exact: true }).fill("fixture-user");
    await page.getByLabel("Password", { exact: true }).fill("fixture-password");
    await page
      .locator("main")
      .getByRole("button", { name: "Sign in", exact: true })
      .click();
    await page.locator("#setting-max_output_tokens").waitFor();
    assert.equal(
      await page.locator("#setting-max_output_tokens").inputValue(),
      "32",
    );
    assert.equal(
      await page
        .getByRole("button", { name: "Review impact", exact: true })
        .isDisabled(),
      true,
    );
    const previews = fixture.state.posts.filter(
      (item) => item.route === "previews",
    ).length;
    await page.locator("#setting-max_output_tokens").fill("40");
    await page.getByRole("button", { name: "Validate", exact: true }).click();
    await page
      .getByRole("button", { name: "Review impact", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Apply change", exact: true })
      .waitFor();
    assert.equal(
      fixture.state.posts.filter((item) => item.route === "previews").length,
      previews + 1,
    );
    assert.equal(fixture.state.operations.length, acceptedBefore);
    report.session_expiry = {
      open_preview: true,
      expired_apply_accepted_operations: 0,
      modal_closed: true,
      private_draft_cleared: true,
      reauthentication_requires_validation_and_new_preview: true,
      new_preview_accepted_operations: 0,
    };
    await page.keyboard.press("Escape");
    assert.deepEqual(report.canary_requests, []);
    assert.deepEqual(report.dialogs, []);
    report.status = "passed";
    process.stdout.write(
      "PASS hostile strings across eight screens, logs/evidence and canonical rejection; unavailable AX description; expiry/re-auth/fresh-preview\n",
    );
  } catch (error) {
    report.status = "failed";
    report.error = error.stack;
    await page.screenshot({
      path: path.join(output, "failure.png"),
      fullPage: true,
    });
    throw error;
  } finally {
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(report, null, 2),
    );
    await browser.close();
    await fixture.close();
  }
})().catch((error) => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
