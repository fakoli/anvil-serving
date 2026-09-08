"use strict";
// Browser-observed live-region mutations against an isolated owner only.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const { createFixture } = require("./fixture_server.cjs");
const output = path.resolve(
  process.argv[2] || "test-results/observatory-announcements",
);
fs.mkdirSync(output, { recursive: true });
(async () => {
  const fixture = await createFixture();
  fixture.state.authenticated = true;
  const operation = {
    id: "operation-announcement",
    resource_id: "serve-a",
    host_id: "host-fixture-a",
    label: "Fixture configuration",
    status: "running",
    native_state: "running",
    execution_outcome: "pending",
    verification: {
      status: "pending",
      message: "Waiting for independent evidence.",
    },
    recovery: { status: "not_required" },
    events: [],
  };
  fixture.state.operations.push(operation);
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
    headless: true,
    args: ["--disable-gpu"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  const report = {
    fixture: true,
    observed_at: new Date().toISOString(),
    status: "running",
    limitations: [
      "This checks real Chromium live-region/AX semantics and mutation frequency; it does not claim speech output from a screen-reader pairing.",
    ],
  };
  try {
    await page.goto(fixture.url + "#/operations/operation-announcement");
    const status = page.locator("#operation-status-announcement");
    await status.getByText(/Operation running/).waitFor({ state: "attached" });
    await page.evaluate(() => {
      const node = document.getElementById("operation-status-announcement");
      window.__announcementNode = node;
      window.__announcements = [];
      new MutationObserver(() =>
        window.__announcements.push(node.textContent),
      ).observe(node, { subtree: true, childList: true, characterData: true });
    });
    assert.equal(await status.getAttribute("role"), null);
    assert.equal(await status.getAttribute("aria-live"), "polite");
    assert.equal(await status.getAttribute("aria-atomic"), "true");
    assert.equal(
      await page.locator(".dialog-body #operation-status-announcement").count(),
      0,
    );
    const before = fixture.state.reads.filter(
      (item) => item.route === "operations/operation-announcement",
    ).length;
    await page.waitForTimeout(4300);
    const repeated =
      fixture.state.reads.filter(
        (item) => item.route === "operations/operation-announcement",
      ).length - before;
    assert.ok(repeated >= 2);
    assert.deepEqual(await page.evaluate(() => window.__announcements), []);
    operation.native_state = "verifying";
    await page.waitForFunction(() => window.__announcements.length === 1);
    operation.updated_at = "2026-09-08T12:00:01Z";
    operation.verification.message =
      "New telemetry details, same verification state.";
    await page.waitForTimeout(2200);
    assert.equal(await page.evaluate(() => window.__announcements.length), 1);
    operation.verification.status = "failed";
    await page.waitForFunction(() => window.__announcements.length === 2);
    operation.recovery.status = "running";
    await page.waitForFunction(() => window.__announcements.length === 3);
    operation.recovery.status = "succeeded";
    operation.execution_outcome = "succeeded";
    operation.status = "failed";
    await page.waitForFunction(() => window.__announcements.length === 4);
    const announcements = await page.evaluate(() => window.__announcements);
    assert.ok(announcements[0].includes("Owner verifying"));
    assert.ok(announcements[1].includes("Verification failed"));
    assert.ok(announcements[2].includes("Recovery running"));
    assert.ok(
      announcements[3].includes("Operation verification failed") &&
        announcements[3].includes("Recovery succeeded"),
    );
    assert.equal(
      await page.evaluate(
        () =>
          document.getElementById("operation-status-announcement") ===
          window.__announcementNode,
      ),
      true,
    );
    const cdp = await page.context().newCDPSession(page);
    const tree = await cdp.send("Accessibility.getFullAXTree");
    const region = tree.nodes.find((node) =>
      node.properties?.some(
        (item) => item.name === "live" && item.value.value === "polite",
      ),
    );
    assert.ok(region);
    report.status = "passed";
    report.repeated_unchanged_polls = repeated;
    report.unchanged_poll_announcements = 0;
    report.timestamp_message_only_announcements = 0;
    report.state_change_announcements = announcements;
    report.persistent_region_outside_replaced_body = true;
    report.ax_live = "polite";
    fs.writeFileSync(
      path.join(output, "operation-announcements.aria.yml"),
      await page.getByRole("dialog").ariaSnapshot(),
    );
    process.stdout.write(
      "PASS persistent polite operation status: repeated polls quiet; phase/verification/recovery announced exactly once each\n",
    );
  } catch (error) {
    report.status = "failed";
    report.error = error.stack;
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
